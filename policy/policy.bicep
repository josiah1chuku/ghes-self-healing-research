// Step 5 - Azure Policy (Deny) so the "no public IP" control also holds for out-of-band changes.
// Deploy at resource-group scope:  az deployment group create -g <env-rg> -f policy.bicep
targetScope = 'resourceGroup'

// Built-in: "Network interfaces should not have public IPs". Confirm the ID in your tenant with:
//   az policy definition show --name 83a86a26-fd1f-447c-b59d-e51f44264114 --query displayName
var noPublicIpPolicyId = '/providers/Microsoft.Authorization/policyDefinitions/83a86a26-fd1f-447c-b59d-e51f44264114'

resource denyPublicIp 'Microsoft.Authorization/policyAssignments@2024-04-01' = {
  name: 'deny-nic-public-ip'
  properties: {
    displayName: 'GHES: deny public IPs on network interfaces'
    policyDefinitionId: noPublicIpPolicyId
    enforcementMode: 'Default'
  }
}
