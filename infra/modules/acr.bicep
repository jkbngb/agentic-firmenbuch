// Azure Container Registry for the pipeline + MCP images (§4.0, §13).
@description('Azure region for the registry.')
param location string

@description('Globally-unique ACR name (5-50 alphanumeric).')
param acrName string

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' = {
  name: acrName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false // pull via Managed Identity, not admin creds
  }
}

output acrId string = acr.id
output acrLoginServer string = acr.properties.loginServer
output acrName string = acr.name
