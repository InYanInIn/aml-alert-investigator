data "azurerm_client_config" "current" {}

data "azurerm_subscription" "current" {}

resource "azurerm_resource_group" "aml" {
  name     = "rg-aml-investigator"
  location = var.location
}

resource "azurerm_cognitive_account" "openai" {
  name                  = "aml-investigator-openai"
  location              = azurerm_resource_group.aml.location
  resource_group_name   = azurerm_resource_group.aml.name
  kind                  = "OpenAI"
  sku_name              = "S0"
  custom_subdomain_name = "aml-investigator-${substr(data.azurerm_client_config.current.subscription_id, 0, 8)}"
  local_auth_enabled    = false
}

resource "azurerm_cognitive_deployment" "chat" {
  name                 = "gpt-5-mini"
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = "gpt-5-mini"
    version = "2025-08-07"
  }

  sku {
    name     = var.chat_sku
    capacity = 250
  }
}

resource "azurerm_cognitive_deployment" "embeddings" {
  name                 = "text-embedding-3-small"
  cognitive_account_id = azurerm_cognitive_account.openai.id

  model {
    format  = "OpenAI"
    name    = "text-embedding-3-small"
    version = "1"
  }

  sku {
    name     = "DataZoneStandard"
    capacity = 100
  }
}

resource "azurerm_role_assignment" "openai_user" {
  scope                = azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_consumption_budget_subscription" "monthly" {
  name            = "aml-investigator-monthly"
  subscription_id = data.azurerm_subscription.current.id
  amount          = 10
  time_grain      = "Monthly"

  time_period {
    start_date = var.budget_start
  }

  dynamic "notification" {
    for_each = [10, 30, 50, 100]
    content {
      operator       = "GreaterThanOrEqualTo"
      threshold      = notification.value
      threshold_type = "Actual"
      contact_emails = [var.budget_email]
    }
  }

  notification {
    operator       = "GreaterThanOrEqualTo"
    threshold      = 100
    threshold_type = "Forecasted"
    contact_emails = [var.budget_email]
  }
}
