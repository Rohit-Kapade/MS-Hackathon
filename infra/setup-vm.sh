#!/bin/bash
# ---------------------------------------------------------------------------
# VM bootstrap — installs Docker, VS Code CLI, and dev tools
# Runs as root via the Azure Custom Script Extension.
# ---------------------------------------------------------------------------
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive

ADMIN_USER="devuser"

# ── System packages ────────────────────────────────────────────────────────
apt-get update
apt-get install -y \
  ca-certificates curl gnupg lsb-release git jq unzip build-essential

# ── Docker (official Ubuntu instructions) ──────────────────────────────────
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

tee /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}")
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
EOF

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Let the admin user run docker without sudo
usermod -aG docker "$ADMIN_USER"
newgrp docker

# ── SSH agent — auto-start on login so keys are forwarded into Dev Containers
ADMIN_HOME="/home/$ADMIN_USER"
BASHRC="$ADMIN_HOME/.bashrc"

cat >> "$BASHRC" <<'SSHAGENT'

# Start ssh-agent automatically if not already running
if [ -z "$SSH_AUTH_SOCK" ]; then
  eval "$(ssh-agent -s)" > /dev/null
fi

[ -f ~/.ssh/id_ado ] && ssh-add ~/.ssh/id_ado 2>/dev/null || true
SSHAGENT

# Make sure .ssh dir exists with correct permissions
install -d -m 700 -o "$ADMIN_USER" -g "$ADMIN_USER" "$ADMIN_HOME/.ssh"

# ── Done ───────────────────────────────────────────────────────────────────
echo "✔ VM setup complete. Docker, VS Code CLI, Azure CLI, and uv installed."
