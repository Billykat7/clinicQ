#!/usr/bin/env bash
#
# Create a Hetzner Cloud server and print its public IPv4.
# Requires: HETZNER_API_TOKEN in environment (never commit the token).
# Optional: jq for parsing JSON (falls back to grep/sed if jq missing).
#
# Usage:
#   export HETZNER_API_TOKEN=your-token
#   ./scripts/create-hetzner-server.sh
#   ./scripts/create-hetzner-server.sh "my-maps-server" cx21 fsn1
#
# Args (all optional): name, server_type, location. Image defaults to ubuntu-22.04.
# Output: server IP on stdout; non-zero exit on failure.
#
set -euo pipefail

NAME="${1:-maps-api}"
SERVER_TYPE="${2:-cx23}"
LOCATION="${3:-fsn1}"
IMAGE="${IMAGE:-ubuntu-24.04}"
API_URL="https://api.hetzner.cloud/v1/servers"

if [[ -z "${HETZNER_API_TOKEN:-}" ]]; then
  echo "Error: HETZNER_API_TOKEN is not set. Set it in the environment or .env." >&2
  exit 1
fi

RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "$API_URL" \
  -H "Authorization: Bearer $HETZNER_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"$NAME\",
    \"server_type\": \"$SERVER_TYPE\",
    \"image\": \"$IMAGE\",
    \"location\": \"$LOCATION\"
  }")

HTTP_BODY=$(echo "$RESPONSE" | head -n -1)
HTTP_CODE=$(echo "$RESPONSE" | tail -n 1)

if [[ "$HTTP_CODE" -lt 200 || "$HTTP_CODE" -ge 300 ]]; then
  echo "Error: API returned HTTP $HTTP_CODE" >&2
  echo "$HTTP_BODY" >&2
  exit 1
fi

if command -v jq &>/dev/null; then
  IP=$(echo "$HTTP_BODY" | jq -r '.server.public_net.ipv4.ip // empty')
else
  IP=$(echo "$HTTP_BODY" | grep -o '"ip":"[^"]*"' | head -1 | sed 's/"ip":"//;s/"//')
fi

if [[ -z "$IP" ]]; then
  echo "Error: Could not parse server IP from response." >&2
  echo "$HTTP_BODY" >&2
  exit 1
fi

echo "$IP"
