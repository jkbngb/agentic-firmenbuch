// Storage account (ADLS Gen2) with the raw + parsed blob containers (§4.0, §5).
@description('Azure region for the storage account.')
param location string

@description('Globally-unique storage account name (3-24 lowercase alphanumeric).')
param storageAccountName string

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    isHnsEnabled: true // ADLS Gen2 hierarchical namespace
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

// Immutable raw artifacts and the parsed-JSON projection (Blob names use hyphens, §5).
var containers = [
  '90-raw'
  '70-parsed'
]

resource blobContainers 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = [
  for name in containers: {
    parent: blobService
    name: name
    properties: {
      publicAccess: 'None'
    }
  }
]

output storageAccountId string = storage.id
output storageAccountName string = storage.name
output blobEndpoint string = storage.properties.primaryEndpoints.blob
