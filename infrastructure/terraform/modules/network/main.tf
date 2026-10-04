variable "project_name" {type = string}
variable "environment" {type = string}
variable "vpc_cidr" {type = string}
variable "availability_zones" {type = list(string)}

resource "aws_vpc" "this" {
  cidr_block = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support = true
  tags = {Name = "${var.project_name}-${var.environment}"}
}
resource "aws_internet_gateway" "this" {vpc_id = aws_vpc.this.id}
resource "aws_subnet" "public" {
  count = length(var.availability_zones)
  vpc_id = aws_vpc.this.id
  cidr_block = cidrsubnet(var.vpc_cidr, 4, count.index)
  availability_zone = var.availability_zones[count.index]
  map_public_ip_on_launch = true
  tags = {Name = "${var.project_name}-public-${count.index}", "kubernetes.io/role/elb" = "1"}
}
resource "aws_subnet" "private" {
  count = length(var.availability_zones)
  vpc_id = aws_vpc.this.id
  cidr_block = cidrsubnet(var.vpc_cidr, 4, count.index + 8)
  availability_zone = var.availability_zones[count.index]
  tags = {Name = "${var.project_name}-private-${count.index}", "kubernetes.io/role/internal-elb" = "1"}
}
resource "aws_route_table" "public" {vpc_id = aws_vpc.this.id, route {cidr_block = "0.0.0.0/0", gateway_id = aws_internet_gateway.this.id}}
resource "aws_route_table_association" "public" {count = length(aws_subnet.public), subnet_id = aws_subnet.public[count.index].id, route_table_id = aws_route_table.public.id}
output "vpc_id" {value = aws_vpc.this.id}
output "public_subnet_ids" {value = aws_subnet.public[*].id}
output "private_subnet_ids" {value = aws_subnet.private[*].id}
