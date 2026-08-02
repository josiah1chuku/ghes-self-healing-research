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
@secure()
param adminUsername string
@secure()
param adminPassword string
param enableCmkEncryption bool = false
param diskEncryptionSetId string = ''
param bootDiagnosticsStorageUri string = ''
param logAnalyticsWorkspaceId string = ''
param tags object

resource dataDisk 'Microsoft.Compute/disks@2023-04-02' = {
  name: '${vmName}-data-disk'
  location: location
  tags: tags
  sku: { name: 'Premium_LRS' }
  properties: {
    diskSizeGB: dataDiskSizeGB
    creationData: { createOption: 'Empty' }
    encryption: enableCmkEncryption ? {
      type: 'EncryptionAtRestWithCustomerKey'
      diskEncryptionSetId: diskEncryptionSetId
    } : {
      type: 'EncryptionAtRestWithPlatformKey'
    }
    networkAccessPolicy: 'AllowAll'
    publicNetworkAccess: 'Enabled'
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
          diskEncryptionSet: enableCmkEncryption ? { id: diskEncryptionSetId } : null
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
          managedDisk: {
            id: dataDisk.id
            storageAccountType: 'Premium_LRS'
            diskEncryptionSet: enableCmkEncryption ? { id: diskEncryptionSetId } : null
          }
        }
      ]
    }
    osProfile: {
      computerName: vmName
      adminUsername: adminUsername
      adminPassword: adminPassword
      linuxConfiguration: {
        disablePasswordAuthentication: false
        patchSettings: {
          patchMode: 'AutomaticByPlatform'
          assessmentMode: 'AutomaticByPlatform'
          automaticByPlatformSettings: { rebootSetting: 'IfRequired' }
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
    diagnosticsProfile: {
      bootDiagnostics: {
        enabled: true
        storageUri: empty(bootDiagnosticsStorageUri) ? null : bootDiagnosticsStorageUri
      }
    }
    securityProfile: {
      securityType: 'TrustedLaunch'
      uefiSettings: { secureBootEnabled: true, vTpmEnabled: true }
      encryptionAtHost: true
    }
    priority: 'Regular'
  }
}

resource amaExtension 'Microsoft.Compute/virtualMachines/extensions@2023-09-01' = if (!empty(logAnalyticsWorkspaceId)) {
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

output vmResourceId string = vm.id
output principalId string = vm.identity.principalId
