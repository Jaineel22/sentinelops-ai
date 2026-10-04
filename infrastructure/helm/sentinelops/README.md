# SentinelOps Helm chart

The chart packages the same container images used by Docker Compose for Kind
and EKS. `values-dev.yaml` is deliberately small and local-image friendly;
`values-prod.yaml` enables TLS and higher availability but expects secrets to
come from External Secrets or Sealed Secrets.

```bash
helm lint infrastructure/helm/sentinelops
helm upgrade --install sentinelops infrastructure/helm/sentinelops \
  -n sentinelops --create-namespace -f infrastructure/helm/sentinelops/values-dev.yaml
```

The chart creates the stateless deployments, Kafka/Postgres stateful services,
services, ingress, HPAs, and retraining CronJob. Database migrations remain an
explicit release operation and should run as a pre-deployment Job in a
production pipeline.
