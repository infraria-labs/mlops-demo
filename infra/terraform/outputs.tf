output "region" { value = var.region }
output "instance_id" { value = aws_instance.node.id }
output "data_volume_id" { value = aws_ebs_volume.data.id }
output "artifact_bucket" { value = aws_s3_bucket.artifacts.id }
output "artifact_destination" { value = "s3://${aws_s3_bucket.artifacts.id}/mlflow" }
output "repositories" { value = { for name, r in aws_ecr_repository.images : name => r.repository_url } }
