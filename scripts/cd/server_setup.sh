#!/usr/bin/env bash
# =============================================================================
# server_setup.sh — Remote Server Bootstrap Script
# =============================================================================
# Automates: root hardening, deployer user setup, GitHub Actions runner,
# SSH key generation, Nginx + Certbot, and project directory provisioning.
# =============================================================================

set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────────
# COLORS
# ──────────────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

log()     { echo -e "${GREEN}[✔]${NC} $*"; }
warn()    { echo -e "${YELLOW}[!]${NC} $*"; }
section() { echo -e "\n${CYAN}${BOLD}── $* ──${NC}"; }
error()   { echo -e "${RED}[✘] ERROR:${NC} $*" >&2; exit 1; }

# ──────────────────────────────────────────────────────────────────────────────
# MUST RUN AS ROOT
# ──────────────────────────────────────────────────────────────────────────────
[[ "$EUID" -ne 0 ]] && error "Please run this script as root (or with sudo)."

# ──────────────────────────────────────────────────────────────────────────────
# COLLECT ALL VARIABLES UPFRONT
# ──────────────────────────────────────────────────────────────────────────────
section "Configuration — please answer all prompts before the script runs"

# Root password
echo -e "${BOLD}[1/8] New ROOT password:${NC}"
read -rsp "      Enter new root password: " ROOT_PASS; echo
read -rsp "      Confirm root password:   " ROOT_PASS_CONFIRM; echo
[[ "$ROOT_PASS" == "$ROOT_PASS_CONFIRM" ]] || error "Root passwords do not match."

# Deployer password
echo -e "\n${BOLD}[2/8] DEPLOYER user password:${NC}"
read -rsp "      Enter deployer password: " DEPLOYER_PASS; echo
read -rsp "      Confirm deployer password: " DEPLOYER_PASS_CONFIRM; echo
[[ "$DEPLOYER_PASS" == "$DEPLOYER_PASS_CONFIRM" ]] || error "Deployer passwords do not match."

# SSH key email
echo -e "\n${BOLD}[3/8] Email for SSH key comments:${NC}"
read -rp "      Email (e.g. billy@sbn.systems): " SSH_EMAIL
[[ -z "$SSH_EMAIL" ]] && error "SSH email cannot be empty."

# GitHub runner token
echo -e "\n${BOLD}[4/8] GitHub Actions Runner:${NC}"
read -rp "      GitHub repo URL (e.g. https://github.com/ORG/REPO): " GITHUB_REPO_URL
[[ -z "$GITHUB_REPO_URL" ]] && error "GitHub repo URL cannot be empty."
read -rp "      Runner registration token: " RUNNER_TOKEN
[[ -z "$RUNNER_TOKEN" ]] && error "Runner token cannot be empty."
read -rp "      Runner label (e.g. self-hosted-maps): " RUNNER_LABEL
[[ -z "$RUNNER_LABEL" ]] && error "Runner label cannot be empty."

# Project name
echo -e "\n${BOLD}[5/8] Project name (used for /opt/<project>):${NC}"
read -rp "      Project name (e.g. maps): " PROJECT_NAME
[[ -z "$PROJECT_NAME" ]] && error "Project name cannot be empty."

# Domain & email for Certbot
echo -e "\n${BOLD}[6/8] Domain configuration (Certbot/Nginx):${NC}"
read -rp "      Primary domain (e.g. maps.bkatalayi.com): " DOMAIN
[[ -z "$DOMAIN" ]] && error "Domain cannot be empty."
read -rp "      Certbot email (e.g. deployer@bkatalayi.com): " DOMAIN_EMAIL
[[ -z "$DOMAIN_EMAIL" ]] && error "Certbot email cannot be empty."

# Local machine public key
echo -e "\n${BOLD}[7/8] Your LOCAL machine's public SSH key:${NC}"
echo -e "      ${YELLOW}This is added to deployer's authorized_keys so you can SSH in as deployer.${NC}"
echo -e "      Run: cat ~/.ssh/id_ed25519.pub  (on your local machine) and paste it here."
read -rp "      Paste your local public key: " LOCAL_PUBLIC_KEY
[[ -z "$LOCAL_PUBLIC_KEY" ]] && error "Local public key cannot be empty — you would be locked out after root login is disabled."
[[ "$LOCAL_PUBLIC_KEY" == ssh-* || "$LOCAL_PUBLIC_KEY" == ecdsa-* ]] || \
  error "Invalid SSH public key format (should start with ssh-ed25519, ssh-rsa, ecdsa-sha2-*, etc.)."

