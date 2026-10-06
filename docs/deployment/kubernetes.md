# Kubernetes deployment

## Local Kind

Install Docker, `kind`, `kubectl`, and Helm, then run:

```bash
bash scripts/helm-install.sh
helm lint infrastructure/helm/sentinelops
kubectl get pods -n sentinelops
```

The script builds the application images, loads them into Kind, and installs
the chart with `values-dev.yaml`. The development values use local images and
mock RCA. The raw manifests under `infrastructure/kubernetes` remain useful
for inspection; the Helm chart is the repeatable deployment interface.

The local script does not create an ingress controller. Use port-forwarding
or install an ingress controller separately when testing ingress behavior:

```bash
kubectl -n sentinelops port-forward service/frontend 3100:3000
```

## Production safeguards

Use an external secret provider instead of committing secrets. When
`secrets.create=false`, create a Secret named `sentinelops-secrets` with
`JWT_SECRET_KEY`, `DB_USERNAME`, `DB_PASSWORD`, and (optionally) `LLM_API_KEY`.
Configure an ingress controller and TLS secret, provide a storage class, and
run database migrations as a reviewed pre-deployment Job. Health probes are
intentionally public; application APIs require JWT authentication.
