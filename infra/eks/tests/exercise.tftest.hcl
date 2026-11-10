mock_provider "aws" {
  mock_data "aws_eks_addon_version" { defaults = { version = "v1.0.0-eksbuild.1" } }
  mock_data "aws_caller_identity" { defaults = { account_id = "123456789012" } }
  mock_data "aws_availability_zones" { defaults = { names = ["us-west-2a", "us-west-2b"] } }
}
variables {
  operator_arn   = "arn:aws:iam::123456789012:role/operator"
  operator_cidrs = ["203.0.113.10/32"]
}
run "bounded_exercise" {
  command = plan
  assert {
    condition     = aws_eks_cluster.demo.vpc_config[0].endpoint_private_access && aws_eks_cluster.demo.vpc_config[0].public_access_cidrs == toset(["203.0.113.10/32"])
    error_message = "Restrict the public API and retain private node access."
  }
  assert {
    condition     = aws_eks_cluster.demo.upgrade_policy[0].support_type == "STANDARD" && aws_eks_node_group.demo.scaling_config[0].max_size == 1
    error_message = "Avoid extended support and unintended node growth."
  }
  assert {
    condition     = aws_launch_template.node.metadata_options[0].http_put_response_hop_limit == 1 && aws_launch_template.node.metadata_options[0].http_tokens == "required"
    error_message = "Pods must use Pod Identity instead of node IMDS credentials."
  }
  assert {
    condition     = aws_eks_pod_identity_association.mlflow.service_account == "mlflow" && aws_eks_pod_identity_association.mlflow.namespace == "meteo-mlops"
    error_message = "Scope artifact credentials to MLflow."
  }
  assert {
    condition     = aws_s3_bucket.artifacts.force_destroy && aws_s3_bucket_public_access_block.artifacts.block_public_policy
    error_message = "Disposable artifacts must remain private and removable."
  }
}
run "reject_global_api" {
  command = plan
  variables { operator_cidrs = ["0.0.0.0/0"] }
  expect_failures = [var.operator_cidrs]
}
