# Deployment costs

The default Terraform profile is intentionally cost-conscious: one to three
`t3.medium` EKS nodes, a single `db.t3.micro` PostgreSQL instance, gp3
storage, and no NAT gateways. AWS pricing varies by region and traffic, so use
Cost Explorer and budgets for the actual account. Destroy idle environments
and enable multi-AZ, NAT, larger nodes, and backups only when the availability
requirement justifies the cost.
