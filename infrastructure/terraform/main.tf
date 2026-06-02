# ============================================================================
#  PharmPilot AI — AWS Production Infrastructure
#  HIPAA-eligible architecture with BAA-covered services only
#  Terraform 1.6+ | AWS Provider ~> 5.0
# ============================================================================

terraform {
  required_version = ">= 1.6"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
  }
  backend "s3" {
    bucket         = "pharmpilot-terraform-state"
    key            = "pharmpilot/production/terraform.tfstate"
    region         = "us-east-1"
    encrypt        = true
    kms_key_id     = "alias/pharmpilot-terraform"
    dynamodb_table = "pharmpilot-terraform-locks"
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = {
      Project     = "PharmPilot"
      Environment = var.environment
      ManagedBy   = "Terraform"
      HIPAA       = "true"
    }
  }
}

# ── VPC ──────────────────────────────────────────────────────────────────
module "vpc" {
  source = "./modules/vpc"

  environment         = var.environment
  vpc_cidr            = "10.0.0.0/16"
  availability_zones  = ["${var.aws_region}a", "${var.aws_region}b", "${var.aws_region}c"]
  private_subnet_cidrs = ["10.0.1.0/24", "10.0.2.0/24", "10.0.3.0/24"]
  public_subnet_cidrs  = ["10.0.101.0/24", "10.0.102.0/24", "10.0.103.0/24"]
  # PHI data flows only through private subnets
  # No direct internet access to databases or application servers
}

# ── KMS Customer Managed Keys ─────────────────────────────────────────────
module "kms" {
  source = "./modules/kms"

  environment       = var.environment
  enable_rotation   = true
  rotation_period   = 365   # Annual rotation per HIPAA best practice
  key_aliases = {
    phi_data      = "pharmpilot-phi"       # PHI at rest (RDS, S3, EBS)
    vault_master  = "pharmpilot-vault"     # Biometric evidence vault
    secrets       = "pharmpilot-secrets"   # Secrets Manager
    logs          = "pharmpilot-logs"      # CloudWatch log encryption
  }
}

# ── RDS PostgreSQL (Multi-AZ, HIPAA-eligible) ─────────────────────────────
module "rds" {
  source = "./modules/rds"

  environment          = var.environment
  vpc_id               = module.vpc.vpc_id
  private_subnet_ids   = module.vpc.private_subnet_ids
  kms_key_arn          = module.kms.phi_key_arn
  instance_class       = var.environment == "production" ? "db.r7g.xlarge" : "db.t4g.medium"
  multi_az             = var.environment == "production"
  deletion_protection  = var.environment == "production"

  db_name     = "pharmpilot"
  db_username = "pharmpilot"

  # Enable enhanced monitoring, Performance Insights
  monitoring_interval  = 30
  performance_insights = true
  backup_retention_days = var.environment == "production" ? 35 : 7

  # PostgreSQL extensions needed
  parameter_group_family = "postgres16"
  db_parameters = {
    "shared_preload_libraries" = "pg_stat_statements,auto_explain"
    "log_min_duration_statement" = "1000"   # Log queries > 1s
  }
}

# ── ElastiCache Redis (Cluster Mode) ──────────────────────────────────────
module "redis" {
  source = "./modules/redis"

  environment         = var.environment
  vpc_id              = module.vpc.vpc_id
  private_subnet_ids  = module.vpc.private_subnet_ids
  kms_key_arn         = module.kms.phi_key_arn
  node_type           = var.environment == "production" ? "cache.r7g.large" : "cache.t4g.small"
  num_cache_clusters  = var.environment == "production" ? 3 : 1
  at_rest_encryption  = true
  in_transit_encryption = true
  auth_token_enabled  = true
}

# ── ECS Fargate (Application Services) ───────────────────────────────────
module "ecs" {
  source = "./modules/ecs"

