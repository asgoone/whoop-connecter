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

echo "=== Creating token directory ==="
mkdir -p ~/.whoop
chmod 700 ~/.whoop

echo "=== Generating encryption key (save this!) ==="
ENCRYPTION_KEY=$(.venv/bin/python -c "import secrets; print(secrets.token_hex(32))")
echo ""
echo "  WHOOP_TOKEN_ENCRYPTION_KEY=${ENCRYPTION_KEY}"
echo ""

if [ ! -f .env ]; then
    echo "=== Creating .env from template ==="
    cp .env.example .env
    chmod 600 .env
    echo "  Edit .env with your credentials:"
    echo "  nano .env"
else
    echo "=== .env already exists, skipping ==="
fi

echo ""
echo "=== Next steps ==="
echo ""
echo "  1. Fill in your credentials:"
echo "     nano .env"
echo ""
echo "  2. Authorize (headless mode for VPS):"
echo "     .venv/bin/whoop auth login-headless"
echo ""
echo "  3. Test:"
echo "     .venv/bin/whoop auth status"
echo "     .venv/bin/whoop summary"
echo ""
echo "  4. Configure OpenClaw MCP (edit paths in):"
echo "     deploy/openclaw_mcp_config.json"
echo ""
