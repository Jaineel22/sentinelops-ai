# Terraform infrastructure

This root module provisions a small EKS deployment: a two-AZ VPC, EKS managed
node group, private PostgreSQL RDS, MLflow S3 artifacts, ECR repositories, and
IRSA roles. It intentionally skips NAT gateways by default to control cost;
private nodes therefore need an approved egress strategy before pulling from
public registries.

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
