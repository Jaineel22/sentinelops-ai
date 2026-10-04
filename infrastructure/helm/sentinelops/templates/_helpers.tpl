{{- define "sentinelops.name" -}}sentinelops{{- end -}}
{{- define "sentinelops.namespace" -}}{{ .Values.namespace }}{{- end -}}
{{- define "sentinelops.labels" -}}
app.kubernetes.io/part-of: sentinelops
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
