#!/bin/bash
# ---------------------------------------------------------------------------
# Deploy the hackathon VM infrastructure
# ---------------------------------------------------------------------------
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

RESOURCE_GROUP="rg-user-vm"
LOCATION="ukwest"
BASE_NAME="hack"

# ---------------------------------------------------------------------------
# Sub-command: base — deploy shared VNet + NSG (run once)
# ---------------------------------------------------------------------------
deploy_base() {
  echo "Deploying base infrastructure to resource group: $RESOURCE_GROUP"

  az deployment group create \
    --resource-group "$RESOURCE_GROUP" \
    --name "deploy-base" \
    --template-file base.bicep \
    --parameters \
      location="$LOCATION" \
      baseName="$BASE_NAME" \
    --output none

  echo "✔ Base infrastructure deployed."
}

# ---------------------------------------------------------------------------
# Sub-command: vm — deploy a per-user VM
# ---------------------------------------------------------------------------
deploy_vm() {
  local USER_NAME="${1:?Usage: $0 vm <user-name> <ssh-public-key>}"
  local SSH_PUBLIC_KEY="${2:?Usage: $0 vm <user-name> <ssh-public-key>}"

  if [[ ! "$SSH_PUBLIC_KEY" =~ ^ssh- ]]; then
    echo "Error: SSH public key should start with 'ssh-ed25519' or 'ssh-rsa'."
    exit 1
  fi

  echo "Deploying VM for user '$USER_NAME' to resource group: $RESOURCE_GROUP"

  az deployment group create \
    --resource-group "$RESOURCE_GROUP" \
    --name "deploy-${USER_NAME}" \
    --template-file vm.bicep \
    --parameters \
      location="$LOCATION" \
      baseName="$BASE_NAME" \
      userName="$USER_NAME" \
      sshPublicKey="$SSH_PUBLIC_KEY" \
    --output none

  PUBLIC_IP=$(az deployment group show \
    --resource-group "$RESOURCE_GROUP" \
    --name "deploy-${USER_NAME}" \
    --query "properties.outputs.publicIpAddress.value" \
    --output tsv)

  echo "✔ Deployment complete."
  echo ""
  echo "  VM:  vm-${BASE_NAME}-${USER_NAME}"
  echo "  IP:  ${PUBLIC_IP}"
  echo "  SSH: ssh devuser@${PUBLIC_IP}"
}

# ---------------------------------------------------------------------------
# Main dispatcher
# ---------------------------------------------------------------------------
COMMAND="${1:?Usage: $0 <base|vm> [args...]}"
shift

case "$COMMAND" in
  base) deploy_base ;;
  vm)   deploy_vm "$@" ;;
  *)
    echo "Unknown command: $COMMAND"
    echo "Usage: $0 <base|vm> [args...]"
    exit 1
    ;;
esac
