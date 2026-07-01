// Cosmos DB (serverless) database + containers with partition keys and the
// 10_presentation indexing policy (§4.0, §4.1, §5).
@description('Azure region for the Cosmos account.')
param location string

@description('Globally-unique Cosmos account name (3-44 lowercase alphanumeric/hyphen).')
param cosmosAccountName string

@description('Database name.')
param databaseName string = 'firmenbuch'

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: cosmosAccountName
  location: location
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    enableFreeTier: false
    disableLocalAuth: false // keys allowed (so the Portal Data Explorer works); the pipeline still uses Managed Identity. Set true to re-harden (§4.0).
    capabilities: [
      { name: 'EnableServerless' }
    ]
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: false
      }
    ]
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: account
  name: databaseName
  properties: {
    resource: {
      id: databaseName
    }
  }
}

// Containers and their partition keys (§4.0). Reserved v2 layers are created up front
// so adding a source later is additive (40_enriched, 20_scored).
var fnrContainers = [
  '50_consolidated'
  '30_derived'
  '99_registry'
  '40_enriched'
  '20_scored'
]

resource genericContainers 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = [
  for name in fnrContainers: {
    parent: database
    name: name
    properties: {
      resource: {
        id: name
        partitionKey: {
          paths: ['/fnr']
          kind: 'Hash'
        }
      }
    }
  }
]

// 00_accounts is partitioned by token_hash (MCP signup, §5).
resource accountsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: '00_accounts'
  properties: {
    resource: {
      id: '00_accounts'
      partitionKey: {
        paths: ['/token_hash']
        kind: 'Hash'
      }
    }
  }
}

// 00_oauth_* — Cowork/claude.ai connector data (OAuth 2.1, §8.10b).
// All three partitioned by /id (random opaque), low volume, default policy.
resource oauthClientsContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: '00_oauth_clients'
  properties: {
    resource: {
      id: '00_oauth_clients'
      partitionKey: { paths: ['/id'], kind: 'Hash' }
    }
  }
}
// 00_directories — register-sourced financial institutions (OeNB/EIOPA), keyed by Firmenbuchnummer
// (issue #15). Low volume (~450), the authoritative is_financial_institution flag.
resource directoriesContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: '00_directories'
  properties: {
    resource: {
      id: '00_directories'
      partitionKey: { paths: ['/id'], kind: 'Hash' }
    }
  }
}
resource oauthCodesContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: '00_oauth_codes'
  properties: {
    resource: {
      id: '00_oauth_codes'
      partitionKey: { paths: ['/id'], kind: 'Hash' }
      // Codes are one-shot and live 10 min; let Cosmos sweep them.
      defaultTtl: 86400
    }
  }
}
// 00_oauth_pending — half-finished /authorize flows (email sent, magic link unclicked).
resource oauthPendingContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: '00_oauth_pending'
  properties: {
    resource: {
      id: '00_oauth_pending'
      partitionKey: { paths: ['/id'], kind: 'Hash' }
      defaultTtl: 86400  // pending grants live 15 min; sweep the rest
    }
  }
}
resource oauthTokensContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: '00_oauth_tokens'
  properties: {
    resource: {
      id: '00_oauth_tokens'
      partitionKey: { paths: ['/id'], kind: 'Hash' }
    }
  }
}

// 00_usage — per-user daily consumption rollup (V2 §8). One doc per (key_hash, day),
// partitioned by /id (id = u_<keyhash16>_<day>). 365-day TTL: usage history
// garbage-collects automatically.
resource usageContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: '00_usage'
  properties: {
    resource: {
      id: '00_usage'
      partitionKey: { paths: ['/id'], kind: 'Hash' }
      defaultTtl: 31536000
      indexingPolicy: {
        indexingMode: 'consistent'
        includedPaths: [{ path: '/key_hash/?' }, { path: '/day_utc/?' }]
        excludedPaths: [{ path: '/*' }]
      }
    }
  }
}

// 10_presentation is the serving container: index only the fields search_companies
// filters/sorts on; exclude large nested histories to control RU (§4.1).
resource presentedContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = {
  parent: database
  name: '10_presentation'
  properties: {
    resource: {
      id: '10_presentation'
      partitionKey: {
        paths: ['/fnr']
        kind: 'Hash'
      }
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/identity/status/?' }
          { path: '/identity/legal_form/?' }
          { path: '/location/bundesland/?' }
          { path: '/size/gkl/?' }
          { path: '/financials/has_guv/?' }
          { path: '/financials/has_guv_latest/?' }
          { path: '/financials/latest/bilanzsumme/?' }
          { path: '/financials/latest/revenue/?' }
          { path: '/ratios/equity_ratio/latest/?' }
          { path: '/employees/latest/?' }
          { path: '/growth/profile/?' }
          { path: '/company/last_filing_year/?' }
          { path: '/company/founded_year/?' }
          { path: '/company/description/?' }
          { path: '/management/primary_manager_name/?' }
          // Branch / industry + location filters (issue #19)
          { path: '/branch/oenace/section/?' }
          { path: '/branch/oenace/division/?' }
          { path: '/branch/oenace/group/?' }
          { path: '/location/postal_code/?' }
          { path: '/location/city/?' }
        ]
        // Opt-in indexing: exclude everything, then the includedPaths above are the
        // only indexed fields. (Cosmos forbids mid-path wildcards like
        // '/ratios/*/history/*', so a single trailing '/*' exclude is the safe form;
        // the large nested histories / _meta fall under it automatically.)
        excludedPaths: [
          { path: '/*' }
        ]
      }
    }
  }
}

output cosmosAccountId string = account.id
output cosmosAccountName string = account.name
output cosmosEndpoint string = account.properties.documentEndpoint
