// Day 18: the Azure deployment (ADR 0006).
//
//   Log Analytics + Container Apps environment
//   Container Registry (Basic)          images: mia (runtime target), mia-ui, redis
//   PostgreSQL Flexible Server (B1ms)   Entra-only auth (no password exists)
//   User-assigned identities            one per app + one that owns the DB role
//   Role assignments                    least privilege per app, on the existing
//                                       OpenAI, Search, Storage and Key Vault
//   Container apps (deployApps=true)    api, worker, news, ui, redis
//
// Two passes (infra/azure/deploy.sh): first without apps (images and DB roles
// don't exist yet), then with apps. Nothing here is a secret.

targetScope = 'resourceGroup'

@description('Suffix used by the existing resources, e.g. "dev-bb01".')
param suffix string = 'dev-bb01'
param location string = resourceGroup().location

@description('Existing resources from Day 1.')
param openAiAccountName string = 'aif-mia-${suffix}'
param searchServiceName string = 'srch-mia-${suffix}'
param storageAccountName string = 'stmiadevbb01'
param keyVaultName string = 'kv-mia-${suffix}'

@description('Model deployments on the OpenAI account.')
param chatDeployment string = 'chat-mini'
param embedDeployment string = 'text-embedding-3-small'
param openAiApiVersion string

@description('Entra ids for the API and UI (infra/entra/setup.sh). Not secrets.')
param authTenantId string = tenant().tenantId
param authApiClientId string
param authUiClientId string

@description('Postgres Entra admin: your user (object id and login name).')
param dbAdminObjectId string
param dbAdminName string

@description('Your public IP, for admin access to Postgres (psql, the DB setup script).')
param adminIp string

param deployApps bool = false
param imageTag string = 'latest'

var names = {
  logs: 'log-mia-${suffix}'
  acr: replace('acrmia${suffix}', '-', '')
  pg: 'psql-mia-${suffix}'
  env: 'cae-mia-${suffix}'
}
var appNames = {
  api: 'ca-mia-api'
  worker: 'ca-mia-worker'
  news: 'ca-mia-news'
  ui: 'ca-mia-ui'
  redis: 'ca-mia-redis'
}
var dbRole = 'id-mia-db'

// Built-in role ids.
var roles = {
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
  openAiUser: '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  searchIndexReader: '1407120a-92aa-4202-b7e9-c0e197c71c8f'
  searchServiceReader: 'acdd72a7-3385-48ef-bd42-f606fba81ae7'
  blobReader: '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
  blobContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  kvSecretsUser: '4633458b-17de-408a-b874-0445c86b69e6'
}

// ---------- existing (Day 1) ----------
resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: openAiAccountName
}
resource search 'Microsoft.Search/searchServices@2023-11-01' existing = {
  name: searchServiceName
}
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}
resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

// ---------- platform ----------
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: names.logs
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: names.acr
  location: location
  sku: { name: 'Basic' }
  properties: {
    adminUserEnabled: false // pulls use managed identities; no registry password
  }
}

// ---------- identities ----------
resource ids 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = [
  for n in ['api', 'worker', 'news', 'ui', 'db']: {
    name: 'id-mia-${n}'
    location: location
  }
]
// ids[0] api, ids[1] worker, ids[2] news, ids[3] ui, ids[4] db (referenced by
// index: Bicep needs identity ids computable at deployment start).

// Every app pulls its image from ACR with its own identity.
resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for i in range(0, 4): {
    scope: acr
    name: guid(acr.id, ids[i].id, roles.acrPull)
    properties: {
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.acrPull)
      principalId: ids[i].properties.principalId
      principalType: 'ServicePrincipal'
    }
  }
]

