variable "location" {
  type    = string
  default = "swedencentral"
}

variable "github_repository" {
  description = "owner/name of the repository whose main branch may deploy."
  type        = string
  default     = "InYanInIn/aml-alert-investigator"
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
