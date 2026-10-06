# Terraform infrastructure

This root module provisions a small EKS deployment: a two-AZ VPC, EKS managed
node group, private PostgreSQL RDS, MLflow S3 artifacts, ECR repositories, and
IRSA roles. It intentionally skips NAT gateways by default to control cost;
private nodes therefore need an approved egress strategy before pulling from
public registries.

The module currently creates public and private subnets but only wires public
routes. It does not create NAT gateways or private routes. Treat the default
profile as a cost-conscious foundation and add an approved egress design before
using private EKS nodes for a production rollout.

```bash
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform fmt -check -recursive
terraform validate
terraform plan
terraform apply
```

The state backend is commented until its S3 bucket and DynamoDB lock table are
bootstrapped. Never commit `terraform.tfvars` or credentials.