// worker: models, retrieval, the report archive, Langfuse keys.
resource workerOpenAi 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: openAi
  name: guid(openAi.id, ids[1].id, roles.openAiUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.openAiUser)
    principalId: ids[1].properties.principalId
    principalType: 'ServicePrincipal'
  }
}
resource workerSearch 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(search.id, ids[1].id, roles.searchIndexReader)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchIndexReader)
    principalId: ids[1].properties.principalId
    principalType: 'ServicePrincipal'
  }
}
resource workerBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, ids[1].id, roles.blobContributor)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobContributor)
    principalId: ids[1].properties.principalId
    principalType: 'ServicePrincipal'
  }
}
resource workerKv 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVault.id, ids[1].id, roles.kvSecretsUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.kvSecretsUser)
    principalId: ids[1].properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// api: company list and index stats (read), archived reports (read).
resource apiSearch 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(search.id, ids[0].id, roles.searchIndexReader)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.searchIndexReader)
    principalId: ids[0].properties.principalId
    principalType: 'ServicePrincipal'
  }
}
resource apiBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, ids[0].id, roles.blobReader)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobReader)
    principalId: ids[0].properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// news: the Tavily key only.
resource newsKv 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: keyVault
  name: guid(keyVault.id, ids[2].id, roles.kvSecretsUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.kvSecretsUser)
    principalId: ids[2].properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------- Postgres: Entra-only authentication ----------
resource pg 'Microsoft.DBforPostgreSQL/flexibleServers@2024-08-01' = {
  name: names.pg
  location: location
  sku: { name: 'Standard_B1ms', tier: 'Burstable' }
  properties: {
    version: '16'
    storage: { storageSizeGB: 32, autoGrow: 'Disabled' }
    backup: { backupRetentionDays: 7, geoRedundantBackup: 'Disabled' }
    highAvailability: { mode: 'Disabled' }
    network: { publicNetworkAccess: 'Enabled' }
    authConfig: {
      activeDirectoryAuth: 'Enabled'
      passwordAuth: 'Disabled' // keyless: tokens only, no password to leak
      tenantId: tenant().tenantId
    }
  }
}

resource pgDb 'Microsoft.DBforPostgreSQL/flexibleServers/databases@2024-08-01' = {
  parent: pg
  name: 'mia'
  properties: { charset: 'UTF8', collation: 'en_US.utf8' }
}

resource pgAdmin 'Microsoft.DBforPostgreSQL/flexibleServers/administrators@2024-08-01' = {
  parent: pg
  name: dbAdminObjectId
  properties: {
    principalName: dbAdminName
    principalType: 'User'
    tenantId: tenant().tenantId
  }
  dependsOn: [pgDb]
}

// Container Apps reach Postgres over its public endpoint ("Azure services");
// your IP for the one-off role setup. TLS is enforced by the server.
resource pgFromAzure 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: pg
  name: 'AllowAzureServices'
  properties: { startIpAddress: '0.0.0.0', endIpAddress: '0.0.0.0' }
  dependsOn: [pgAdmin]
}
resource pgFromAdmin 'Microsoft.DBforPostgreSQL/flexibleServers/firewallRules@2024-08-01' = {
  parent: pg
  name: 'Admin'
  properties: { startIpAddress: adminIp, endIpAddress: adminIp }
  dependsOn: [pgFromAzure]
}

// ---------- Container Apps environment ----------
resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: names.env
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

// ---------- apps ----------
var image = '${acr.properties.loginServer}/mia:${imageTag}'
var uiImage = '${acr.properties.loginServer}/mia-ui:${imageTag}'
var envDomain = env.properties.defaultDomain
var apiUrl = 'https://${appNames.api}.${envDomain}'
var uiUrl = 'https://${appNames.ui}.${envDomain}'

