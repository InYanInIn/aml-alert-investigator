variable "location" {
  type    = string
  default = "swedencentral"
}

variable "github_repository" {
  description = "GitHub OIDC repository subject (owner@owner_id/name@repo_id) whose main branch may deploy."
  type        = string
  default     = "InYanInIn@61251392/aml-alert-investigator@1437724437"
}

variable "namespace" {
  type    = string
  default = "aml"
}

variable "service_account" {
  description = "Kubernetes service account of the UI; the Helm release name is aml, so the chart names it aml-ui."
  type        = string
  default     = "aml-ui"
}
