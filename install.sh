#!/usr/bin/env bash
#
# install.sh — One-click setup for Claude Code Telegram Bot on Ubuntu
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SERVICE_NAME="claude-telegram-bot"
VENV_DIR="$SCRIPT_DIR/venv"
CURRENT_USER="$(whoami)"

echo "============================================"
echo "  Claude Code Telegram Bot — Installer"
echo "============================================"
echo ""

# ------------------------------------------------------------------
# 1. System dependencies
# ------------------------------------------------------------------
echo "[1/7] Checking system dependencies..."
NEED_INSTALL=()
for pkg in python3-venv python3-pip; do
    if ! dpkg -s "$pkg" &>/dev/null; then
        NEED_INSTALL+=("$pkg")
    fi
done

if [ ${#NEED_INSTALL[@]} -gt 0 ]; then
    echo "  Installing: ${NEED_INSTALL[*]}"
    sudo apt-get update -qq
    sudo apt-get install -y -qq "${NEED_INSTALL[@]}"
else
    echo "  All system dependencies satisfied."
fi

# ------------------------------------------------------------------
# 2. Python virtual environment
# ------------------------------------------------------------------
echo "[2/7] Setting up Python virtual environment..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
    echo "  Created venv at $VENV_DIR"
else
    echo "  Venv already exists."
fi
source "$VENV_DIR/bin/activate"

# ------------------------------------------------------------------
# 3. Install Python dependencies
# ------------------------------------------------------------------
echo "[3/7] Installing Python dependencies..."
pip install --upgrade pip -q
pip install -r "$SCRIPT_DIR/requirements.txt" -q
echo "  Dependencies installed."

# ------------------------------------------------------------------
# 4. Check Claude Code CLI
# ------------------------------------------------------------------
echo "[4/7] Checking Claude Code CLI..."
CLAUDE_PATH=$(which claude 2>/dev/null || echo "")
if [ -z "$CLAUDE_PATH" ]; then
    echo "  WARNING: 'claude' not found in PATH."
    echo "  Make sure Claude Code is installed. Install with:"
    echo "    npm install -g @anthropic-ai/claude-code"
    echo "  Or update cli_path in config.yaml to the full path."
else
    echo "  Found Claude CLI at: $CLAUDE_PATH"
fi

# ------------------------------------------------------------------
# 5. Configuration
# ------------------------------------------------------------------
echo "[5/7] Configuring..."
if [ ! -f "$SCRIPT_DIR/config.yaml" ]; then
    cp "$SCRIPT_DIR/config.yaml.example" "$SCRIPT_DIR/config.yaml"
    echo "  Created config.yaml from template."
    echo "  IMPORTANT: Edit config.yaml to set your bot token and user ID!"
else
    echo "  config.yaml already exists."
fi

# Update paths in config.yaml to match current user
sed -i "s|/home/user|/home/$CURRENT_USER|g" "$SCRIPT_DIR/config.yaml"
echo "  Updated paths for user: $CURRENT_USER"

# ------------------------------------------------------------------
# 6. Initialize MemPalace
# ------------------------------------------------------------------
echo "[6/7] Initializing MemPalace..."
if command -v mempalace &>/dev/null || [ -f "$VENV_DIR/bin/mempalace" ]; then
    MEMPAL_CMD="${VENV_DIR}/bin/mempalace"
    if [ ! -d "$HOME/.mempalace" ]; then
        "$MEMPAL_CMD" init "$HOME" || echo "  MemPalace init completed (may need manual config)."
    else
        echo "  MemPalace already initialized."
    fi
else
    echo "  MemPalace CLI not found. It will be available after pip install."
fi

# ------------------------------------------------------------------
# 7. Install systemd service
# ------------------------------------------------------------------
echo "[7/7] Installing systemd service..."

# Generate the service file with actual username
cat > "/tmp/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Claude Code Telegram Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${VENV_DIR}/bin/python3 ${SCRIPT_DIR}/bot.py
Restart=always
RestartSec=10
StandardOutput=append:${SCRIPT_DIR}/bot.log
StandardError=append:${SCRIPT_DIR}/bot.log
Environment=PATH=/home/${CURRENT_USER}/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=HOME=/home/${CURRENT_USER}

[Install]
WantedBy=multi-user.target
EOF

sudo cp "/tmp/${SERVICE_NAME}.service" "/etc/systemd/system/${SERVICE_NAME}.service"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
echo "  Service installed and enabled."

echo ""
echo "============================================"
echo "  Installation complete!"
echo "============================================"
echo ""
echo "Before starting, make sure config.yaml is correct:"
echo "  nano $SCRIPT_DIR/config.yaml"
echo ""
echo "Then start the bot:"
echo "  sudo systemctl start $SERVICE_NAME"
echo ""
echo "Check status:"
echo "  sudo systemctl status $SERVICE_NAME"
echo ""
echo "View logs:"
echo "  tail -f $SCRIPT_DIR/bot.log"
echo ""
echo "The bot will auto-start on boot."
