#!/usr/bin/env bash
#
# DEPRECATED — do not run on the BK Platform platform.
#
# This script used to free host ports 80/443 for product-owned nginx
# (docker-compose.https.yml). Gateway edge nginx now owns those ports.
# Running this would stop the platform edge and take down all products.
#
# Production CD uses Billykat7/infra cd-product.yml (app-only compose
# on 127.0.0.1:8002). TLS and routing live in the gateway deploy root (GATEWAY_COMPOSE_DIR).
#
set -euo pipefail

echo "error: free-http-ports-before-compose.sh is retired." >&2
echo "  Gateway edge nginx owns host ports 80/443." >&2
echo "  Do not free those ports from a product repo — it would take down the platform." >&2
echo "  Deploy via: .github/workflows/cd.yml → cd-product.yml" >&2
exit 1
