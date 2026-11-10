variable "github_repository" {
  description = "Owner/repository allowed to publish images from the protected aws-demo GitHub environment; null disables CI IAM."
  type        = string
  default     = "infraria-labs/mlops-demo"
  validation {
    condition     = var.github_repository == null ? true : can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repository))
    error_message = "Use owner/repository."
  }
}
variable "github_oidc_provider_arn" {
  description = "Existing GitHub OIDC provider ARN; null creates one when CI is enabled."
  type        = string
  default     = null
}
variable "github_oidc_subject_prefix" {
  description = "Exact repository OIDC subject prefix reported by GitHub; supports immutable owner/repository IDs."
  type        = string
  default     = null
  validation {
    condition     = var.github_oidc_subject_prefix == null ? true : can(regex("^repo:[A-Za-z0-9_.-]+(@[0-9]+)?/[A-Za-z0-9_.-]+(@[0-9]+)?$", var.github_oidc_subject_prefix))
    error_message = "Use an exact repo:owner/repository prefix, optionally with immutable @IDs."
  }
}
resource "aws_iam_openid_connect_provider" "github" {
  count           = var.github_repository != null && var.github_oidc_provider_arn == null ? 1 : 0
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}
resource "aws_iam_role" "github" {
  count = var.github_repository != null ? 1 : 0
  name  = "${local.name}-github"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Action = "sts:AssumeRoleWithWebIdentity",
    Principal = { Federated = var.github_oidc_provider_arn != null ? var.github_oidc_provider_arn : aws_iam_openid_connect_provider.github[0].arn },
    Condition = { StringEquals = {
      "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com",
      "token.actions.githubusercontent.com:sub" = "${var.github_oidc_subject_prefix != null ? var.github_oidc_subject_prefix : "repo:${var.github_repository}"}:environment:aws-demo"
    } }
  }] })
}
resource "aws_iam_role_policy" "github" {
  count = var.github_repository != null ? 1 : 0
  role  = aws_iam_role.github[0].id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:DescribeImages", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage"], Resource = [for r in aws_ecr_repository.images : r.arn] }
  ] })
}
output "github_role_arn" {
  value = var.github_repository != null ? aws_iam_role.github[0].arn : null
}
