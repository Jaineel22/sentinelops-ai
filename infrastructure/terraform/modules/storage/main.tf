variable "project_name" {type = string}
variable "environment" {type = string}
variable "repositories" {type = list(string)}
resource "random_id" "suffix" {byte_length = 4}
resource "aws_s3_bucket" "this" {bucket = "${var.project_name}-${var.environment}-mlflow-${random_id.suffix.hex}"}
resource "aws_s3_bucket_public_access_block" "this" {bucket = aws_s3_bucket.this.id, block_public_acls = true, block_public_policy = true, ignore_public_acls = true, restrict_public_buckets = true}
resource "aws_ecr_repository" "this" {for_each = toset(var.repositories), name = "${var.project_name}/${each.key}", image_scanning_configuration {scan_on_push = true}, image_tag_mutability = "IMMUTABLE"}
output "bucket_arn" {value = aws_s3_bucket.this.arn}
output "bucket_name" {value = aws_s3_bucket.this.bucket}
output "repository_urls" {value = {for name, repo in aws_ecr_repository.this : name => repo.repository_url}}
