# EKS deployment

Create the AWS resources with the Terraform root module, configure AWS
credentials through GitHub OIDC or a short-lived operator session, then deploy:

```bash
aws eks update-kubeconfig --name <cluster> --region <region>
helm upgrade --install sentinelops infrastructure/helm/sentinelops \
  -n sentinelops --create-namespace \
  -f infrastructure/helm/sentinelops/values-prod.yaml --wait
```

The deploy workflow uses a protected GitHub environment and
`AWS_DEPLOY_ROLE_ARN`; long-lived AWS keys must not be stored in repository
secrets. Review node egress, ingress TLS, RDS security groups, and pod
resource requests before production rollout.
