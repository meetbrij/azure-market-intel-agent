{{- define "mia.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "mia.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/* Selector labels for one component: include "mia.selector" (list . "api") */}}
{{- define "mia.selector" -}}
{{- $ := index . 0 -}}
app.kubernetes.io/name: {{ $.Chart.Name }}
app.kubernetes.io/instance: {{ $.Release.Name }}
app.kubernetes.io/component: {{ index . 1 }}
{{- end -}}

{{/* Pod-level hardening for our own images (uid 10001 in the Dockerfile). */}}
{{- define "mia.podSecurity" -}}
automountServiceAccountToken: false
securityContext:
  runAsNonRoot: true
  runAsUser: 10001
  runAsGroup: 10001
  fsGroup: 10001
  seccompProfile:
    type: RuntimeDefault
{{- end -}}

{{- define "mia.containerSecurity" -}}
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop: ["ALL"]
{{- end -}}

{{/* Environment shared by api, worker and news. Sources later in envFrom win,
     and explicit env wins over envFrom: the chart's settings beat a .env. */}}
{{- define "mia.env" -}}
{{- $ := index . 0 -}}
{{- $component := index . 1 -}}
{{- $name := include "mia.fullname" $ -}}
envFrom:
{{- range $.Values.extraEnvFromConfigMaps }}
  - configMapRef:
      name: {{ . }}
{{- end }}
  - configMapRef:
      name: {{ $name }}-config
env:
  - name: MIA_COMPONENT
    value: {{ $component }}
{{- if $.Values.postgres.enabled }}
  - name: POSTGRES_PASSWORD
    valueFrom:
      secretKeyRef:
        name: {{ $name }}-postgres
        key: POSTGRES_PASSWORD
  - name: DATABASE_URL
    value: postgresql+asyncpg://mia:$(POSTGRES_PASSWORD)@{{ $name }}-postgres:5432/mia
{{- else if $.Values.postgres.externalUrlSecret }}
  - name: DATABASE_URL
    valueFrom:
      secretKeyRef:
        name: {{ $.Values.postgres.externalUrlSecret }}
        key: {{ $.Values.postgres.externalUrlSecretKey }}
{{- end }}
{{- if $.Values.redis.enabled }}
  - name: REDIS_URL
    value: redis://{{ $name }}-redis:6379/0
{{- else if $.Values.redis.externalUrl }}
  - name: REDIS_URL
    value: {{ $.Values.redis.externalUrl | quote }}
{{- end }}
{{- if $.Values.news.enabled }}
  - name: NEWS_MCP_URL
    value: http://{{ $name }}-news:{{ $.Values.news.port }}/mcp
{{- else }}
  - name: NEWS_ENABLED
    value: "false"
{{- end }}
{{- end -}}

{{/* Writable scratch space on a read-only root filesystem, plus the host's
     Azure CLI login when enabled (the local image's entrypoint copies it
     into $HOME/.azure). */}}
{{- define "mia.volumeMounts" -}}
volumeMounts:
  - name: tmp
    mountPath: /tmp
  - name: home
    mountPath: /home/app
{{- if .Values.azureCliLogin.enabled }}
  - name: host-azure
    mountPath: /mnt/host-azure
    readOnly: true
{{- end }}
{{- end -}}

{{- define "mia.volumes" -}}
volumes:
  - name: tmp
    emptyDir: {}
  - name: home
    emptyDir: {}
{{- if .Values.azureCliLogin.enabled }}
  - name: host-azure
    hostPath:
      path: {{ .Values.azureCliLogin.nodePath }}
      type: Directory
{{- end }}
{{- end -}}