// Shared, non-secret settings for api, worker and news.
var commonEnv = [
  { name: 'ENVIRONMENT', value: 'production' }
  { name: 'DEV_AUTH_BYPASS', value: 'false' }
  { name: 'AZURE_TOKEN_CREDENTIALS', value: 'ManagedIdentityCredential' }
  { name: 'AZURE_OPENAI_ENDPOINT', value: openAi.properties.endpoint }
  { name: 'AZURE_OPENAI_API_VERSION', value: openAiApiVersion }
  { name: 'AZURE_OPENAI_CHAT_DEPLOYMENT', value: chatDeployment }
  { name: 'AZURE_OPENAI_EMBED_DEPLOYMENT', value: embedDeployment }
  { name: 'AZURE_SEARCH_ENDPOINT', value: 'https://${search.name}.search.windows.net' }
  { name: 'AZURE_KEYVAULT_URL', value: keyVault.properties.vaultUri }
  { name: 'AZURE_STORAGE_ACCOUNT_URL', value: storage.properties.primaryEndpoints.blob }
  { name: 'AUTH_TENANT_ID', value: authTenantId }
  { name: 'AUTH_API_CLIENT_ID', value: authApiClientId }
  { name: 'LANGGRAPH_STRICT_MSGPACK', value: 'true' }
  { name: 'RETRIEVAL_MODE', value: 'hybrid_semantic' }
  { name: 'APPROVAL_REQUIRED', value: 'true' }
  // Keyless Postgres: the id-mia-db identity's token is the password.
  { name: 'DATABASE_AUTH', value: 'entra' }
  { name: 'DATABASE_URL', value: 'postgresql+asyncpg://${dbRole}@${pg.properties.fullyQualifiedDomainName}:5432/mia?ssl=require' }
  { name: 'DATABASE_CLIENT_ID', value: ids[4].properties.clientId }
  { name: 'REDIS_URL', value: 'redis://${appNames.redis}:6379/0' }
  { name: 'NEWS_MCP_URL', value: 'http://${appNames.news}/mcp' }
]

