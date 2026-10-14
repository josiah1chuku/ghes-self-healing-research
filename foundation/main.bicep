// Step 1 - Foundation: network, Bastion, Key Vault, CMK, disk encryption set.
// Deploy once, before iac/main.bicep.
targetScope = 'subscription'

param location string = 'eastus'
param networkRgName string = 'ghes-research-network-rg'
param securityRgName string = 'ghes-research-security-rg'

@description('CIDR ranges allowed to reach GHES on 443/22 (your corporate or VPN range). Do not use 0.0.0.0/0 or Internet.')
param clientSourcePrefixes array

@description('Deploy Azure Bastion (Standard) for admin access to ports 122 and 8443.')
param deployBastion bool = true

param tags object = {
  Project: 'GHES-Self-Healing-Research'
  Paper: 'ICPADS-2026'
}

resource networkRg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: networkRgName
  location: location
  tags: tags
}

resource securityRg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: securityRgName
  location: location
  tags: tags
}

module network './modules/network.bicep' = {
  name: 'foundation-network'
  scope: networkRg
  params: {
    location: location
    clientSourcePrefixes: clientSourcePrefixes
    deployBastion: deployBastion
    tags: tags
  }
}

module security './modules/security.bicep' = {
  name: 'foundation-security'
  scope: securityRg
  params: {
    location: location
    tags: tags
  }
}

output vnetId string = network.outputs.vnetId
output devSubnetId string = network.outputs.devSubnetId
output testSubnetId string = network.outputs.testSubnetId
output prodSubnetId string = network.outputs.prodSubnetId
output diskEncryptionSetId string = security.outputs.diskEncryptionSetId
output keyVaultName string = security.outputs.keyVaultName
