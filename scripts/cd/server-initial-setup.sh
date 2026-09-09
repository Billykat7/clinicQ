#!/usr/bin/env bash
#
# Server initial setup: Ubuntu/Debian hardening, Docker, deployer user, SSH keys,
# and GitHub Actions self-hosted runner. Follows common practices used at large-scale
# providers (restrict SSH, least privilege, dedicated deploy user, runner as service).
#
# Required: run as root. Set env vars before running (see below).
#
# Usage:
#   export DEPLOYER_PASSWORD='your-secure-password'
#   export GITHUB_RUNNER_TOKEN='repo-token-from-github-add-runner-ui'
#   sudo bash scripts/cd/server-initial-setup.sh
#
# Optional env:
#   DEPLOYER_PASSWORD       — password for user deployer (required if creating user)
#   GITHUB_RUNNER_TOKEN      — token from repo Settings → Actions → Runners → Add (required for runner)
#   GITHUB_RUNNER_REPO_URL  — default https://github.com/BTKTechnologies/maps
#   RUNNER_VERSION          — default 2.332.0
#   AUTHORIZED_KEY_ED25519  — optional: add this line to deployer's authorized_keys (no default)
#   SKIP_SSH_HARDENING      — set to 1 to skip sshd_config changes (e.g. you still need password)
#   SKIP_RUNNER_SETUP       — set to 1 to skip GitHub Actions runner install
#
set -euo pipefail

# --- Config (override via env) ---
DEPLOYER_USER="${DEPLOYER_USER:-deployer}"
GITHUB_RUNNER_REPO_URL="${GITHUB_RUNNER_REPO_URL:-https://github.com/BTKTechnologies/maps}"
RUNNER_VERSION="${RUNNER_VERSION:-2.332.0}"
RUNNER_LINUX_X64_SHA256="${RUNNER_LINUX_X64_SHA256:-f2094522a6b9afeab07ffb586d1eb3f190b6457074282796c497ce7dce9e0f2a}"
SSH_KEY_COMMENT="${SSH_KEY_COMMENT:-billy@sbn.systems}"

# --- Check root ---
if [[ "$(id -u)" -ne 0 ]]; then
  echo "Error: run as root or with sudo." >&2
  exit 1
fi

echo "[initial-setup] 1/7 System update..."
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get upgrade -y -qq

echo "[initial-setup] 2/7 SSH hardening..."
if [[ "${SKIP_SSH_HARDENING:-0}" != "1" ]]; then
  SSHD_CONFIG="/etc/ssh/sshd_config"
  [[ -f "$SSHD_CONFIG" ]] || { echo "Error: $SSHD_CONFIG not found." >&2; exit 1; }
  cp -a "$SSHD_CONFIG" "${SSHD_CONFIG}.bak.$(date +%Y%m%d%H%M%S)"
  for option in "PasswordAuthentication no" "ChallengeResponseAuthentication no" "KbdInteractiveAuthentication no" "PermitRootLogin no"; do
    key="${option%% *}"
    if grep -qE "^#?\s*${key}\s+" "$SSHD_CONFIG"; then
      sed -i -E "s/^#?\s*${key}\s+.*/$option/" "$SSHD_CONFIG"
    else
      echo "$option" >> "$SSHD_CONFIG"
    fi
  done
  if sshd -t 2>/dev/null; then
    systemctl restart sshd 2>/dev/null || systemctl restart ssh 2>/dev/null || true
    echo "[initial-setup] SSH hardened and service restarted."
  else
    echo "Warning: sshd -t failed; restoring backup. Fix config and restart sshd manually." >&2
    mv "${SSHD_CONFIG}.bak."* "$SSHD_CONFIG"
    exit 1
  fi
else
  echo "[initial-setup] Skipping SSH hardening (SKIP_SSH_HARDENING=1)."
fi

echo "[initial-setup] 3/7 Docker (official repo)..."
apt-get install -y -qq apt-transport-https ca-certificates curl software-properties-common
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
CODENAME="$(. /etc/os-release 2>/dev/null && echo "${VERSION_CODENAME}")"
CODENAME="${CODENAME:-$(lsb_release -cs 2>/dev/null)}"
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${CODENAME} stable" \
  | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update -qq
apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
echo "[initial-setup] Docker installed."

echo "[initial-setup] 4/7 Deployer user and sudo..."
if getent passwd "$DEPLOYER_USER" &>/dev/null; then
  echo "[initial-setup] User $DEPLOYER_USER already exists."
else
  if [[ -z "${DEPLOYER_PASSWORD:-}" ]]; then
    echo "Error: set DEPLOYER_PASSWORD to create user $DEPLOYER_USER." >&2
    exit 1
  fi
  adduser --gecos "" --disabled-password "$DEPLOYER_USER"
  echo "${DEPLOYER_USER}:${DEPLOYER_PASSWORD}" | chpasswd
  echo "[initial-setup] User $DEPLOYER_USER created."
fi
usermod -aG sudo "$DEPLOYER_USER"
usermod -aG docker "$DEPLOYER_USER"

