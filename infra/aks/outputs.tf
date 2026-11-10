output "github_variables" {
  description = "Values for the GitHub repository variables read by .github/workflows/deploy.yml."
  value = {
    AZURE_CLIENT_ID        = azurerm_user_assigned_identity.github.client_id
    AZURE_TENANT_ID        = data.azurerm_client_config.current.tenant_id
    AZURE_SUBSCRIPTION_ID  = data.azurerm_subscription.current.subscription_id
    RESOURCE_GROUP         = azurerm_resource_group.demo.name
    ACR_NAME               = azurerm_container_registry.demo.name
    AKS_NAME               = azurerm_kubernetes_cluster.demo.name
    KEY_VAULT_NAME         = azurerm_key_vault.demo.name
    APP_IDENTITY_CLIENT_ID = azurerm_user_assigned_identity.app.client_id
    AZURE_OPENAI_ENDPOINT  = data.azurerm_cognitive_account.openai.endpoint
  }
}

output "key_vault_name" {
  value = azurerm_key_vault.demo.name
}

output "postgres_host" {
  value = azurerm_postgresql_flexible_server.demo.fqdn
}