  environment        = var.environment
  vpc_id             = module.vpc.vpc_id
  private_subnet_ids = module.vpc.private_subnet_ids
  public_subnet_ids  = module.vpc.public_subnet_ids
  kms_key_arn        = module.kms.phi_key_arn

  services = {
    api = {
      image        = "${var.ecr_registry}/pharmpilot-api:${var.image_tag}"
      cpu          = var.environment == "production" ? 2048 : 512
      memory       = var.environment == "production" ? 4096 : 1024
      min_capacity = var.environment == "production" ? 3 : 1
      max_capacity = var.environment == "production" ? 20 : 3
      port         = 8001
      health_check_path = "/health"
      env_vars = {
        ENVIRONMENT  = var.environment
        DATABASE_URL = "postgresql+asyncpg://${module.rds.endpoint}/pharmpilot"
        REDIS_URL    = "redis://${module.redis.endpoint}:6379/0"
      }
      secrets = {
        SECRET_KEY          = "${module.secrets.secret_arns["app_secrets"]}:SECRET_KEY"
        ANTHROPIC_API_KEY   = "${module.secrets.secret_arns["app_secrets"]}:ANTHROPIC_API_KEY"
        VAULT_MASTER_KEY_HEX = "${module.secrets.secret_arns["vault_keys"]}:VAULT_MASTER_KEY_HEX"
        DB_PASSWORD         = "${module.secrets.secret_arns["db_credentials"]}:password"
      }
    }

    audio_processor = {
      image        = "${var.ecr_registry}/pharmpilot-audio:${var.image_tag}"
      cpu          = 2048
      memory       = 8192   # Whisper needs memory
      min_capacity = 1
      max_capacity = 5
      port         = 8002
      health_check_path = "/health"
    }
  }
}

# ── WAF (Web Application Firewall) ────────────────────────────────────────
module "waf" {
  source = "./modules/waf"

  environment       = var.environment
  alb_arn           = module.ecs.alb_arn
  enable_rate_limiting = true
  requests_per_5min = 2000  # Per IP — prevents API abuse
  managed_rule_sets = [
    "AWSManagedRulesCommonRuleSet",
    "AWSManagedRulesKnownBadInputsRuleSet",
    "AWSManagedRulesSQLiRuleSet",
  ]
}

# ── S3 (PHI encrypted storage) ────────────────────────────────────────────
resource "aws_s3_bucket" "phi_storage" {
  bucket = "pharmpilot-phi-${var.environment}-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "phi" {
  bucket = aws_s3_bucket.phi_storage.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = module.kms.phi_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "phi" {
  bucket = aws_s3_bucket.phi_storage.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_public_access_block" "phi" {
  bucket                  = aws_s3_bucket.phi_storage.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── CloudTrail (Immutable audit for HIPAA) ────────────────────────────────
resource "aws_cloudtrail" "pharmpilot" {
  name                          = "pharmpilot-${var.environment}"
  s3_bucket_name                = aws_s3_bucket.phi_storage.id
  include_global_service_events = true
  is_multi_region_trail         = true
  enable_log_file_validation    = true  # Tamper detection
  kms_key_id                    = module.kms.logs_key_arn

  event_selector {
    read_write_type           = "All"
    include_management_events = true
    data_resource {
      type   = "AWS::S3::Object"
      values = ["${aws_s3_bucket.phi_storage.arn}/"]
    }
  }
}

# ── GuardDuty (Threat detection) ──────────────────────────────────────────
resource "aws_guardduty_detector" "pharmpilot" {
  enable = true
  datasources {
    s3_logs { enable = true }
    kubernetes { audit_logs { enable = false } }
    malware_protection { scan_ec2_instance_with_findings { ebs_volumes { enable = true } } }
  }
}

# ── Outputs ───────────────────────────────────────────────────────────────
output "api_endpoint" {
  value       = module.ecs.alb_dns_name
  description = "PharmPilot API load balancer endpoint"
}

output "database_endpoint" {
  value     = module.rds.endpoint
  sensitive = true
}

data "aws_caller_identity" "current" {}
