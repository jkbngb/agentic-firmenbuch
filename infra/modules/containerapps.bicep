// Container Apps Environment + the pipeline Job (cron) and the MCP HTTP app (§4.0, §8.8/§8.9).
@description('Azure region.')
param location string

@description('Container Apps environment name.')
param environmentName string

@description('Pipeline Job name = the quarterly full reconcile/grind. The daily change-feed job is named "<jobName>-daily".')
param jobName string

@description('MCP Container App name.')
param mcpAppName string

@description('Log Analytics workspace resource id (for the environment).')
param logAnalyticsWorkspaceId string

@description('User-assigned managed identity resource id used by both workloads.')
param managedIdentityId string

@description('Client (app) id of the user-assigned MI — required so DefaultAzureCredential picks it.')
param managedIdentityClientId string

@description('ACR login server for pulling images.')
param acrLoginServer string

@description('Container image for the pipeline job (override once built + pushed to ACR).')
param pipelineImage string = 'mcr.microsoft.com/k8se/quickstart-jobs:latest'

@description('Container image for the MCP server (override once built + pushed to ACR).')
param mcpImage string = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

@description('Daily change-feed cron (UTC). Cheap delta: new registrations / deletions / filings.')
param dailyCron string = '0 3 * * *'

@description('Quarterly full reconcile/grind cron (UTC). Default: 02:00 on the 1st of Jan/Apr/Jul/Oct.')
param syncCron string = '0 2 1 */3 *'

@description('Replica timeout (s) for the daily change-feed job. Minutes of work; 4h is ample.')
param dailyReplicaTimeout int = 14400

@description('Replica timeout (s) for the quarterly grind. The full prefix-walk can run many hours to days; 7-day headroom so one pass always completes (the checkpoint also lets it resume if ever killed).')
param syncReplicaTimeout int = 604800

@description('Cron for the monthly OeNB directory sync (register-based FI flag, issue #15). Default: 04:00 on the 1st.')
param directoriesCron string = '0 4 1 * *'

@description('Cosmos endpoint passed to workloads (data plane via Managed Identity).')
param cosmosEndpoint string

@description('Blob account URL passed to workloads.')
param blobAccountUrl string

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' existing = {
  name: last(split(logAnalyticsWorkspaceId, '/'))
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: environmentName
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
  }
}

// Two scheduled Container Apps Jobs (both singletons; the run lock also prevents overlap):
//   - the DAILY change-feed delta (cheap, minutes), the steady-state path, and
//   - the QUARTERLY full reconcile/grind (the prefix-walk a*, aa*, …, many hours; 4x/year),
//     the completeness safety net that emits the "what the change feed missed" drift report
//     (§15a.1). NOTE: cron is '0 2 1 */3 *' (1st of Jan/Apr/Jul/Oct), NOT monthly.
var jobDefs = [
  {
    name: '${jobName}-daily'
    mode: 'daily'
    cron: dailyCron
    timeout: dailyReplicaTimeout
  }
  {
    name: jobName
    mode: 'sync-registry'
    cron: syncCron
    timeout: syncReplicaTimeout
  }
  {
    // Active-only, XML-only document backfill (the "second run"). Hourly trigger, but the
    // job self-defers (no-op) until the registry grind has finished, then runs once and
    // resumes from its blob checkpoint if a replica times out. Same 7-day replica window
    // as the grind. The FIRMENBUCH_API_KEY secret is wired onto the job like the others.
    name: '${jobName}-backfill-ingest'
    mode: 'backfill-ingest'
    cron: '0 * * * *'
    timeout: syncReplicaTimeout
  }
  {
    // Monthly OeNB register sync (issue #15): downloads the MFI + NMFI lists, archives them
    // dated to 90-raw, and reconciles 00_directories (the authoritative is_financial_institution
    // flag). No API key needed — public CC-BY CSVs. Cheap (~10 s); a 1 h replica window is ample.
    name: '${jobName}-directories'
    mode: 'directories'
    cron: directoriesCron
    timeout: 3600
  }
]

resource pipelineJobs 'Microsoft.App/jobs@2024-03-01' = [
  for j in jobDefs: {
    name: j.name
    location: location
    identity: {
      type: 'UserAssigned'
      userAssignedIdentities: {
        '${managedIdentityId}': {}
      }
    }
    properties: {
      environmentId: environment.id
      configuration: {
        triggerType: 'Schedule'
        replicaTimeout: j.timeout
        replicaRetryLimit: 1
        scheduleTriggerConfig: {
          cronExpression: j.cron
          parallelism: 1 // singleton: never two runs at once (run lock also enforced in code)
          replicaCompletionCount: 1
        }
        registries: [
          {
            server: acrLoginServer
            identity: managedIdentityId
          }
        ]
      }
      template: {
        containers: [
          {
            name: 'pipeline'
            image: pipelineImage
            resources: {
              cpu: 2
              memory: '4Gi'
            }
            args: ['--mode', j.mode]
            env: [
              { name: 'COSMOS_ENDPOINT', value: cosmosEndpoint }
              { name: 'BLOB_ACCOUNT_URL', value: blobAccountUrl }
              { name: 'AZURE_CLIENT_ID', value: managedIdentityClientId }
            ]
          }
        ]
      }
    }
  }
]

// The MCP server is an HTTP Container App that can scale to zero.
resource mcpApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: mcpAppName
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${managedIdentityId}': {}
    }
  }
  properties: {
    environmentId: environment.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      registries: [
        {
          server: acrLoginServer
          identity: managedIdentityId
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'mcp'
          image: mcpImage
          resources: {
            cpu: 1
            memory: '2Gi'
          }
          env: [
            { name: 'COSMOS_ENDPOINT', value: cosmosEndpoint }
            { name: 'BLOB_ACCOUNT_URL', value: blobAccountUrl }
            { name: 'AZURE_CLIENT_ID', value: managedIdentityClientId }
          ]
        }
      ]
      scale: {
        // min=1: MCP streamable-HTTP holds persistent sessions per connector.
        // Scale-to-zero kills live Cowork/Code/Cursor connections mid-conversation
        // (observed 2026-06-25 around 19:59 UTC) — "Tool not found" + connector
        // vanishing from registry. Keep one warm replica.
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

output environmentId string = environment.id
output mcpFqdn string = mcpApp.properties.configuration.ingress.fqdn
