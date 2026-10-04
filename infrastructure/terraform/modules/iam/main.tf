variable "project_name" {type = string}
variable "oidc_provider_arn" {type = string}
variable "oidc_provider_url" {type = string}
variable "service_accounts" {type = set(string)}
variable "artifact_bucket_arn" {type = string}
data "aws_iam_policy_document" "assume" {
  for_each = var.service_accounts
  statement {actions = ["sts:AssumeRoleWithWebIdentity"], principals {type = "Federated", identifiers = [var.oidc_provider_arn]}, condition {test = "StringEquals", variable = "${var.oidc_provider_url}:sub", values = ["system:serviceaccount:sentinelops:${each.key}"]}, condition {test = "StringEquals", variable = "${var.oidc_provider_url}:aud", values = ["sts.amazonaws.com"]}}
}
resource "aws_iam_role" "this" {for_each = var.service_accounts, name = "${var.project_name}-${each.key}", assume_role_policy = data.aws_iam_policy_document.assume[each.key].json}
resource "aws_iam_role_policy" "s3" {for_each = var.service_accounts, role = aws_iam_role.this[each.key].id, policy = jsonencode({Version = "2012-10-17", Statement = [{Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket"], Resource = [var.artifact_bucket_arn, "${var.artifact_bucket_arn}/*"]}]})}
output "role_arns" {value = {for name, role in aws_iam_role.this : name => role.arn}}
