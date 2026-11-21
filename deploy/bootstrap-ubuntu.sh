#!/usr/bin/env bash
set -euo pipefail

# Run on the EC2 Ubuntu VM. Installs Docker using Docker's official apt repository.
if command -v docker >/dev/null 2>&1 && sudo docker compose version >/dev/null 2>&1; then
  echo "Docker and Compose are already installed."
  exit 0
fi

LYFTR_OS_ID="$(. /etc/os-release && echo "$ID")"
LYFTR_OS_CODENAME="$(. /etc/os-release && echo "$VERSION_CODENAME")"
LYFTR_ARCH="$(dpkg --print-architecture)"
if [ "$LYFTR_OS_ID" != "ubuntu" ]; then
  echo "This installer expects Ubuntu."
  exit 1
fi

sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: $LYFTR_OS_CODENAME
Components: stable
Architectures: $LYFTR_ARCH
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker version
sudo docker compose version
