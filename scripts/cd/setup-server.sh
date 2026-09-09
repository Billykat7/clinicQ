#!/usr/bin/env bash
#
# Setup a fresh Ubuntu/Debian server for running the Maps API (Docker, base packages).
# Idempotent where possible: safe to re-run; Docker install is skipped if already present.
#
# Required: run as root or with sudo.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/BTKTechnologies/maps/main/scripts/setup-server.sh | sudo bash
#   # or: scp scripts/setup-server.sh root@<server-ip>:/tmp/ && ssh root@<server-ip> 'bash /tmp/setup-server.sh'
#
# Optional env:
#   SKIP_UFW=1     — do not install or configure ufw
#   CREATE_APP_USER=1 — create non-root user 'mapsapp', dir /opt/maps, .env placeholder
#
set -euo pipefail

# --- Check root/sudo ---
if [[ "$(id -u)" -ne 0 ]]; then
  echo "Error: run as root or with sudo." >&2
  exit 1
fi

echo "[setup-server] System update..."
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq

echo "[setup-server] Installing base packages (curl, git, gnupg)..."
apt-get install -y -qq ca-certificates curl git gnupg

# --- Docker (official Docker CE for Ubuntu/Debian) ---
if command -v docker &>/dev/null; then
  echo "[setup-server] Docker already installed ($(docker --version)); skipping."
else
  echo "[setup-server] Installing Docker (official repo) and docker-compose plugin..."
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  CODENAME="$(. /etc/os-release 2>/dev/null && echo "${VERSION_CODENAME}")"
  CODENAME="${CODENAME:-$(lsb_release -cs 2>/dev/null)}"
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${CODENAME} stable" \
    | tee /etc/apt/sources.list.d/docker.list > /dev/null
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  echo "[setup-server] Docker installed."
fi

# Verify Docker
if ! docker run --rm hello-world &>/dev/null; then
  echo "[setup-server] Warning: docker run hello-world failed; you may need to start the service or log out/in." >&2
fi

# --- UFW (optional) ---
if [[ "${SKIP_UFW:-0}" != "1" ]]; then
  echo "[setup-server] Installing ufw (optional firewall)..."
  apt-get install -y -qq ufw
  # Allow SSH, HTTP, HTTPS; do not enable by default so we don't lock out the user
  ufw default deny incoming 2>/dev/null || true
  ufw allow 22/tcp comment 'SSH' 2>/dev/null || true
  ufw allow 80/tcp comment 'HTTP' 2>/dev/null || true
  ufw allow 443/tcp comment 'HTTPS' 2>/dev/null || true
  echo "[setup-server] ufw installed. Enable when ready: sudo ufw enable"
else
  echo "[setup-server] Skipping ufw (SKIP_UFW=1)."
fi

# --- Optional: app user and directory ---
if [[ "${CREATE_APP_USER:-0}" == "1" ]]; then
  if getent passwd mapsapp &>/dev/null; then
    echo "[setup-server] User mapsapp already exists; skipping."
  else
    echo "[setup-server] Creating user mapsapp and /opt/maps..."
    useradd -r -s /bin/bash -d /opt/maps -m mapsapp
    mkdir -p /opt/maps
    chown mapsapp:mapsapp /opt/maps
    # Allow mapsapp to run docker without sudo (add to docker group)
    usermod -aG docker mapsapp
    # Placeholder .env
    touch /opt/maps/.env
    chown mapsapp:mapsapp /opt/maps/.env
    echo "[setup-server] User mapsapp created. Add your env vars to /opt/maps/.env"
  fi
else
  echo "[setup-server] Skipping app user (set CREATE_APP_USER=1 to create mapsapp and /opt/maps)."
fi

echo "[setup-server] Done. Next: deploy the app (e.g. clone repo and docker-compose up, or run the app image). See docs/INFRASTRUCTURE/SERVER-SETUP.md."
