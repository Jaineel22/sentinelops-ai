# Cost expectations

This is a planning guide, not a billing quote. A single `t3.medium` EKS node,
the EKS control-plane fee, a `db.t3.micro` RDS instance, 20 GB gp3 storage, S3,
ECR, and normal data transfer are the minimum viable production-conscious
baseline. NAT gateways are intentionally omitted because one gateway can cost
more than the small compute footprint.

Use AWS Cost Explorer and set budgets before applying. Stop or destroy the
environment when it is not being used; `terraform destroy` removes the
resources managed by this root module.
