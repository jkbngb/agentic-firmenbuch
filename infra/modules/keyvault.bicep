// Key Vault for secrets (FIRMENBUCH_API_KEY, ACS connection string, …), RBAC-authorized (§14).
@description('Azure region for the Key Vault.')
param location string

@description('Globally-unique Key Vault name (3-24 alphanumeric/hyphen).')
param keyVaultName string

@description('Tenant ID for the vault.')
param tenantId string = subscription().tenantId

resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: keyVaultName
  location: location
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: tenantId
    enableRbacAuthorization: true // RBAC, not access policies (least privilege via Managed Identity)
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
  }
}

output keyVaultId string = keyVault.id
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
