// User-assigned managed identity shared by the pipeline Job and the MCP app (§4.0).
@description('Azure region.')
param location string

@description('Managed identity name.')
param identityName string

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
}

output identityId string = identity.id
output principalId string = identity.properties.principalId
output clientId string = identity.properties.clientId