# GitHub runner version
echo -e "\n${BOLD}[8/8] GitHub Actions runner version:${NC}"
read -rp "      Runner version [default: 2.332.0]: " RUNNER_VERSION
RUNNER_VERSION="${RUNNER_VERSION:-2.332.0}"

# ──────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ──────────────────────────────────────────────────────────────────────────────
echo -e "\n${CYAN}${BOLD}════════════════════════════════════════${NC}"
echo -e "${BOLD} Setup Summary${NC}"
echo -e "${CYAN}${BOLD}════════════════════════════════════════${NC}"
echo -e "  Deployer user  : deployer"
echo -e "  SSH key email  : ${SSH_EMAIL}"
echo -e "  Local pub key  : ${LOCAL_PUBLIC_KEY:0:60}…"
echo -e "  GitHub repo    : ${GITHUB_REPO_URL}"
echo -e "  Runner label   : ${RUNNER_LABEL}"
echo -e "  Runner version : ${RUNNER_VERSION}"
echo -e "  Project name   : ${PROJECT_NAME}  →  /opt/${PROJECT_NAME}"
echo -e "  Domain         : ${DOMAIN}  (+ www.${DOMAIN})"
echo -e "  Certbot email  : ${DOMAIN_EMAIL}"
echo -e "${CYAN}${BOLD}════════════════════════════════════════${NC}"

read -rp $'\nProceed with setup? [y/N]: ' CONFIRM
[[ "${CONFIRM,,}" == "y" ]] || { warn "Aborted by user."; exit 0; }

# ──────────────────────────────────────────────────────────────────────────────
# STEP 1 — Change root password
# ──────────────────────────────────────────────────────────────────────────────
section "Step 1 — Changing root password"
echo "root:${ROOT_PASS}" | chpasswd
log "Root password updated."

# ──────────────────────────────────────────────────────────────────────────────
# STEP 2 — System update (first pass)
# ──────────────────────────────────────────────────────────────────────────────
section "Step 2 — System update & upgrade (pass 1)"
apt update && apt upgrade -y
log "System updated."

# ──────────────────────────────────────────────────────────────────────────────
# STEP 3 & 4 — Harden SSH
# ──────────────────────────────────────────────────────────────────────────────
section "Steps 3 & 4 — Hardening SSH configuration"
SSHD_CONFIG="/etc/ssh/sshd_config"

# Back up original
cp "${SSHD_CONFIG}" "${SSHD_CONFIG}.bak.$(date +%Y%m%d%H%M%S)"

# Apply settings (idempotent: replace if exists, append if not)
_sshd_set() {
  local key="$1" val="$2"
  if grep -qE "^#?[[:space:]]*${key}" "${SSHD_CONFIG}"; then
    sed -i "s|^#\?[[:space:]]*${key}.*|${key} ${val}|" "${SSHD_CONFIG}"
  else
    echo "${key} ${val}" >> "${SSHD_CONFIG}"
  fi
}

_sshd_set "PasswordAuthentication"          "no"
_sshd_set "ChallengeResponseAuthentication" "no"
_sshd_set "PermitRootLogin"                 "no"

systemctl reload sshd 2>/dev/null || systemctl reload ssh 2>/dev/null || \
  systemctl restart sshd 2>/dev/null || systemctl restart ssh 2>/dev/null || \
  warn "Could not reload SSH daemon — config changes will apply after reboot."
log "SSH hardened: PasswordAuthentication=no, ChallengeResponseAuthentication=no, PermitRootLogin=no"
warn "Ensure your SSH key is in authorized_keys BEFORE your next login!"

# ──────────────────────────────────────────────────────────────────────────────
# STEP 5 — System update (second pass)
# ──────────────────────────────────────────────────────────────────────────────
section "Step 5 — System update & upgrade (pass 2)"
apt update && apt upgrade -y
log "System updated again."

# ──────────────────────────────────────────────────────────────────────────────
# STEP 6–9 — Create deployer user
# ──────────────────────────────────────────────────────────────────────────────
section "Steps 6–9 — Creating deployer user"

if id "deployer" &>/dev/null; then
  warn "User 'deployer' already exists — skipping creation."
