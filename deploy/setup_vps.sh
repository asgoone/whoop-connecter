#!/usr/bin/env bash
# Setup script for WHOOP MCP server on VPS
# Run from the whoop_connecter directory

set -euo pipefail

INSTALL_DIR="$(pwd)"

echo "=== Creating virtualenv ==="
python3 -m venv .venv

echo "=== Installing dependencies ==="
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e .

echo "=== Generating encryption key (save this!) ==="
ENCRYPTION_KEY=$(.venv/bin/python -c "import secrets; print(secrets.token_hex(32))")
echo "WHOOP_TOKEN_ENCRYPTION_KEY=${ENCRYPTION_KEY}"

echo ""
echo "=== Copy .env.example to .env and fill in your credentials ==="
echo "cp .env.example .env && nano .env"
echo ""
echo "=== First-time auth ==="
echo ".venv/bin/whoop auth login"
echo ""
echo "=== Test ==="
echo ".venv/bin/whoop auth status"
echo ".venv/bin/whoop summary"
