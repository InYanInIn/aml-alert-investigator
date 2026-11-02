{{- define "aml.labels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}

{{- define "aml.securityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
{{- end }}

{{- define "aml.probes" -}}
startupProbe:
  httpGet: {path: /healthz, port: http}
  periodSeconds: 2
  failureThreshold: 30
livenessProbe:
  httpGet: {path: /healthz, port: http}
  periodSeconds: 10
readinessProbe:
  httpGet: {path: /readyz, port: http}
  periodSeconds: 5
  timeoutSeconds: 3
{{- end }}
