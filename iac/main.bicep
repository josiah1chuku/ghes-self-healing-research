targetScope = 'subscription'

@description('Environment name')
@allowed(['dev', 'test', 'prod'])
param environmentName string

@description('Resource group name')
param resourceGroupName string

@description('Azure region')
param location string = 'eastus'

@description('VM name')
param vmName string

@description('VM SKU')
param vmSize string

@minValue(128)
param osDiskSizeGB int

@minValue(256)
param dataDiskSizeGB int

param imagePublisher string
param imageOffer string
param imageSku string
param imageVersion string

param nicName string
param subnetId string
param privateIpAddress string

@allowed(['Static', 'Dynamic'])
param privateIpAllocationMethod string = 'Static'

param enablePublicIp bool = false
param enableCmkEncryption bool = false
param diskEncryptionSetId string = ''
param bootDiagnosticsStorageUri string = ''

@secure()
param adminUsername string

@secure()
param adminPassword string

param keyVaultName string = ''
param enableMonitoring bool = true

@minValue(30)
@maxValue(730)
param logRetentionDays int = 90

param tags object

resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module monitoring './modules/monitoring.bicep' = if (enableMonitoring) {
  name: 'monitoring-${environmentName}'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    logRetentionDays: logRetentionDays
    tags: tags
  }
}

module network './modules/network.bicep' = {
  name: 'network-${environmentName}'
  scope: rg
  params: {
    nicName: nicName
    subnetId: subnetId
    privateIpAddress: privateIpAddress
    allocationMethod: privateIpAllocationMethod
    enablePublicIp: enablePublicIp
    location: location
    tags: tags
  }
}

module vm './modules/vm.bicep' = {
  name: 'vm-${environmentName}'
  scope: rg
  dependsOn: [network, monitoring]
  params: {
    vmName: vmName
    vmSize: vmSize
    location: location
    osDiskSizeGB: osDiskSizeGB
    dataDiskSizeGB: dataDiskSizeGB
    imagePublisher: imagePublisher
    imageOffer: imageOffer
    imageSku: imageSku
    imageVersion: imageVersion
    nicId: network.outputs.nicId
    adminUsername: adminUsername
    adminPassword: adminPassword
    enableCmkEncryption: enableCmkEncryption
    diskEncryptionSetId: diskEncryptionSetId
    bootDiagnosticsStorageUri: bootDiagnosticsStorageUri
    logAnalyticsWorkspaceId: enableMonitoring ? monitoring.outputs.workspaceId : ''
    tags: tags
  }
}

output privateIp string = network.outputs.privateIp
output publicIp string = network.outputs.publicIp
output vmResourceId string = vm.outputs.vmResourceId
output nicResourceId string = network.outputs.nicId
output principalId string = vm.outputs.principalId
output logAnalyticsWorkspaceId string = enableMonitoring ? monitoring.outputs.workspaceId : ''
output logAnalyticsResourceId string = enableMonitoring ? monitoring.outputs.workspaceResourceId : ''
