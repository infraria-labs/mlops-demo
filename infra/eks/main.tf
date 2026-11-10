data "aws_caller_identity" "current" {}
data "aws_availability_zones" "available" { state = "available" }
locals {
  azs       = slice(data.aws_availability_zones.available.names, 0, 2)
  pod_trust = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "pods.eks.amazonaws.com" }, Action = ["sts:AssumeRole", "sts:TagSession"], Condition = { StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }, ArnEquals = { "aws:SourceArn" = aws_eks_cluster.demo.arn } } }] })
}
resource "aws_vpc" "demo" {
  cidr_block           = "10.83.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
}
resource "aws_internet_gateway" "demo" { vpc_id = aws_vpc.demo.id }
resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.demo.id
  cidr_block              = cidrsubnet(aws_vpc.demo.cidr_block, 8, count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = true
}
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.demo.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.demo.id
  }
}
resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}
resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.demo.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.public.id]
}
resource "aws_iam_role" "cluster" {
  name               = "${var.name}-cluster"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "eks.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy_attachment" "cluster" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}
resource "aws_eks_cluster" "demo" {
  name     = var.name
  role_arn = aws_iam_role.cluster.arn
  version  = var.kubernetes_version
  access_config {
    authentication_mode                         = "API"
    bootstrap_cluster_creator_admin_permissions = false
  }
  upgrade_policy { support_type = "STANDARD" }
  vpc_config {
    subnet_ids              = aws_subnet.public[*].id
    endpoint_private_access = true
    endpoint_public_access  = true
    public_access_cidrs     = var.operator_cidrs
  }
  depends_on = [aws_iam_role_policy_attachment.cluster]
}
resource "aws_eks_access_entry" "operator" {
  cluster_name  = aws_eks_cluster.demo.name
  principal_arn = var.operator_arn
}
resource "aws_eks_access_policy_association" "operator" {
  cluster_name  = aws_eks_cluster.demo.name
  principal_arn = aws_eks_access_entry.operator.principal_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
  access_scope { type = "cluster" }
}
resource "aws_iam_role" "node" {
  name               = "${var.name}-node"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy_attachment" "node" {
  for_each   = toset(["AmazonEKSWorkerNodePolicy", "AmazonEC2ContainerRegistryPullOnly", "AmazonEKS_CNI_Policy"])
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/${each.value}"
}
resource "aws_launch_template" "node" {
  name_prefix = "${var.name}-"
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_size           = 20
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }
  tag_specifications {
    resource_type = "instance"
    tags          = { Project = "meteo-mlops", Environment = "eks-exercise" }
  }
  tag_specifications {
    resource_type = "volume"
    tags          = { Project = "meteo-mlops", Environment = "eks-exercise" }
  }
}
resource "aws_eks_node_group" "demo" {
  cluster_name    = aws_eks_cluster.demo.name
  node_group_name = "demo"
  node_role_arn   = aws_iam_role.node.arn
  # Keep both RWO PVCs and replacement nodes in the same AZ for this one-node lab.
  subnet_ids     = [aws_subnet.public[0].id]
  ami_type       = "AL2023_ARM_64_STANDARD"
  instance_types = var.instance_types
  capacity_type  = var.capacity_type
  scaling_config {
    desired_size = 1
    min_size     = 1
    max_size     = 1
  }
  launch_template {
    id      = aws_launch_template.node.id
    version = aws_launch_template.node.latest_version
  }
  depends_on = [aws_iam_role_policy_attachment.node, aws_route_table_association.public]
}
data "aws_eks_addon_version" "core" {
  for_each           = toset(["vpc-cni", "coredns", "kube-proxy", "eks-pod-identity-agent", "aws-ebs-csi-driver"])
  addon_name         = each.value
  kubernetes_version = var.kubernetes_version
  most_recent        = true
}
resource "aws_eks_addon" "core" {
  for_each      = toset(["vpc-cni", "coredns", "kube-proxy", "eks-pod-identity-agent"])
  cluster_name  = aws_eks_cluster.demo.name
  addon_name    = each.value
  addon_version = data.aws_eks_addon_version.core[each.value].version
  depends_on    = [aws_eks_node_group.demo]
}
resource "aws_iam_role" "ebs" {
  name               = "${var.name}-ebs"
  assume_role_policy = local.pod_trust
}
resource "aws_iam_role_policy_attachment" "ebs" {
  role       = aws_iam_role.ebs.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}
resource "aws_eks_addon" "ebs" {
  cluster_name  = aws_eks_cluster.demo.name
  addon_name    = "aws-ebs-csi-driver"
  addon_version = data.aws_eks_addon_version.core["aws-ebs-csi-driver"].version
  pod_identity_association {
    role_arn        = aws_iam_role.ebs.arn
    service_account = "ebs-csi-controller-sa"
  }
  depends_on = [aws_eks_addon.core, aws_iam_role_policy_attachment.ebs]
}
resource "aws_s3_bucket" "artifacts" {
  bucket = "${var.name}-${data.aws_caller_identity.current.account_id}-${var.region}"
  # Only this disposable lab bucket
  # Existing k3s artifacts are never touched.
  force_destroy = true
}
resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}
resource "aws_iam_role" "mlflow" {
  name               = "${var.name}-mlflow"
  assume_role_policy = local.pod_trust
}
resource "aws_iam_role_policy" "mlflow" {
  role = aws_iam_role.mlflow.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.artifacts.arn, Condition = { StringLike = { "s3:prefix" = ["mlflow", "mlflow/*"] } } },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"], Resource = "${aws_s3_bucket.artifacts.arn}/mlflow/*" }
  ] })
}
resource "aws_eks_pod_identity_association" "mlflow" {
  cluster_name    = aws_eks_cluster.demo.name
  namespace       = "meteo-mlops"
  service_account = "mlflow"
  role_arn        = aws_iam_role.mlflow.arn
  depends_on      = [aws_eks_addon.core]
}
