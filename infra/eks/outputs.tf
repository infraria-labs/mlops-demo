output "region" { value = var.region }
output "cluster_name" { value = aws_eks_cluster.demo.name }
output "artifact_bucket" { value = aws_s3_bucket.artifacts.id }
output "artifact_destination" { value = "s3://${aws_s3_bucket.artifacts.id}/mlflow" }
output "vpc_id" { value = aws_vpc.demo.id }
output "mlflow_role_arn" { value = aws_iam_role.mlflow.arn }
output "addon_versions" { value = { for name, addon in data.aws_eks_addon_version.core : name => addon.version } }
