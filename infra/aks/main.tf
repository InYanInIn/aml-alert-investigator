data "azurerm_client_config" "current" {}

data "azurerm_subscription" "current" {}

data "azurerm_cognitive_account" "openai" {
  name                = "aml-investigator-openai"
  resource_group_name = "rg-aml-investigator"
}

resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

resource "azurerm_resource_group" "demo" {
  name     = "rg-aml-demo"
  location = var.location
}

resource "azurerm_container_registry" "demo" {
  name                = "amldemo${random_string.suffix.result}"
  resource_group_name = azurerm_resource_group.demo.name
  location            = azurerm_resource_group.demo.location
  sku                 = "Basic"
  admin_enabled       = false
}

resource "azurerm_kubernetes_cluster" "demo" {
  name                      = "aks-aml-demo"
  resource_group_name       = azurerm_resource_group.demo.name
  location                  = azurerm_resource_group.demo.location
  dns_prefix                = "aml-demo-${random_string.suffix.result}"
  sku_tier                  = "Free"
  oidc_issuer_enabled       = true
  workload_identity_enabled = true

  default_node_pool {
    name            = "system"
    vm_size         = "Standard_B2s_v2"
    node_count      = 1
    os_disk_size_gb = 30
  }

  identity {
    type = "SystemAssigned"
  }

  key_vault_secrets_provider {
    secret_rotation_enabled = false
  }

  network_profile {
    network_plugin = "azure"
  }
}

resource "azurerm_role_assignment" "kubelet_acr_pull" {
  scope                            = azurerm_container_registry.demo.id
  role_definition_name             = "AcrPull"
  principal_id                     = azurerm_kubernetes_cluster.demo.kubelet_identity[0].object_id
  skip_service_principal_aad_check = true
}

resource "random_password" "admin" {
  length  = 32
  special = false
}

resource "random_password" "role" {
  for_each = toset(["aml_mcp_reader", "aml_app", "aml_evaluator"])
  length   = 32
  special  = false
}

resource "azurerm_key_vault" "demo" {
  name                       = "kv-aml-${random_string.suffix.result}"
  resource_group_name        = azurerm_resource_group.demo.name
  location                   = azurerm_resource_group.demo.location
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  rbac_authorization_enabled = true
  purge_protection_enabled   = false
  soft_delete_retention_days = 7
}

resource "azurerm_role_assignment" "operator_secrets_officer" {
  scope                = azurerm_key_vault.demo.id
  role_definition_name = "Key Vault Secrets Officer"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "time_sleep" "role_propagation" {
  create_duration = "60s"
  depends_on      = [azurerm_role_assignment.operator_secrets_officer]
}

locals {
  server = "postgres.${var.namespace}.svc.cluster.local"
  dsns = merge(
    {
      aml-owner-dsn         = "postgresql://amladmin:${random_password.admin.result}@${local.server}:5432/postgres?sslmode=disable"
      aml-postgres-password = random_password.admin.result
    },
    {
      for secret, role in {
        aml-mcp-dsn       = "aml_mcp_reader"
        aml-app-dsn       = "aml_app"
        aml-evaluator-dsn = "aml_evaluator"
      } : secret => "postgresql://${role}:${random_password.role[role].result}@${local.server}:5432/aml_sim_ui?sslmode=disable"
    }
  )
}

resource "azurerm_key_vault_secret" "dsn" {
  for_each     = nonsensitive(toset(keys(local.dsns)))
  name         = each.key
  value        = local.dsns[each.key]
  key_vault_id = azurerm_key_vault.demo.id
  depends_on   = [time_sleep.role_propagation]
}

resource "azurerm_user_assigned_identity" "app" {
  name                = "id-aml-app"
  resource_group_name = azurerm_resource_group.demo.name
  location            = azurerm_resource_group.demo.location
}

resource "azurerm_federated_identity_credential" "app" {
  name                = "aml-ui"
  resource_group_name = azurerm_resource_group.demo.name
  parent_id           = azurerm_user_assigned_identity.app.id
  issuer              = azurerm_kubernetes_cluster.demo.oidc_issuer_url
  subject             = "system:serviceaccount:${var.namespace}:${var.service_account}"
  audience            = ["api://AzureADTokenExchange"]
}

resource "azurerm_role_assignment" "app_openai_user" {
  scope                = data.azurerm_cognitive_account.openai.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_role_assignment" "app_openai_reader" {
  scope                = data.azurerm_cognitive_account.openai.id
  role_definition_name = "Reader"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_role_assignment" "app_secrets_user" {
  scope                = azurerm_key_vault.demo.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_user_assigned_identity" "github" {
  name                = "id-aml-github"
  resource_group_name = azurerm_resource_group.demo.name
  location            = azurerm_resource_group.demo.location
}

resource "azurerm_federated_identity_credential" "github" {
  name                = "github-main"
  resource_group_name = azurerm_resource_group.demo.name
  parent_id           = azurerm_user_assigned_identity.github.id
  issuer              = "https://token.actions.githubusercontent.com"
  subject             = "repo:${var.github_repository}:ref:refs/heads/main"
  audience            = ["api://AzureADTokenExchange"]
}

resource "azurerm_role_assignment" "github_acr_push" {
  scope                = azurerm_container_registry.demo.id
  role_definition_name = "AcrPush"
  principal_id         = azurerm_user_assigned_identity.github.principal_id
}

resource "azurerm_role_assignment" "github_cluster_admin" {
  scope                = azurerm_kubernetes_cluster.demo.id
  role_definition_name = "Azure Kubernetes Service Cluster Admin Role"
  principal_id         = azurerm_user_assigned_identity.github.principal_id
}
