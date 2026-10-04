# Bootstrap this bucket/table once, then uncomment and configure this backend.
# terraform {
#   backend "s3" {
#     bucket         = "sentinelops-terraform-state"
#     key            = "sentinelops/terraform.tfstate"
#     region         = "us-east-1"
#     dynamodb_table = "sentinelops-terraform-locks"
#     encrypt        = true
#   }
# }
