variable "region" { default = "us-west-2" }
variable "name" { default = "meteo-mlops-eks-exercise" }
variable "kubernetes_version" { default = "1.35" }
variable "operator_arn" {
  type        = string
  description = "IAM user/role ARN, never an STS session ARN, permitted to administer this temporary cluster."
}
variable "operator_cidrs" {
  type        = list(string)
  description = "Operator public IPv4 /32 addresses permitted to reach the Kubernetes API."
  validation {
    condition     = length(var.operator_cidrs) > 0 && alltrue([for c in var.operator_cidrs : can(cidrnetmask(c)) && endswith(c, "/32")])
    error_message = "Supply explicit IPv4 /32 operator addresses; do not expose the API globally."
  }
}
variable "instance_types" { default = ["t4g.large"] }
variable "capacity_type" {
  default = "ON_DEMAND"
  validation {
    condition     = contains(["ON_DEMAND", "SPOT"], var.capacity_type)
    error_message = "Use ON_DEMAND or SPOT."
  }
}