else
  useradd -m -s /bin/bash deployer
  log "User 'deployer' created."
fi

echo "deployer:${DEPLOYER_PASS}" | chpasswd
usermod -aG sudo deployer

# Sudoers entry (idempotent)
SUDOERS_LINE="deployer ALL=(ALL) NOPASSWD:ALL"
if ! grep -qF "$SUDOERS_LINE" /etc/sudoers; then
  echo "$SUDOERS_LINE" >> /etc/sudoers
fi

log "Deployer added to sudo and granted NOPASSWD sudoers entry."

# ──────────────────────────────────────────────────────────────────────────────
# STEPS 10–14 — SSH keys for deployer
# ──────────────────────────────────────────────────────────────────────────────
section "Steps 10–14 — Generating SSH keys for deployer"

DEPLOYER_HOME="/home/deployer"
SSH_DIR="${DEPLOYER_HOME}/.ssh"

# Create dir as root, then immediately hand ownership to deployer
# so that ssh-keygen (run as deployer) can write into it
mkdir -p "${SSH_DIR}"
chown deployer:deployer "${SSH_DIR}"
chmod 700 "${SSH_DIR}"

# ed25519 key
if [[ ! -f "${SSH_DIR}/id_ed25519" ]]; then
  sudo -u deployer ssh-keygen -t ed25519 -C "${SSH_EMAIL}" \
    -f "${SSH_DIR}/id_ed25519" -N ""
  log "ed25519 key generated."
else
  warn "ed25519 key already exists — skipping."
fi

# RSA 4096 key
if [[ ! -f "${SSH_DIR}/id_rsa" ]]; then
  sudo -u deployer ssh-keygen -t rsa -b 4096 -C "${SSH_EMAIL}" \
    -f "${SSH_DIR}/id_rsa" -N ""
  log "RSA 4096 key generated."
else
  warn "RSA key already exists — skipping."
fi

# Add to authorized_keys (idempotent)
touch "${SSH_DIR}/authorized_keys"
chown deployer:deployer "${SSH_DIR}/authorized_keys"
chmod 600 "${SSH_DIR}/authorized_keys"

for pubkey in "${SSH_DIR}/id_ed25519.pub" "${SSH_DIR}/id_rsa.pub"; do
  key_content=$(cat "$pubkey")
  if ! grep -qF "$key_content" "${SSH_DIR}/authorized_keys"; then
    echo "$key_content" >> "${SSH_DIR}/authorized_keys"
    log "Added $(basename $pubkey) to authorized_keys."
  else
    warn "$(basename $pubkey) already in authorized_keys."
  fi
done

# Add the operator's local machine public key so they can SSH in as deployer
if ! grep -qF "$LOCAL_PUBLIC_KEY" "${SSH_DIR}/authorized_keys"; then
  echo "$LOCAL_PUBLIC_KEY" >> "${SSH_DIR}/authorized_keys"
  log "Local machine public key added to deployer's authorized_keys."
else
  warn "Local machine public key already present in authorized_keys."
fi

# Final ownership sweep to be safe
chown -R deployer:deployer "${SSH_DIR}"
log "SSH directory permissions set."

# ──────────────────────────────────────────────────────────────────────────────
# STEP 12 — System update as deployer context (still root)
# ──────────────────────────────────────────────────────────────────────────────
section "Step 12 — System update & upgrade (pass 3)"
apt update && apt upgrade -y
log "System updated."

# ──────────────────────────────────────────────────────────────────────────────
# STEP 15 — GitHub Actions Runner
# ──────────────────────────────────────────────────────────────────────────────
section "Step 15 — Installing GitHub Actions Runner"

RUNNER_DIR="${DEPLOYER_HOME}/actions-runner"
RUNNER_ARCHIVE="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
RUNNER_URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${RUNNER_ARCHIVE}"

sudo -u deployer mkdir -p "${RUNNER_DIR}"
cd "${RUNNER_DIR}"

if [[ ! -f "${RUNNER_ARCHIVE}" ]]; then
  sudo -u deployer curl -o "${RUNNER_ARCHIVE}" -L "${RUNNER_URL}"
  log "Runner archive downloaded."
fi

sudo -u deployer tar xzf "${RUNNER_ARCHIVE}"
log "Runner extracted."

