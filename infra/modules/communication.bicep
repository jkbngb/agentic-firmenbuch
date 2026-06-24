// Azure Communication Services + Email for MCP signup token delivery (§4.0, §8.10).
// Communication Services data is global; dataLocation is pinned to Europe for GDPR.
@description('Communication Services resource name.')
param communicationName string

@description('Email Communication Services resource name.')
param emailName string

resource email 'Microsoft.Communication/emailServices@2023-04-01' = {
  name: emailName
  location: 'global'
  properties: {
    dataLocation: 'Europe'
  }
}

// Azure-managed domain for sending (no custom DNS needed to start).
resource emailDomain 'Microsoft.Communication/emailServices/domains@2023-04-01' = {
  parent: email
  name: 'AzureManagedDomain'
  location: 'global'
  properties: {
    domainManagement: 'AzureManaged'
    userEngagementTracking: 'Disabled'
  }
}

resource communication 'Microsoft.Communication/communicationServices@2023-04-01' = {
  name: communicationName
  location: 'global'
  properties: {
    dataLocation: 'Europe'
    linkedDomains: [
      emailDomain.id
    ]
  }
}

output communicationName string = communication.name
