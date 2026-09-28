#!/usr/bin/env bash
# Entra ID setup for the research API and its Streamlit client (Day 15).
#
#   infra/entra/setup.sh <analyst-object-id> <approver-object-id>
#
# Creates, or brings up to date (safe to re-run):
#   mia-api  app registration + service principal
#            - Expose an API: api://<appId>, delegated scope access_as_user
#            - App roles: analyst, approver (assigned to users)
#            - v2 access tokens; only assigned users can get a token
#   mia-ui   public client (device code flow) with admin consent for
#            mia-api/access_as_user
#   Role assignments: <analyst-object-id> -> analyst,
#                     <approver-object-id> -> approver
#
# No client secrets: the API only validates tokens and the UI is a public
# client. Prints the three non-secret values for .env. Needs `az login` as a
# user who can create app registrations and grant admin consent.
set -euo pipefail

API_NAME="${API_NAME:-mia-api}"
UI_NAME="${UI_NAME:-mia-ui}"
# Fixed ids, so re-running updates the same scope and roles instead of adding.
SCOPE_ID="da870bca-c69c-49e4-a7dc-b95a7d1d058d"
ANALYST_ROLE_ID="d83ca84c-21b3-40b6-a0c8-8dd492819c36"
APPROVER_ROLE_ID="8692ae1d-1d37-4cf6-8a24-945fcf6fcebb"
GRAPH="https://graph.microsoft.com/v1.0"

ANALYST_OID="${1:?usage: $0 <analyst-object-id> <approver-object-id>}"
APPROVER_OID="${2:?usage: $0 <analyst-object-id> <approver-object-id>}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

TENANT_ID="$(az account show --query tenantId -o tsv)"

app_id_by_name() {
  az ad app list --display-name "$1" --query "[0].appId" -o tsv
}

ensure_sp() {  # service principal for an app id; prints its object id
  local sp
  sp="$(az ad sp list --filter "appId eq '$1'" --query "[0].id" -o tsv)"
  if [[ -z "$sp" ]]; then
    sp="$(az ad sp create --id "$1" --query id -o tsv)"
  fi
  echo "$sp"
}

# ---------- API app: scope, roles, v2 tokens ----------
API_APP_ID="$(app_id_by_name "$API_NAME")"
if [[ -z "$API_APP_ID" ]]; then
  API_APP_ID="$(az ad app create --display-name "$API_NAME" \
    --sign-in-audience AzureADMyOrg --query appId -o tsv)"
  echo "created app $API_NAME ($API_APP_ID)"
fi
API_OBJ_ID="$(az ad app show --id "$API_APP_ID" --query id -o tsv)"

cat > "$TMP/api.json" <<JSON
{
  "identifierUris": ["api://$API_APP_ID"],
  "api": {
    "requestedAccessTokenVersion": 2,
    "oauth2PermissionScopes": [{
      "id": "$SCOPE_ID",
      "value": "access_as_user",
      "type": "User",
      "isEnabled": true,
      "adminConsentDisplayName": "Use the research API as the signed-in user",
      "adminConsentDescription": "Submit research, view jobs and decide approvals, as allowed by the user's app role.",
      "userConsentDisplayName": "Use the research API as you",
      "userConsentDescription": "Submit research, view jobs and decide approvals, as allowed by your role."
    }]
  },
  "appRoles": [
    {
      "id": "$ANALYST_ROLE_ID",
      "value": "analyst",
      "displayName": "Analyst",
      "description": "Submit research and view own jobs.",
      "allowedMemberTypes": ["User"],
      "isEnabled": true
    },
    {
      "id": "$APPROVER_ROLE_ID",
      "value": "approver",
      "displayName": "Approver",
      "description": "Everything an analyst can do, plus view all jobs and approve or reject plans (never their own).",
      "allowedMemberTypes": ["User"],
      "isEnabled": true
    }
  ]
}
JSON
az rest --method PATCH --uri "$GRAPH/applications/$API_OBJ_ID" \
  --headers Content-Type=application/json --body "@$TMP/api.json" >/dev/null
API_SP_ID="$(ensure_sp "$API_APP_ID")"
# Only users with a role assignment can get a token for the API at all.
az ad sp update --id "$API_SP_ID" --set appRoleAssignmentRequired=true
echo "configured $API_NAME: scope access_as_user, roles analyst/approver"

# ---------- UI app: public client with delegated access to the API ----------
UI_APP_ID="$(app_id_by_name "$UI_NAME")"
if [[ -z "$UI_APP_ID" ]]; then
  UI_APP_ID="$(az ad app create --display-name "$UI_NAME" \
    --sign-in-audience AzureADMyOrg --is-fallback-public-client true \
    --query appId -o tsv)"
  echo "created app $UI_NAME ($UI_APP_ID)"
fi
az ad app update --id "$UI_APP_ID" --is-fallback-public-client true
cat > "$TMP/ui-access.json" <<JSON
[{"resourceAppId": "$API_APP_ID",
  "resourceAccess": [{"id": "$SCOPE_ID", "type": "Scope"}]}]
JSON
az ad app update --id "$UI_APP_ID" --required-resource-accesses "@$TMP/ui-access.json"
UI_SP_ID="$(ensure_sp "$UI_APP_ID")"

# Admin consent (tenant-wide) for mia-ui -> mia-api/access_as_user.
GRANT="$(az rest --method GET \
  --uri "$GRAPH/oauth2PermissionGrants?\$filter=clientId eq '$UI_SP_ID' and resourceId eq '$API_SP_ID'" \
  --query "value[0].id" -o tsv)"
if [[ -z "$GRANT" ]]; then
  cat > "$TMP/grant.json" <<JSON
{"clientId": "$UI_SP_ID", "consentType": "AllPrincipals",
 "resourceId": "$API_SP_ID", "scope": "access_as_user"}
JSON
  az rest --method POST --uri "$GRAPH/oauth2PermissionGrants" \
    --headers Content-Type=application/json --body "@$TMP/grant.json" >/dev/null
fi
echo "configured $UI_NAME: public client, admin consent for access_as_user"

# ---------- role assignments ----------
assign() {  # <user object id> <role id> <label>
  local existing
  existing="$(az rest --method GET \
    --uri "$GRAPH/servicePrincipals/$API_SP_ID/appRoleAssignedTo" \
    --query "value[?principalId=='$1' && appRoleId=='$2'] | [0].id" -o tsv)"
  if [[ -z "$existing" ]]; then
    cat > "$TMP/assign.json" <<JSON
{"principalId": "$1", "resourceId": "$API_SP_ID", "appRoleId": "$2"}
JSON
    az rest --method POST --uri "$GRAPH/servicePrincipals/$API_SP_ID/appRoleAssignedTo" \
      --headers Content-Type=application/json --body "@$TMP/assign.json" >/dev/null
  fi
  echo "assigned $3 to $1"
}
assign "$ANALYST_OID" "$ANALYST_ROLE_ID" analyst
assign "$APPROVER_OID" "$APPROVER_ROLE_ID" approver

cat <<ENV

Add to .env (not secrets):
AUTH_TENANT_ID=$TENANT_ID
AUTH_API_CLIENT_ID=$API_APP_ID
AUTH_UI_CLIENT_ID=$UI_APP_ID
ENV
