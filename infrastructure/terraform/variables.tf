variable "aws_region"   { default = "us-east-1" }
variable "environment"  { description = "staging | production" }
variable "image_tag"    { description = "Docker image tag to deploy" }
variable "ecr_registry" { description = "AWS ECR registry URL" }
