# Kubernetes deployment

## Local Kind

Install Docker, `kind`, `kubectl`, and Helm, then run:

```bash
bash scripts/helm-install.sh
helm lint infrastructure/helm/sentinelops
kubectl get pods -n sentinelops
```

The development values use local images and mock RCA. The raw manifests under
`infrastructure/kubernetes` remain useful for inspection and bootstrap, while
the Helm chart is the repeatable deployment interface.

## Production safeguards

Use an external secret provider instead of committing `values-prod.yaml`
secrets. Configure an ingress controller and TLS secret, provide a storage
class, and run database migrations as a reviewed pre-deployment Job. Health
probes are intentionally public; application APIs require JWT authentication.