# Configure runner
sudo -u deployer bash -c "cd ${RUNNER_DIR} && ./config.sh \
  --url ${GITHUB_REPO_URL} \
  --token ${RUNNER_TOKEN} \
  --labels ${RUNNER_LABEL} \
  --unattended \
  --replace"

log "Runner configured."

# Install and start as service
bash "${RUNNER_DIR}/svc.sh" install deployer
bash "${RUNNER_DIR}/svc.sh" start

# Enable the systemd service (wildcard match)
RUNNER_SERVICE=$(systemctl list-unit-files | grep 'actions.runner' | awk '{print $1}' | head -n1)
if [[ -n "$RUNNER_SERVICE" ]]; then
  systemctl enable "$RUNNER_SERVICE"
  log "Runner service enabled: ${RUNNER_SERVICE}"
fi

log "Runner service status:"
systemctl status actions.runner.* --no-pager || true

chown -R deployer:deployer "${RUNNER_DIR}"
log "Runner directory ownership set to deployer."

cd "${DEPLOYER_HOME}"

# ──────────────────────────────────────────────────────────────────────────────
# STEP 16–17 — Project directory
# ──────────────────────────────────────────────────────────────────────────────
section "Steps 16–17 — Creating project directory /opt/${PROJECT_NAME}"

mkdir -p "/opt/${PROJECT_NAME}"
chown -R deployer:deployer "/opt/${PROJECT_NAME}"
log "Project directory /opt/${PROJECT_NAME} created and owned by deployer."

# ──────────────────────────────────────────────────────────────────────────────
# STEP 18 — Install Docker CE (official repository)
# ──────────────────────────────────────────────────────────────────────────────
section "Step 18 — Installing Docker CE"

# Remove any conflicting legacy packages
for pkg in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do
  apt remove -y "$pkg" 2>/dev/null || true
done

# Install prerequisites
apt install -y ca-certificates curl gnupg

# Add Docker's official GPG key
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

# Add Docker apt repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | tee /etc/apt/sources.list.d/docker.list > /dev/null

apt update
apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Enable and start Docker
systemctl enable docker
systemctl start docker

# Add deployer to the docker group so it can run docker without sudo
usermod -aG docker deployer
log "Docker CE installed and deployer added to docker group."
log "Docker version: $(docker --version)"

# ──────────────────────────────────────────────────────────────────────────────
# STEP 19–20 — Certbot / Nginx SSL
# ──────────────────────────────────────────────────────────────────────────────
section "Steps 19–20 — Installing Certbot and obtaining SSL certificate"

apt install -y certbot python3-certbot-nginx
log "Certbot installed."

certbot --nginx \
  -d "${DOMAIN}" \
  -d "www.${DOMAIN}" \
  --non-interactive \
  --agree-tos \
  -m "${DOMAIN_EMAIL}" \
  --redirect

log "SSL certificate obtained for ${DOMAIN} and www.${DOMAIN}."

# ──────────────────────────────────────────────────────────────────────────────
# STEP 20 — Disable Nginx (managed externally / by runner)
# ──────────────────────────────────────────────────────────────────────────────
section "Step 20 — Disabling Nginx service"

systemctl stop nginx    || warn "nginx was not running."
systemctl disable nginx || warn "nginx was not enabled."
update-rc.d -f nginx disable 2>/dev/null || warn "update-rc.d skipped (systemd-only system)."

log "Nginx stopped and disabled."

# ──────────────────────────────────────────────────────────────────────────────
# DONE — Print public SSH keys before reboot
# ──────────────────────────────────────────────────────────────────────────────
section "Setup Complete — Deployer Public SSH Keys"
echo -e "${YELLOW}Save these keys if you need them externally (e.g. add to GitHub):${NC}\n"
echo -e "${BOLD}── id_ed25519.pub ──${NC}"
cat "${SSH_DIR}/id_ed25519.pub"
echo -e "\n${BOLD}── id_rsa.pub ──${NC}"
cat "${SSH_DIR}/id_rsa.pub"

echo -e "\n${GREEN}${BOLD}All steps completed successfully!${NC}"
warn "The server will reboot in 10 seconds. Press Ctrl+C to cancel."
sleep 10

# ──────────────────────────────────────────────────────────────────────────────
# STEP 21 — Reboot
# ──────────────────────────────────────────────────────────────────────────────
section "Step 21 — Rebooting"
reboot now
