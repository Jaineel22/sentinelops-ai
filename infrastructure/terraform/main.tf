module "network" {
  source = "./modules/network"
  project_name = var.project_name
  environment = var.environment
  vpc_cidr = var.vpc_cidr
  availability_zones = ["${var.aws_region}a", "${var.aws_region}b"]
}

module "eks" {
  source = "./modules/eks"
  cluster_name = "${var.project_name}-${var.environment}"
  cluster_version = var.cluster_version
  vpc_id = module.network.vpc_id
  subnet_ids = module.network.private_subnet_ids
  node_instance_type = var.node_instance_type
  node_min_size = var.node_min_size
  node_max_size = var.node_max_size
}

module "rds" {
  source = "./modules/rds"
  project_name = var.project_name
  environment = var.environment
  vpc_id = module.network.vpc_id
  subnet_ids = module.network.private_subnet_ids
  db_name = var.db_name
  db_username = var.db_username
  db_password = var.db_password
  eks_security_group_id = module.eks.node_security_group_id
}

module "storage" {
  source = "./modules/storage"
  project_name = var.project_name
  environment = var.environment
  repositories = ["api", "orders-service", "anomaly-detector", "incident-correlator", "rca-agent", "remediation-controller", "frontend", "mlflow"]
}

module "iam" {
  source = "./modules/iam"
  project_name = var.project_name
  oidc_provider_arn = module.eks.oidc_provider_arn
  oidc_provider_url = module.eks.oidc_provider_url
  service_accounts = var.service_accounts
  artifact_bucket_arn = module.storage.bucket_arn
}
