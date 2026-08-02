targetScope = 'resourceGroup'

param nicName string
param subnetId string
param privateIpAddress string
@allowed(['Static', 'Dynamic'])
param allocationMethod string = 'Static'
param enablePublicIp bool = false
param location string
param tags object

resource publicIpResource 'Microsoft.Network/publicIPAddresses@2023-05-01' = if (enablePublicIp) {
  name: '${nicName}-pip'
  location: location
  tags: tags
  sku: { name: 'Standard' }
  properties: {
    publicIPAllocationMethod: 'Static'
    dnsSettings: {
      domainNameLabel: replace(toLower(nicName), '-', '')
    }
  }
}

resource nic 'Microsoft.Network/networkInterfaces@2023-05-01' = {
  name: nicName
  location: location
  tags: tags
  properties: {
    enableAcceleratedNetworking: true
    enableIPForwarding: false
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          privateIPAddress: privateIpAddress
          privateIPAllocationMethod: allocationMethod
          subnet: { id: subnetId }
          publicIPAddress: enablePublicIp ? { id: publicIpResource.id } : null
        }
      }
    ]
  }
}

output nicId string = nic.id
output privateIp string = nic.properties.ipConfigurations[0].properties.privateIPAddress
output publicIp string = enablePublicIp ? publicIpResource.properties.ipAddress : ''
output fqdn string = enablePublicIp ? publicIpResource.properties.dnsSettings.fqdn : ''
