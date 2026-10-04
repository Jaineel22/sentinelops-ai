output "cluster_name" {value = module.eks.cluster_name}
output "cluster_endpoint" {value = module.eks.cluster_endpoint}
output "db_endpoint" {value = module.rds.db_endpoint}
output "ecr_urls" {value = module.storage.repository_urls}
output "s3_bucket" {value = module.storage.bucket_name}
