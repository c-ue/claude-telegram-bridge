# Claude Code Telegram Bot

A Telegram bot daemon that lets you remotely control [Claude Code](https://docs.anthropic.com/en/docs/claude-code) from your phone or any Telegram client. Send a message, Claude Code executes it, and the result comes back — all within the same persistent session.

Includes optional [MemPalace](https://github.com/MemPalace/mempalace) integration for long-term memory that survives across sessions.

## Architecture

```
Telegram  ──>  Bot daemon (Python, systemd)  ──>  claude -p --continue  ──>  response  ──>  Telegram
                     │
                     └── MemPalace MCP server (optional, long-term memory)
```

**Dual-layer memory:**

- **Short-term** — `--continue` resumes the most recent Claude Code session, preserving full conversation context.
- **Long-term** — MemPalace stores verbatim memories locally, searchable via semantic search and a knowledge graph (19 MCP tools). Even after a session reset, Claude can recall past context.

## Prerequisites

- **Ubuntu** 20.04+ (or any systemd-based Linux)
- **Python** 3.10+
- **Claude Code** CLI installed and authenticated (`npm install -g @anthropic-ai/claude-code`)
- **Telegram Bot Token** — create one via [@BotFather](https://t.me/BotFather)
- **Your Telegram User ID** — get it from [@userinfobot](https://t.me/userinfobot)

## Quick Start

```bash
# 1. Clone or copy the project
git clone <this-repo> ~/claude-telegram-bot
cd ~/claude-telegram-bot

# 2. Edit configuration
cp config.yaml.example config.yaml
nano config.yaml
# Set your bot_token, allowed_user_ids, and default_cwd

# 3. Run the installer
chmod +x install.sh
./install.sh

# 4. Start the bot
sudo systemctl start claude-telegram-bot
```

The bot starts on boot automatically and restarts on crash.

## Configuration

All settings live in `config.yaml`:

```yaml
telegram:
  bot_token: "YOUR_BOT_TOKEN_HERE"
  allowed_user_ids:
    - 123456789              # Only these Telegram user IDs can use the bot

claude:
  cli_path: "claude"         # Path to the claude binary
  default_cwd: "/home/user"  # Default working directory for commands
  timeout_seconds: 300       # Max execution time per command (10s–3600s)
  extra_args:
    - "--dangerously-skip-permissions"
  use_same_session: true     # true = --continue (resume last session)

mempalace:
  enabled: true              # Enable MemPalace long-term memory
  mcp_config: "mempalace-mcp.json"

logging:
  level: "INFO"
  file: "/home/user/claude-telegram-bot/bot.log"
```

### Security

Only Telegram users whose IDs appear in `allowed_user_ids` can interact with the bot. Messages from all other users are silently ignored.

The bot token and config file should be kept private — do not commit `config.yaml` to a public repository.

## Telegram Commands

| Command | Description |
|---------|-------------|
| *(any text)* | Execute as a Claude Code prompt |
| `/start` | Show welcome message and command list |
| `/status` | Show bot uptime, working directory, and current settings |
| `/cd <path>` | Change working directory |
| `/timeout <seconds>` | Set command timeout (10–3600) |
| `/session_new` | Start a fresh Claude Code session (next message only) |
| `/cancel` | Cancel the currently running command |

### Examples

```
You: List all Python files in the current directory
Bot: Here are the Python files: ...

You: /cd /home/user/myproject
Bot: Working directory changed to: /home/user/myproject

You: Explain what the main function does in app.py
Bot: The main function in app.py ...

You: /session_new
Bot: Next message will start a new session.
You: Start fresh — what do you see in this project?
Bot: (responds with no prior context)
```

## MemPalace Integration

[MemPalace](https://github.com/MemPalace/mempalace) provides local-first, privacy-preserving long-term memory. When enabled, Claude Code gains access to 19 MCP tools including:

- **Semantic search** — find past conversations and decisions by meaning
- **Knowledge graph** — track facts about people, projects, and topics over time
- **Agent diary** — Claude writes session summaries it can recall later

### Setup

MemPalace is installed automatically by `install.sh`. To initialize it manually:

```bash
source venv/bin/activate
mempalace init ~/myproject
mempalace mine ~/myproject
```

To disable MemPalace, set `mempalace.enabled: false` in `config.yaml`.

## Service Management

```bash
# Start / stop / restart
sudo systemctl start claude-telegram-bot
sudo systemctl stop claude-telegram-bot
sudo systemctl restart claude-telegram-bot

# Check status
sudo systemctl status claude-telegram-bot

# View logs (live)
tail -f ~/claude-telegram-bot/bot.log

# Disable auto-start on boot
sudo systemctl disable claude-telegram-bot
```

## Project Structure

```
claude-telegram-bot/
├── bot.py                      # Main bot — async Telegram polling + Claude subprocess
├── config.yaml                 # Runtime configuration (not committed)
├── config.yaml.example         # Configuration template
├── mempalace-mcp.json          # MCP server config for MemPalace
├── requirements.txt            # Python dependencies
├── install.sh                  # One-click Ubuntu setup script
└── claude-telegram-bot.service # systemd unit file (template)
```

## How It Works

1. The bot uses long polling to receive messages from Telegram.
2. When an authorized user sends a text message, the bot spawns a subprocess:
   ```
   claude -p --continue --dangerously-skip-permissions --mcp-config mempalace-mcp.json "<prompt>"
   ```
3. `--continue` resumes the most recent session in the working directory, so Claude remembers the full conversation history.
4. The response is read from stdout, split into chunks (Telegram's 4096-char limit), and sent back.
5. If the command exceeds the timeout, it is terminated and the user is notified.

## Troubleshooting

**Bot doesn't respond:**
- Check `sudo systemctl status claude-telegram-bot` for errors.
- Verify your bot token is correct in `config.yaml`.
- Make sure your Telegram user ID is in `allowed_user_ids`.

**"claude: command not found":**
- Set `cli_path` in `config.yaml` to the full path (e.g., `/home/user/.local/bin/claude`).
- Make sure the `PATH` in the systemd service includes the directory where `claude` is installed.

**Command times out:**
- Increase `timeout_seconds` in `config.yaml` or use `/timeout 600` in Telegram.

**MemPalace not working:**
- Verify `mempalace-mcp.json` exists in the project directory.
- Run `source venv/bin/activate && mempalace status` to check MemPalace health.
- Check that `mempalace.enabled` is `true` in `config.yaml`.

## License

MIT
