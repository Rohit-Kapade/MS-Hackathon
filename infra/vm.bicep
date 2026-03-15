// ---------------------------------------------------------------------------
// Per-user VM — dev container-ready Ubuntu VM with auto-shutdown
// ---------------------------------------------------------------------------

@description('Azure region for all resources')
param location string

@description('VM administrator username')
param adminUsername string = 'devuser'

@description('SSH public key for the admin user')
param sshPublicKey string

@description('VM size')
param vmSize string = 'Standard_D8s_v3'

@description('Base name used for shared resources (VNet, NSG)')
param baseName string

@description('User name to personalize the VM (e.g. firstname). Used in VM, NIC, PIP, and disk names.')
param userName string

// ---------------------------------------------------------------------------
// References to existing base infrastructure
// ---------------------------------------------------------------------------

resource vnet 'Microsoft.Network/virtualNetworks@2023-11-01' existing = {
  name: 'vnet-${baseName}'
}

resource nsg 'Microsoft.Network/networkSecurityGroups@2023-11-01' existing = {
  name: 'nsg-${baseName}-vm'
}

// ---------------------------------------------------------------------------
// Public IP
// ---------------------------------------------------------------------------

resource pip 'Microsoft.Network/publicIPAddresses@2023-11-01' = {
  name: 'pip-${baseName}-${userName}'
  location: location
  sku: {
    name: 'Standard'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

// ---------------------------------------------------------------------------
// Network Interface
// ---------------------------------------------------------------------------

resource nic 'Microsoft.Network/networkInterfaces@2023-11-01' = {
  name: 'nic-${baseName}-${userName}'
  location: location
  properties: {
    networkSecurityGroup: {
      id: nsg.id
    }
    ipConfigurations: [
      {
        name: 'ipconfig1'
        properties: {
          subnet: {
            id: vnet.properties.subnets[0].id
          }
          privateIPAllocationMethod: 'Dynamic'
          publicIPAddress: {
            id: pip.id
          }
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Virtual Machine
// ---------------------------------------------------------------------------

resource vm 'Microsoft.Compute/virtualMachines@2024-03-01' = {
  name: 'vm-${baseName}-${userName}'
  location: location
  properties: {
    hardwareProfile: {
      vmSize: vmSize
    }
    osProfile: {
      computerName: 'vm-${userName}'
      adminUsername: adminUsername
      linuxConfiguration: {
        disablePasswordAuthentication: true
        ssh: {
          publicKeys: [
            {
              path: '/home/${adminUsername}/.ssh/authorized_keys'
              keyData: sshPublicKey
            }
          ]
        }
      }
    }
    storageProfile: {
      imageReference: {
        publisher: 'Canonical'
        offer: '0001-com-ubuntu-server-jammy'
        sku: '22_04-lts-gen2'
        version: 'latest'
      }
      osDisk: {
        createOption: 'FromImage'
        diskSizeGB: 128
        managedDisk: {
          storageAccountType: 'Premium_LRS'
        }
      }
    }
    networkProfile: {
      networkInterfaces: [
        {
          id: nic.id
        }
      ]
    }
  }
}

// ---------------------------------------------------------------------------
// Custom Script Extension — install Docker, VS Code CLI, dev tools
// ---------------------------------------------------------------------------

resource vmSetup 'Microsoft.Compute/virtualMachines/extensions@2024-03-01' = {
  parent: vm
  name: 'setup-devcontainer'
  location: location
  properties: {
    publisher: 'Microsoft.Azure.Extensions'
    type: 'CustomScript'
    typeHandlerVersion: '2.1'
    autoUpgradeMinorVersion: true
    settings: {
      script: loadFileAsBase64('../infra/setup-vm.sh')
    }
  }
}

// ---------------------------------------------------------------------------
// Auto-shutdown at 18:30 UK time
// ---------------------------------------------------------------------------

resource autoShutdown 'Microsoft.DevTestLab/schedules@2018-09-15' = {
  name: 'shutdown-computevm-${vm.name}'
  location: location
  properties: {
    status: 'Enabled'
    taskType: 'ComputeVmShutdownTask'
    dailyRecurrence: {
      time: '1830'
    }
    timeZoneId: 'GMT Standard Time'
    targetResourceId: vm.id
    notificationSettings: {
      status: 'Disabled'
    }
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output vmName string = vm.name
output publicIpAddress string = pip.properties.ipAddress
output sshCommand string = 'ssh ${adminUsername}@${pip.properties.ipAddress}'
