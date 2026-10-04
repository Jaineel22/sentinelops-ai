variable "cluster_name" {type = string}
variable "cluster_version" {type = string}
variable "vpc_id" {type = string}
variable "subnet_ids" {type = list(string)}
variable "node_instance_type" {type = string}
variable "node_min_size" {type = number}
variable "node_max_size" {type = number}
data "aws_iam_policy_document" "assume" {statement {actions = ["sts:AssumeRole"], principals {type = "Service", identifiers = ["eks.amazonaws.com"]}}}
resource "aws_iam_role" "cluster" {name = "${var.cluster_name}-cluster", assume_role_policy = data.aws_iam_policy_document.assume.json}
resource "aws_iam_role_policy_attachment" "cluster" {role = aws_iam_role.cluster.name, policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"}
resource "aws_eks_cluster" "this" {name = var.cluster_name, version = var.cluster_version, role_arn = aws_iam_role.cluster.arn, vpc_config {subnet_ids = var.subnet_ids}, depends_on = [aws_iam_role_policy_attachment.cluster]}
data "aws_iam_policy_document" "nodes" {statement {actions = ["sts:AssumeRole"], principals {type = "Service", identifiers = ["ec2.amazonaws.com"]}}}
resource "aws_iam_role" "nodes" {name = "${var.cluster_name}-nodes", assume_role_policy = data.aws_iam_policy_document.nodes.json}
resource "aws_iam_role_policy_attachment" "node" {for_each = toset(["AmazonEKSWorkerNodePolicy", "AmazonEKS_CNI_Policy", "AmazonEC2ContainerRegistryReadOnly"]), role = aws_iam_role.nodes.name, policy_arn = "arn:aws:iam::aws:policy/${each.key}"}
resource "aws_eks_node_group" "this" {cluster_name = aws_eks_cluster.this.name, node_group_name = "default", node_role_arn = aws_iam_role.nodes.arn, subnet_ids = var.subnet_ids, instance_types = [var.node_instance_type], scaling_config {desired_size = var.node_min_size, min_size = var.node_min_size, max_size = var.node_max_size}, depends_on = [aws_iam_role_policy_attachment.node]}
data "tls_certificate" "oidc" {url = aws_eks_cluster.this.identity[0].oidc[0].issuer}
resource "aws_iam_openid_connect_provider" "this" {url = data.tls_certificate.oidc.url, client_id_list = ["sts.amazonaws.com"], thumbprint_list = [data.tls_certificate.oidc.certificates[0].sha1_fingerprint]}
output "cluster_name" {value = aws_eks_cluster.this.name}
output "cluster_endpoint" {value = aws_eks_cluster.this.endpoint}
output "cluster_certificate_authority_data" {value = aws_eks_cluster.this.certificate_authority[0].data}
output "node_security_group_id" {value = aws_eks_cluster.this.vpc_config[0].cluster_security_group_id}
output "oidc_provider_arn" {value = aws_iam_openid_connect_provider.this.arn}
output "oidc_provider_url" {value = replace(aws_iam_openid_connect_provider.this.url, "https://", "")}