resource redis 'Microsoft.App/containerApps@2024-03-01' = if (deployApps) {
  name: appNames.redis
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${ids[3].id}': {} } }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      registries: [{ server: acr.properties.loginServer, identity: ids[3].id }]
      // Internal TCP only: reachable from the other apps, never from outside.
      ingress: { external: false, transport: 'tcp', targetPort: 6379, exposedPort: 6379 }
    }
    template: {
      containers: [
        {
          name: 'redis'
          image: '${acr.properties.loginServer}/redis:7'
          args: ['redis-server', '--save', '', '--appendonly', 'no']
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

resource news 'Microsoft.App/containerApps@2024-03-01' = if (deployApps) {
  name: appNames.news
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${ids[2].id}': {} } }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      registries: [{ server: acr.properties.loginServer, identity: ids[2].id }]
      // Internal only (the worker); plain HTTP inside the environment.
      ingress: { external: false, targetPort: 8001, transport: 'http', allowInsecure: true }
    }
    template: {
      containers: [
        {
          name: 'news'
          image: image
          args: ['python', '-m', 'mcp_news.server']
          env: [
            { name: 'MIA_COMPONENT', value: 'news' }
            { name: 'AZURE_CLIENT_ID', value: ids[2].properties.clientId }
            { name: 'AZURE_TOKEN_CREDENTIALS', value: 'ManagedIdentityCredential' }
            { name: 'AZURE_KEYVAULT_URL', value: keyVault.properties.vaultUri }
            { name: 'NEWS_MCP_TRANSPORT', value: 'streamable-http' }
            { name: 'NEWS_MCP_HOST', value: '0.0.0.0' }
            { name: 'NEWS_MCP_PORT', value: '8001' }
            { name: 'NEWS_MCP_ALLOWED_HOSTS', value: '${appNames.news},${appNames.news}:*,${appNames.news}.internal.${envDomain},${appNames.news}.internal.${envDomain}:*' }
          ]
          resources: { cpu: json('0.25'), memory: '0.5Gi' }
          probes: [
            { type: 'Liveness', tcpSocket: { port: 8001 }, periodSeconds: 20 }
            { type: 'Readiness', tcpSocket: { port: 8001 }, periodSeconds: 10 }
          ]
        }
      ]
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
  dependsOn: [acrPull]
}

resource worker 'Microsoft.App/containerApps@2024-03-01' = if (deployApps) {
  name: appNames.worker
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${ids[1].id}': {}, '${ids[4].id}': {} }
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      registries: [{ server: acr.properties.loginServer, identity: ids[1].id }]
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: image
          args: ['arq', 'app.jobs.worker.WorkerSettings']
          env: concat(commonEnv, [
            { name: 'MIA_COMPONENT', value: 'worker' }
            { name: 'AZURE_CLIENT_ID', value: ids[1].properties.clientId }
          ])
          resources: { cpu: json('0.5'), memory: '1Gi' }
        }
      ]
      // Exactly one: crash recovery assumes a single worker (D-25). No
      // ingress, so no HTTP probes; Container Apps restarts it if it exits.
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
  dependsOn: [acrPull, workerOpenAi, workerSearch, workerBlob, workerKv, redis, news]
}

resource api 'Microsoft.App/containerApps@2024-03-01' = if (deployApps) {
  name: appNames.api
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${ids[0].id}': {}, '${ids[4].id}': {} }
  }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      registries: [{ server: acr.properties.loginServer, identity: ids[0].id }]
      ingress: { external: true, targetPort: 8000, transport: 'auto', allowInsecure: false }
    }
    template: {
      containers: [
        {
          name: 'api'
          image: image
          args: ['uvicorn', 'app.api.main:app', '--host', '0.0.0.0', '--port', '8000']
          env: concat(commonEnv, [
            { name: 'MIA_COMPONENT', value: 'api' }
            { name: 'AZURE_CLIENT_ID', value: ids[0].properties.clientId }
          ])
          resources: { cpu: json('0.5'), memory: '1Gi' }
          probes: [
            { type: 'Startup', httpGet: { path: '/livez', port: 8000 }, periodSeconds: 3, failureThreshold: 30 }
            { type: 'Liveness', httpGet: { path: '/livez', port: 8000 }, periodSeconds: 15 }
            { type: 'Readiness', httpGet: { path: '/health', port: 8000 }, periodSeconds: 10, timeoutSeconds: 5 }
          ]
        }
      ]
      scale: { minReplicas: 0, maxReplicas: 2 }
    }
  }
  dependsOn: [acrPull, apiSearch, apiBlob, redis]
}

resource ui 'Microsoft.App/containerApps@2024-03-01' = if (deployApps) {
  name: appNames.ui
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${ids[3].id}': {} } }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      registries: [{ server: acr.properties.loginServer, identity: ids[3].id }]
      ingress: { external: true, targetPort: 8501, transport: 'auto', allowInsecure: false }
    }
    template: {
      containers: [
        {
          name: 'ui'
          image: uiImage
          env: [
            { name: 'API_BASE_URL', value: apiUrl }
            { name: 'AUTH_TENANT_ID', value: authTenantId }
            { name: 'AUTH_API_CLIENT_ID', value: authApiClientId }
            { name: 'AUTH_UI_CLIENT_ID', value: authUiClientId }
            { name: 'AUTH_REDIRECT_URI', value: uiUrl }
          ]
          resources: { cpu: json('0.5'), memory: '1Gi' }
          probes: [
            { type: 'Readiness', httpGet: { path: '/_stcore/health', port: 8501 }, periodSeconds: 10 }
          ]
        }
      ]
      // One replica: a sign-in in progress lives in the UI process.
      scale: { minReplicas: 0, maxReplicas: 1 }
    }
  }
  dependsOn: [acrPull]
}

output acrLoginServer string = acr.properties.loginServer
output acrName string = acr.name
output pgHost string = pg.properties.fullyQualifiedDomainName
output pgServerName string = pg.name
output dbRoleName string = dbRole
output dbIdentityPrincipalId string = ids[4].properties.principalId
output envDomain string = envDomain
output apiUrl string = apiUrl
output uiUrl string = uiUrl
