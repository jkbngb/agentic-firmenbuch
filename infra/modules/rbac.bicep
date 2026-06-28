// Least-privilege data-plane role assignments for the workloads' managed identity (§4.0, §14).
@description('Principal (object) id of the user-assigned managed identity.')
param principalId string

@description('Storage account name to grant blob data access on.')
param storageAccountName string

@description('Key Vault name to grant secret read on.')
param keyVaultName string

@description('ACR name to grant pull on.')
param acrName string

@description('Cosmos account name to grant data-plane access on.')
param cosmosAccountName string

// Built-in role definition ids.
var blobDataContributor = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
// Storage Blob Delegator: lets the MI request a user-delegation key to sign short-lived SAS
// download links (get_document, ROADMAP P2.2). Required IN ADDITION to Blob Data Contributor —
// data access alone cannot mint a user-delegation SAS; get_user_delegation_key 403s without it.
var blobDelegator = 'db58b8e5-c6ad-4a2a-8342-4190687cbf4a'
var keyVaultSecretsUser = '4633458b-17de-408a-b874-0445c86b69e6'
var acrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
// Cosmos DB built-in data contributor (SQL data-plane role).
var cosmosDataContributor = '00000000-0000-0000-0000-000000000002'

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource keyVault 'Microsoft.KeyVault/vaults@2024-11-01' existing = {
  name: keyVaultName
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: acrName
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' existing = {
  name: cosmosAccountName
}

resource blobAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, principalId, blobDataContributor)
  scope: storage
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      blobDataContributor
    )
  }
}

resource blobDelegatorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, principalId, blobDelegator)
  scope: storage
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      blobDelegator
    )
  }
}

resource kvAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, principalId, keyVaultSecretsUser)
  scope: keyVault
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      keyVaultSecretsUser
    )
  }
}

resource acrAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, principalId, acrPull)
  scope: acr
  properties: {
    principalId: principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPull)
  }
}

// Cosmos data-plane access is a SQL role assignment on the account (not Azure RBAC).
resource cosmosAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = {
  parent: cosmos
  name: guid(cosmos.id, principalId, cosmosDataContributor)
  properties: {
    principalId: principalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/${cosmosDataContributor}'
    scope: cosmos.id
  }
}
