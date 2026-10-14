targetScope = 'resourceGroup'

param location string
param clientSourcePrefixes array
param deployBastion bool = true
param tags object

var vnetName = 'ghes-research-vnet'
var bastionPrefix = '10.1.250.0/26'

// Least-privilege NSG for the GHES subnets. Ports follow GitHub's GHES network-ports guidance:
// 443 (web/API), 22 (Git over SSH), 122 (admin SSH), 8443 (management console).
resource nsg 'Microsoft.Network/networkSecurityGroups@2023-11-01' = {
  name: 'ghes-research-nsg'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'Allow-HTTPS-Clients'
        properties: {
          priority: 100, direction: 'Inbound', access: 'Allow', protocol: 'Tcp'
          sourceAddressPrefixes: clientSourcePrefixes, sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork', destinationPortRange: '443'
        }
      }
      {
        name: 'Allow-GitSSH-Clients'
        properties: {
          priority: 110, direction: 'Inbound', access: 'Allow', protocol: 'Tcp'
          sourceAddressPrefixes: clientSourcePrefixes, sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork', destinationPortRange: '22'
        }
      }
      {
        name: 'Allow-AdminSSH-FromBastion'
        properties: {
          priority: 120, direction: 'Inbound', access: 'Allow', protocol: 'Tcp'
          sourceAddressPrefix: bastionPrefix, sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork', destinationPortRange: '122'
        }
      }
      {
        name: 'Allow-MgmtConsole-FromBastion'
        properties: {
          priority: 130, direction: 'Inbound', access: 'Allow', protocol: 'Tcp'
          sourceAddressPrefix: bastionPrefix, sourcePortRange: '*'
          destinationAddressPrefix: 'VirtualNetwork', destinationPortRange: '8443'
        }
      }
      {
        name: 'Deny-All-Inbound'
        properties: {
          priority: 4000, direction: 'Inbound', access: 'Deny', protocol: '*'
          sourceAddressPrefix: '*', sourcePortRange: '*'
          destinationAddressPrefix: '*', destinationPortRange: '*'
        }
      }
      {
        name: 'Allow-AzurePlatform-Outbound'
        properties: {
          priority: 100, direction: 'Outbound', access: 'Allow', protocol: '*'
          sourceAddressPrefix: '*', sourcePortRange: '*'
          destinationAddressPrefix: '168.63.129.16/32', destinationPortRange: '*'
        }
      }
      {
        name: 'Allow-HTTPS-Outbound'
        properties: {
          priority: 110, direction: 'Outbound', access: 'Allow', protocol: 'Tcp'
          sourceAddressPrefix: '*', sourcePortRange: '*'
          destinationAddressPrefix: 'Internet', destinationPortRange: '443'
        }
      }
      {
        name: 'Allow-NTP-Outbound'
        properties: {
          priority: 120, direction: 'Outbound', access: 'Allow', protocol: 'Udp'
          sourceAddressPrefix: '*', sourcePortRange: '*'
          destinationAddressPrefix: 'Internet', destinationPortRange: '123'
        }
      }
      {
        name: 'Deny-All-Outbound'
        properties: {
          priority: 4000, direction: 'Outbound', access: 'Deny', protocol: '*'
          sourceAddressPrefix: '*', sourcePortRange: '*'
          destinationAddressPrefix: '*', destinationPortRange: '*'
        }
      }
    ]
  }
}

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: vnetName
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: ['10.1.0.0/16'] }
    subnets: concat([
      {
        name: 'dev-subnet'
        properties: { addressPrefix: '10.1.1.0/24', networkSecurityGroup: { id: nsg.id }, defaultOutboundAccess: false }
      }
      {
        name: 'test-subnet'
        properties: { addressPrefix: '10.1.2.0/24', networkSecurityGroup: { id: nsg.id }, defaultOutboundAccess: false }
      }
      {
        name: 'prod-subnet'
        properties: { addressPrefix: '10.1.3.0/24', networkSecurityGroup: { id: nsg.id }, defaultOutboundAccess: false }
      }
    ], deployBastion ? [
      {
        name: 'AzureBastionSubnet'
        properties: { addressPrefix: bastionPrefix }
      }
    ] : [])
  }
}

resource bastionPip 'Microsoft.Network/publicIPAddresses@2023-11-01' = if (deployBastion) {
  name: 'ghes-research-bastion-pip'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static' }
}

// Standard SKU with tunneling is required to reach custom ports (122, 8443).
resource bastion 'Microsoft.Network/bastionHosts@2023-11-01' = if (deployBastion) {
  name: 'ghes-research-bastion'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: {
    enableTunneling: true
    disableCopyPaste: true
    ipConfigurations: [
      {
        name: 'bastion-ipconfig'
        properties: {
          subnet: { id: '${vnet.id}/subnets/AzureBastionSubnet' }
          publicIPAddress: { id: bastionPip.id }
        }
      }
    ]
  }
}

output vnetId string = vnet.id
output devSubnetId string = '${vnet.id}/subnets/dev-subnet'
output testSubnetId string = '${vnet.id}/subnets/test-subnet'
output prodSubnetId string = '${vnet.id}/subnets/prod-subnet'
