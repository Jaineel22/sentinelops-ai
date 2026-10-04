#!/usr/bin/env bash
set -euo pipefail

command -v kind >/dev/null || { echo "kind is required" >&2; exit 1; }
command -v helm >/dev/null || { echo "helm is required" >&2; exit 1; }
command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }

cluster="${KIND_CLUSTER_NAME:-sentinelops}"
if ! kind get clusters | grep -qx "$cluster"; then
  kind create cluster --config infrastructure/kubernetes/kind-config.yaml
fi

images=(api orders-service anomaly-detector incident-correlator rca-agent remediation-controller frontend)
docker build -t "sentinelops-ai/api:latest" .
docker build -f apps/orders-service/Dockerfile -t "sentinelops-ai/orders-service:latest" .
docker build -f services/anomaly-detector/Dockerfile -t "sentinelops-ai/anomaly-detector:latest" .
docker build -f services/incident-correlator/Dockerfile -t "sentinelops-ai/incident-correlator:latest" .
docker build -f services/rca-agent/Dockerfile -t "sentinelops-ai/rca-agent:latest" .
docker build -f services/remediation-controller/Dockerfile -t "sentinelops-ai/remediation-controller:latest" .
docker build -f apps/frontend/Dockerfile -t "sentinelops-ai/frontend:latest" .
for image in "${images[@]}"; do kind load docker-image "sentinelops-ai/$image:latest" --name "$cluster"; done

helm upgrade --install sentinelops infrastructure/helm/sentinelops \
  --namespace sentinelops --create-namespace \
  --values infrastructure/helm/sentinelops/values-dev.yaml \
  --wait --timeout 5m
kubectl -n sentinelops rollout status statefulset/kafka --timeout=180s
kubectl -n sentinelops rollout status statefulset/postgres --timeout=180s
for deployment in api orders-service anomaly-detector incident-correlator rca-agent remediation-controller frontend; do
  kubectl -n sentinelops rollout status "deployment/$deployment" --timeout=180s
done
