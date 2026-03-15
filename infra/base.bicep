// ---------------------------------------------------------------------------
// Base infrastructure — shared VNet and NSG (deploy once)
// ---------------------------------------------------------------------------

@description('Azure region for all resources')
param location string

@description('Base name used for shared resources (VNet, NSG)')
param baseName string

var allowedSshSources = [
  '45.80.136.161/32'
]

// ---------------------------------------------------------------------------
// Virtual Network
// ---------------------------------------------------------------------------

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: 'vnet-${baseName}'
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.0.0.0/16'
      ]
    }
    subnets: [
      {
        name: 'snet-vms'
        properties: {
          addressPrefix: '10.0.0.0/24'
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Network Security Group — allow SSH inbound
// ---------------------------------------------------------------------------

resource nsg 'Microsoft.Network/networkSecurityGroups@2023-11-01' = {
  name: 'nsg-${baseName}-vm'
  location: location
  properties: {
    securityRules: [
      {
        name: 'Allow-SSH'
        properties: {
          priority: 1000
          direction: 'Inbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourcePortRange: '*'
          destinationPortRange: '22'
          sourceAddressPrefixes: allowedSshSources
          destinationAddressPrefix: '*'
        }
      }
      {
        name: 'Deny-All-Inbound'
        properties: {
          priority: 4096
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourcePortRange: '*'
          destinationPortRange: '*'
          sourceAddressPrefix: '*'
          destinationAddressPrefix: '*'
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Outputs — referenced by vm.bicep
// ---------------------------------------------------------------------------

output vnetName string = vnet.name
output subnetId string = vnet.properties.subnets[0].id
output nsgId string = nsg.id
