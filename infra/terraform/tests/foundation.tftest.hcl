mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_availability_zones" {
    defaults = { names = ["us-west-2a"] }
  }
  mock_data "aws_ssm_parameter" {
    defaults = { value = "ami-1234567890abcdef0" }
  }
  mock_resource "aws_ecr_repository" {
    defaults = { repository_url = "123456789012.dkr.ecr.us-west-2.amazonaws.com/demo" }
  }
}
run "minimal_locked_down_node" {
  command = plan
  assert {
    condition     = aws_instance.node.instance_type == "t4g.medium" && aws_instance.node.credit_specification[0].cpu_credits == "standard"
    error_message = "Default should use ARM without unlimited CPU credit charges."
  }
  assert {
    condition     = aws_instance.node.metadata_options[0].http_tokens == "required" && aws_instance.node.metadata_options[0].http_put_response_hop_limit == 2
    error_message = "IMDSv2 must support MLflow pod credentials."
  }
  assert {
    condition     = length(aws_security_group.node.ingress) == 0
    error_message = "Do not expose SSH, Kubernetes, inference or MLflow."
  }
  assert {
    condition     = aws_ebs_volume.data.encrypted && aws_instance.node.root_block_device[0].encrypted
    error_message = "Encrypt metadata and root storage."
  }
  assert {
    condition     = aws_s3_bucket_public_access_block.artifacts.block_public_policy && aws_s3_bucket_public_access_block.artifacts.restrict_public_buckets
    error_message = "Artifacts must remain private."
  }
  assert {
    condition     = aws_ecr_repository.images["training"].image_tag_mutability == "IMMUTABLE"
    error_message = "Published workload tags must be immutable."
  }
}
run "github_oidc_release_scope" {
  # Mock apply resolves ECR ARNs so the rendered CI policy can be checked.
  command = apply
  variables {
    github_oidc_subject_prefix = null
    github_repository          = "infraria-labs/mlops-demo"
    github_oidc_provider_arn   = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
  }
  assert {
    condition     = jsondecode(aws_iam_role.github[0].assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:infraria-labs/mlops-demo:environment:aws-demo"
    error_message = "Only the protected AWS GitHub environment may assume the release role."
  }
  assert {
    condition     = length(aws_iam_openid_connect_provider.github) == 0
    error_message = "Reuse an existing OIDC provider rather than create a duplicate."
  }
  assert {
    condition     = jsondecode(aws_iam_role.github[0].assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    error_message = "OIDC audience must be restricted to STS."
  }
  assert {
    condition     = alltrue([for statement in jsondecode(aws_iam_role_policy.github[0].policy).Statement : alltrue([for action in statement.Action : startswith(action, "ecr:")])])
    error_message = "CI publishes ECR artifacts only; no SSM or cluster mutation grants."
  }

}

run "github_immutable_oidc_subject" {
  command = plan
  variables {
    github_repository          = "infraria-labs/mlops-demo"
    github_oidc_provider_arn   = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
    github_oidc_subject_prefix = "repo:infraria-labs@123456789/mlops-demo@987654321"
  }
  assert {
    condition     = jsondecode(aws_iam_role.github[0].assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:infraria-labs@123456789/mlops-demo@987654321:environment:aws-demo"
    error_message = "Immutable repository identities must retain the exact environment restriction."
  }
}
