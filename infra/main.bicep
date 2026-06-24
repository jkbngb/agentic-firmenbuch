// agentic-firmenbuch — full environment, one declarative deployment (§4.0).
// Subscription-scoped: creates the resource group, then all resources inside it.
// Idempotent: re-running creates only what's missing (Bicep is declarative).
targetScope = 'subscription'

@description('EU-only region (setup.sh chooses germanywestcentral -> westeurope -> northeurope).')
param location string

@description('Environment short name, e.g. prod / dev.')
param environmentName string = 'prod'

@description('Lowercase base name (3-11 chars) used to derive globally-unique resource names.')
@minLength(3)
@maxLength(11)
param baseName string = 'firmenbuch'

@description('Resource group name.')
param resourceGroupName string = 'rg-${baseName}-${environmentName}'

// Deterministic, globally-unique suffix derived from the subscription + base name.
var suffix = take(uniqueString(subscription().id, baseName, environmentName), 8)
var names = {
  storage: take('st${baseName}${suffix}', 24)
  cosmos: take('cosmos-${baseName}-${suffix}', 44)
  keyVault: take('kv-${baseName}-${suffix}', 24)
  acr: take('acr${baseName}${suffix}', 50)
  workspace: 'log-${baseName}-${environmentName}'
  appInsights: 'appi-${baseName}-${environmentName}'
  identity: 'id-${baseName}-${environmentName}'
  acaEnv: 'cae-${baseName}-${environmentName}'
  job: 'job-${baseName}-pipeline'
  mcp: 'app-${baseName}-mcp'
  communication: 'acs-${baseName}-${suffix}'
  email: 'acs-email-${baseName}-${suffix}'
}

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
}

module identity 'modules/identity.bicep' = {
  name: 'identity'
  scope: rg
  params: {
    location: location
    identityName: names.identity
  }
}

module storage 'modules/storage.bicep' = {
  name: 'storage'
  scope: rg
  params: {
    location: location
    storageAccountName: names.storage
  }
}

module cosmos 'modules/cosmos.bicep' = {
  name: 'cosmos'
  scope: rg
  params: {
    location: location
    cosmosAccountName: names.cosmos
  }
}

module keyVault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  scope: rg
  params: {
    location: location
    keyVaultName: names.keyVault
  }
}

module acr 'modules/acr.bicep' = {
  name: 'acr'
  scope: rg
  params: {
    location: location
    acrName: names.acr
  }
}

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  scope: rg
  params: {
    location: location
    workspaceName: names.workspace
    appInsightsName: names.appInsights
  }
}

module communication 'modules/communication.bicep' = {
  name: 'communication'
  scope: rg
  params: {
    communicationName: names.communication
    emailName: names.email
  }
}

module rbac 'modules/rbac.bicep' = {
  name: 'rbac'
  scope: rg
  params: {
    principalId: identity.outputs.principalId
    storageAccountName: storage.outputs.storageAccountName
    keyVaultName: keyVault.outputs.keyVaultName
    acrName: acr.outputs.acrName
    cosmosAccountName: cosmos.outputs.cosmosAccountName
  }
}

module containerapps 'modules/containerapps.bicep' = {
  name: 'containerapps'
  scope: rg
  params: {
    location: location
    environmentName: names.acaEnv
    jobName: names.job
    mcpAppName: names.mcp
    logAnalyticsWorkspaceId: monitoring.outputs.workspaceId
    managedIdentityId: identity.outputs.identityId
    managedIdentityClientId: identity.outputs.clientId
    acrLoginServer: acr.outputs.acrLoginServer
    cosmosEndpoint: cosmos.outputs.cosmosEndpoint
    blobAccountUrl: storage.outputs.blobEndpoint
  }
}

output resourceGroup string = rg.name
output cosmosEndpoint string = cosmos.outputs.cosmosEndpoint
output blobEndpoint string = storage.outputs.blobEndpoint
output keyVaultUri string = keyVault.outputs.keyVaultUri
output acrLoginServer string = acr.outputs.acrLoginServer
output appInsightsConnectionString string = monitoring.outputs.appInsightsConnectionString
output mcpFqdn string = containerapps.outputs.mcpFqdn
