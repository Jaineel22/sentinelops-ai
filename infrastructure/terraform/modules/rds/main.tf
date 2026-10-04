variable "project_name" {type = string}
variable "environment" {type = string}
variable "vpc_id" {type = string}
variable "subnet_ids" {type = list(string)}
variable "db_name" {type = string}
variable "db_username" {type = string}
variable "db_password" {type = string, sensitive = true}
variable "eks_security_group_id" {type = string}
resource "aws_db_subnet_group" "this" {name = "${var.project_name}-${var.environment}", subnet_ids = var.subnet_ids}
resource "aws_security_group" "this" {name = "${var.project_name}-${var.environment}-rds", vpc_id = var.vpc_id, ingress {from_port = 5432, to_port = 5432, protocol = "tcp", security_groups = [var.eks_security_group_id]}}
resource "aws_db_instance" "this" {identifier = "${var.project_name}-${var.environment}", engine = "postgres", engine_version = "17", instance_class = "db.t3.micro", allocated_storage = 20, storage_type = "gp3", db_name = var.db_name, username = var.db_username, password = var.db_password, db_subnet_group_name = aws_db_subnet_group.this.name, vpc_security_group_ids = [aws_security_group.this.id], publicly_accessible = false, multi_az = false, backup_retention_period = 7, skip_final_snapshot = true}
output "db_endpoint" {value = aws_db_instance.this.endpoint}
output "db_name" {value = var.db_name}
