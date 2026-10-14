targetScope = 'resourceGroup'

param location string
param tags object

var suffix = uniqueString(resourceGroup().id)
var kvName = 'ghes-kv-${suffix}'

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'ghes-research-sec-law'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 365
  }
}

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: kvName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true
    publicNetworkAccess: 'Enabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
  }
}

resource cmk 'Microsoft.KeyVault/vaults/keys@2023-07-01' = {
  parent: kv
  name: 'ghes-disk-cmk'
  properties: {
    kty: 'RSA'
    keySize: 3072
    keyOps: ['wrapKey', 'unwrapKey']
    rotationPolicy: {
      lifetimeActions: [
        {
          trigger: { timeAfterCreate: 'P180D' }
          action: { type: 'rotate' }
        }
      ]
      attributes: { expiryTime: 'P1Y' }
    }
  }
}

resource des 'Microsoft.Compute/diskEncryptionSets@2023-04-02' = {
  name: 'ghes-research-des'
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    encryptionType: 'EncryptionAtRestWithCustomerKey'
    rotationToLatestKeyVersionEnabled: true
    activeKey: {
      sourceVault: { id: kv.id }
      keyUrl: cmk.properties.keyUriWithVersion
    }
  }
}

// Key Vault Crypto Service Encryption User
var cryptoUserRoleId = 'e147488a-f6f5-4113-8e2d-b22465e65bf6'
resource desAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, des.id, cryptoUserRoleId)
  scope: kv
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cryptoUserRoleId)
    principalId: des.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource kvDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'kv-audit'
  scope: kv
  properties: {
    workspaceId: law.id
    logs: [{ categoryGroup: 'audit', enabled: true }]
  }
}

output diskEncryptionSetId string = des.id
output keyVaultName string = kv.name
