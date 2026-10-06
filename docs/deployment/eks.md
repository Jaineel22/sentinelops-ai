# EKS deployment

Create the AWS resources with the Terraform root module, configure AWS
credentials through GitHub OIDC or a short-lived operator session, then deploy:

```bash
aws eks update-kubeconfig --name <cluster> --region <region>
helm upgrade --install sentinelops infrastructure/helm/sentinelops \
  -n sentinelops --create-namespace \
  -f infrastructure/helm/sentinelops/values-prod.yaml --wait
```

Before installing, replace the placeholder ECR repositories in
`values-prod.yaml` and provide repositories/tags for every application image.
Create the external `sentinelops-secrets` Secret, install an ingress
controller, and confirm the cluster has an egress path for ECR/image pulls.
The default Terraform profile intentionally omits NAT gateways for cost
control, so private-node egress must be designed before production use.

The deploy workflow uses a protected GitHub environment and
`AWS_DEPLOY_ROLE_ARN`; long-lived AWS keys must not be stored in repository
secrets. Review node egress, ingress TLS, RDS security groups, and pod
resource requests before production rollout.
