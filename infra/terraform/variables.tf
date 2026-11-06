variable "location" {
  type    = string
  default = "polandcentral"
}

variable "chat_sku" {
  description = "DataZoneStandard needs quota the subscription does not have yet (ADR-0007 fallback)."
  type        = string
  default     = "GlobalStandard"
}

variable "budget_email" {
  type = string
}

variable "budget_start" {
  description = "First day of the month the budget starts, RFC 3339."
  type        = string
  default     = "2026-11-01T00:00:00Z"
}