# Sudoers: NOPASSWD for deployer via drop-in (safer than appending to /etc/sudoers)
SUDOERS_D="/etc/sudoers.d/90-${DEPLOYER_USER}"
if [[ ! -f "$SUDOERS_D" ]]; then
  echo "${DEPLOYER_USER} ALL=(ALL) NOPASSWD:ALL" > "$SUDOERS_D"
  chmod 0440 "$SUDOERS_D"
  if ! visudo -c -q -f "$SUDOERS_D" 2>/dev/null; then
    rm -f "$SUDOERS_D"
    echo "Error: sudoers drop-in invalid." >&2
    exit 1
  fi
  echo "[initial-setup] Sudoers drop-in $SUDOERS_D added."
else
  echo "[initial-setup] Sudoers drop-in already present."
fi

echo "[initial-setup] 5/7 Deployer SSH keys and authorized_keys..."
DEPLOYER_HOME="$(getent passwd "$DEPLOYER_USER" | cut -d: -f6)"
DEPLOYER_SSH="${DEPLOYER_HOME}/.ssh"
mkdir -p "$DEPLOYER_SSH"
chown "$DEPLOYER_USER:$DEPLOYER_USER" "$DEPLOYER_SSH"
chmod 700 "$DEPLOYER_SSH"

# Generate keys as deployer (no passphrase)
for key_type in "ed25519" "rsa"; do
  if [[ "$key_type" == "ed25519" ]]; then
    key_path="${DEPLOYER_SSH}/id_ed25519"
    gen_args=(-t ed25519 -f "$key_path" -N "" -C "$SSH_KEY_COMMENT")
  else
    key_path="${DEPLOYER_SSH}/id_rsa"
    gen_args=(-t rsa -b 4096 -f "$key_path" -N "" -C "$SSH_KEY_COMMENT")
  fi
  if [[ ! -f "$key_path" ]]; then
    su - "$DEPLOYER_USER" -c "ssh-keygen ${gen_args[*]}"
    echo "[initial-setup] Generated $key_type key for $DEPLOYER_USER."
  fi
done

# authorized_keys: optional external key + local id_rsa.pub
AUTH_KEYS="${DEPLOYER_SSH}/authorized_keys"
touch "$AUTH_KEYS"
chown "$DEPLOYER_USER:$DEPLOYER_USER" "$AUTH_KEYS"
chmod 600 "$AUTH_KEYS"
if [[ -n "${AUTHORIZED_KEY_ED25519:-}" ]]; then
  if ! grep -qF "$AUTHORIZED_KEY_ED25519" "$AUTH_KEYS" 2>/dev/null; then
    echo "$AUTHORIZED_KEY_ED25519" >> "$AUTH_KEYS"
    echo "[initial-setup] Added AUTHORIZED_KEY_ED25519 to authorized_keys."
  fi
fi
ID_RSA_PUB="${DEPLOYER_SSH}/id_rsa.pub"
if [[ -f "$ID_RSA_PUB" ]]; then
  if ! grep -qF "$(cat "$ID_RSA_PUB")" "$AUTH_KEYS" 2>/dev/null; then
    cat "$ID_RSA_PUB" >> "$AUTH_KEYS"
    echo "[initial-setup] Appended deployer id_rsa.pub to authorized_keys."
  fi
fi

echo "[initial-setup] 6/7 GitHub Actions self-hosted runner..."
if [[ "${SKIP_RUNNER_SETUP:-0}" == "1" ]]; then
  echo "[initial-setup] Skipping runner (SKIP_RUNNER_SETUP=1)."
else
  if [[ -z "${GITHUB_RUNNER_TOKEN:-}" ]]; then
    echo "Warning: GITHUB_RUNNER_TOKEN not set; skipping runner. Add runner manually and set token next time." >&2
  else
    RUNNER_DIR="${DEPLOYER_HOME}/actions-runner"
    RUNNER_TAR="actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz"
    RUNNER_URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${RUNNER_TAR}"
    mkdir -p "$RUNNER_DIR"
    if [[ ! -f "${RUNNER_DIR}/config.sh" ]]; then
      curl -fsSL -o "${RUNNER_DIR}/${RUNNER_TAR}" "$RUNNER_URL"
      echo "${RUNNER_LINUX_X64_SHA256}  ${RUNNER_DIR}/${RUNNER_TAR}" | shasum -a 256 -c -
      tar xzf "${RUNNER_DIR}/${RUNNER_TAR}" -C "$RUNNER_DIR"
      rm -f "${RUNNER_DIR}/${RUNNER_TAR}"
      chown -R "$DEPLOYER_USER:$DEPLOYER_USER" "$RUNNER_DIR"
      su - "$DEPLOYER_USER" -c "cd ${RUNNER_DIR} && ./config.sh --url ${GITHUB_RUNNER_REPO_URL} --token ${GITHUB_RUNNER_TOKEN} --unattended"
      "${RUNNER_DIR}/svc.sh" install "$DEPLOYER_USER"
      "${RUNNER_DIR}/svc.sh" start
      echo "[initial-setup] GitHub Actions runner installed and started (systemd service)."
    else
      echo "[initial-setup] Runner already configured in $RUNNER_DIR."
    fi
  fi
fi

echo "[initial-setup] 7/7 Done."
echo "  - SSH: password auth disabled, root login disabled. Use key-based auth as $DEPLOYER_USER."
echo "  - Deployer: $DEPLOYER_USER (sudo, docker). Keys in ${DEPLOYER_SSH}."
echo "  - Runner: service runs as $DEPLOYER_USER; sudo svc.sh status/start/stop in $RUNNER_DIR."
echo "  - Set AUTHORIZED_KEY_ED25519 and re-run to add your public key to authorized_keys if needed."
