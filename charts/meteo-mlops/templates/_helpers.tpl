{{- define "meteo.image" -}}
{{- $repository := required "image repository is required" .image.repository -}}
{{- $tag := required "immutable image tag is required" .image.tag -}}
{{- if or (eq $tag "latest") (not (regexMatch "^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$" $tag)) -}}
{{- fail "use an explicit immutable image tag, never latest" -}}
{{- end -}}
{{- printf "%s:%s" $repository $tag -}}
{{- end -}}
