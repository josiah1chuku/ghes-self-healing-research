targetScope = 'resourceGroup'
param subnetId string

resource pip 'Microsoft.Network/publicIPAddresses@2023-11-01' = {
  name: 'probe2-pip'
  location: resourceGroup().location
  sku: { name: 'Standard' }
  properties: { publicIPAllocationMethod: 'Static' }
}

resource nic 'Microsoft.Network/networkInterfaces@2023-11-01' = {
  name: 'probe2-nic'
  location: resourceGroup().location
  properties: {
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          subnet: { id: subnetId }
          publicIPAddress: { id: pip.id }
        }
      }
    ]
  }
}
