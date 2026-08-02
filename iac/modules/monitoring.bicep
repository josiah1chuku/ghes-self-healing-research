targetScope = 'resourceGroup'

param environmentName string
param location string
param logRetentionDays int = 90
param tags object

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'ghes-research-law-${environmentName}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: logRetentionDays
    publicNetworkAccessForQuery: 'Enabled'
    publicNetworkAccessForIngestion: 'Enabled'
    workspaceCapping: { dailyQuotaGb: 1 }
  }
}

resource dcr 'Microsoft.Insights/dataCollectionRules@2023-03-11' = {
  name: 'ghes-research-dcr-${environmentName}'
  location: location
  tags: tags
  properties: {
    dataSources: {
      performanceCounters: [
        {
          name: 'VMInsightsPerfCounters'
          samplingFrequencyInSeconds: 60
          streams: ['Microsoft-InsightsMetrics']
          counterSpecifiers: [
            '\\Processor Information(_Total)\\% Processor Time'
            '\\Memory\\Available MBytes'
            '\\LogicalDisk(/)\\% Free Space'
            '\\LogicalDisk(/)\\Disk Read Bytes/sec'
            '\\LogicalDisk(/)\\Disk Write Bytes/sec'
            '\\LogicalDisk(/)\\Current Disk Queue Length'
            '\\Network Interface(*)\\Bytes Received/sec'
            '\\Network Interface(*)\\Bytes Sent/sec'
          ]
        }
      ]
      syslog: [
        {
          name: 'GHESSyslog'
          streams: ['Microsoft-Syslog']
          facilityNames: ['syslog', 'daemon', 'kern', 'auth']
          logLevels: ['Warning', 'Error', 'Critical', 'Alert', 'Emergency']
        }
      ]
    }
    destinations: {
      logAnalytics: [
        {
          name: 'ghes-law-destination'
          workspaceResourceId: workspace.id
        }
      ]
    }
    dataFlows: [
      {
        streams: ['Microsoft-InsightsMetrics']
        destinations: ['ghes-law-destination']
        transformKql: 'source'
        outputStream: 'Microsoft-InsightsMetrics'
      }
      {
        streams: ['Microsoft-Syslog']
        destinations: ['ghes-law-destination']
        transformKql: 'source'
        outputStream: 'Microsoft-Syslog'
      }
    ]
  }
}

output workspaceId string = workspace.properties.customerId
output workspaceResourceId string = workspace.id
output dcrId string = dcr.id
output workspaceName string = workspace.name
