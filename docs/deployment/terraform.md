# Terraform deployment

See [`infrastructure/terraform/README.md`](../../infrastructure/terraform/README.md).
Copy the example variables, provide the database password through a secure
CI variable or secret manager, and run `terraform fmt`, `init`, `validate`,
and `plan` before apply. State should use the documented encrypted S3 backend
with DynamoDB locking after bootstrap.
