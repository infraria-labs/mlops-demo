variable "region" {
  type    = string
  default = "us-west-2"
}
variable "environment" {
  type    = string
  default = "demo"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,19}$", var.environment))
    error_message = "Use a short lowercase environment name."
  }
}
variable "instance_type" {
  type    = string
  default = "t4g.medium"
  validation {
    condition     = contains(["t4g.small", "t4g.medium", "t4g.large"], var.instance_type)
    error_message = "Choose a supported ARM burstable instance."
  }
}
variable "data_volume_gib" {
  type    = number
  default = 30
  validation {
    condition     = var.data_volume_gib >= 20 && var.data_volume_gib <= 100 && floor(var.data_volume_gib) == var.data_volume_gib
    error_message = "Data volume must be an integer between 20 and 100 GiB."
  }
}
variable "k3s_version" {
  type    = string
  default = "v1.33.9+k3s1"
  validation {
    condition     = can(regex("^v[0-9]+\\.[0-9]+\\.[0-9]+\\+k3s[0-9]+$", var.k3s_version))
    error_message = "Use an explicit k3s release."
  }
}
