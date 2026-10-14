targetScope = 'resourceGroup'

param vmName string
param vmSize string
param location string
param osDiskSizeGB int
param dataDiskSizeGB int
param imagePublisher string
param imageOffer string
param imageSku string
param imageVersion string
param nicId string
param adminUsername string
param adminSshPublicKey string
param diskEncryptionSetId string
param logAnalyticsWorkspaceResourceId string = ''
@description('The GHES appliance is not a general-purpose Linux host. Leave false unless you have tested the agent on it.')
param enableAmaExtension bool = false
param tags object

// CMK-encrypted data disk, no public/network access.
resource dataDisk 'Microsoft.Compute/disks@2023-04-02' = {
  name: '${vmName}-data-disk'
  location: location
  tags: tags
  sku: { name: 'Premium_LRS' }
  properties: {
    diskSizeGB: dataDiskSizeGB
    creationData: { createOption: 'Empty' }
    encryption: {
      type: 'EncryptionAtRestWithCustomerKey'
      diskEncryptionSetId: diskEncryptionSetId
    }
    networkAccessPolicy: 'DenyAll'
    publicNetworkAccess: 'Disabled'
  }
}

resource vm 'Microsoft.Compute/virtualMachines@2023-09-01' = {
  name: vmName
  location: location
  tags: tags
  identity: { type: 'SystemAssigned' }
  properties: {
    hardwareProfile: { vmSize: vmSize }
    storageProfile: {
      imageReference: {
        publisher: imagePublisher
        offer: imageOffer
        sku: imageSku
        version: imageVersion
      }
      osDisk: {
        name: '${vmName}-os-disk'
        createOption: 'FromImage'
        diskSizeGB: osDiskSizeGB
        caching: 'ReadWrite'
        managedDisk: {
          storageAccountType: 'Premium_LRS'
          diskEncryptionSet: { id: diskEncryptionSetId }
        }
        deleteOption: 'Delete'
      }
      dataDisks: [
        {
          lun: 0
          name: '${vmName}-data-disk'
          createOption: 'Attach'
          caching: 'None'
          deleteOption: 'Detach'
          managedDisk: { id: dataDisk.id }
        }
      ]
    }
    osProfile: {
      computerName: vmName
      adminUsername: adminUsername
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            {
              path: '/home/${adminUsername}/.ssh/authorized_keys'
              keyData: adminSshPublicKey
            }
          ]
        }
      }
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: nicId
          properties: { primary: true, deleteOption: 'Detach' }
        }
      ]
    }
    // Managed boot diagnostics: no storage account, no public blob endpoint.
    diagnosticsProfile: {
      bootDiagnostics: { enabled: true }
    }
    securityProfile: {
      encryptionAtHost: true
    }
    priority: 'Regular'
  }
}

resource amaExtension 'Microsoft.Compute/virtualMachines/extensions@2023-09-01' = if (enableAmaExtension) {
  name: 'AzureMonitorLinuxAgent'
  parent: vm
  location: location
  tags: tags
  properties: {
    publisher: 'Microsoft.Azure.Monitor'
    type: 'AzureMonitorLinuxAgent'
    typeHandlerVersion: '1.29'
    autoUpgradeMinorVersion: true
    enableAutomaticUpgrade: true
  }
}

// Platform metrics (CPU %, etc.) to Log Analytics; works without any guest agent.
resource metricsDiag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (!empty(logAnalyticsWorkspaceResourceId)) {
  name: 'vm-platform-metrics'
  scope: vm
  properties: {
    workspaceId: logAnalyticsWorkspaceResourceId
    metrics: [{ category: 'AllMetrics', enabled: true }]
  }
}

output vmResourceId string = vm.id
output principalId string = vm.identity.principalId
