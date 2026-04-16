#!/usr/bin/env python3
"""Claude Code Telegram Bot — receive instructions via Telegram, execute with Claude Code."""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).parent / "config.yaml"


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


CFG = load_config()
BOT_TOKEN = CFG["telegram"]["bot_token"]
ALLOWED_IDS: set[int] = set(CFG["telegram"]["allowed_user_ids"])
API_BASE_URL: str = CFG["telegram"].get("api_base_url", "https://api.telegram.org/bot")
CLAUDE_CLI = CFG["claude"].get("cli_path", "claude")
DEFAULT_CWD = Path(CFG["claude"].get("default_cwd", str(Path.home())))
TIMEOUT = CFG["claude"].get("timeout_seconds", 300)
EXTRA_ARGS: list[str] = CFG["claude"].get("extra_args", [])
USE_SAME_SESSION: bool = CFG["claude"].get("use_same_session", True)
MEMPALACE_ENABLED: bool = CFG.get("mempalace", {}).get("enabled", False)
MEMPALACE_MCP_CFG = CFG.get("mempalace", {}).get("mcp_config", "")
LOG_LEVEL = CFG.get("logging", {}).get("level", "INFO")
LOG_FILE = CFG.get("logging", {}).get("file", "")
CHAT_LOG_FILE = CFG.get("logging", {}).get("chat_log", "")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger("claude-tg-bot")
logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

console = logging.StreamHandler()
console.setFormatter(fmt)
logger.addHandler(console)

if LOG_FILE:
    fh = logging.FileHandler(os.path.expanduser(LOG_FILE))
    fh.setFormatter(fmt)
    logger.addHandler(fh)

# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------

START_TIME = time.time()
current_cwd: Path = DEFAULT_CWD
current_timeout: int = TIMEOUT
running_proc: asyncio.subprocess.Process | None = None

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TELEGRAM_MSG_LIMIT = 4096


def chunk_message(text: str, limit: int = TELEGRAM_MSG_LIMIT) -> list[str]:
    """Split text into chunks that fit within Telegram's message limit."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        # Try to break at a newline
        idx = text.rfind("\n", 0, limit)
        if idx == -1 or idx < limit // 2:
            # Fall back to space
            idx = text.rfind(" ", 0, limit)
        if idx == -1 or idx < limit // 2:
            idx = limit
        chunks.append(text[:idx])
        text = text[idx:].lstrip("\n")
    return chunks


def log_chat(user_id: int, username: str | None, prompt: str, response: str) -> None:
    """Append a conversation entry to the chat log file (JSONL format)."""
    if not CHAT_LOG_FILE:
        return
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "username": username,
        "prompt": prompt,
        "response": response,
    }
    path = os.path.expanduser(CHAT_LOG_FILE)
    try:
        with open(path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.error("Failed to write chat log: %s", e)


SOUL_FILE = Path(__file__).parent / "soul.md"


def build_claude_cmd(prompt: str, *, continue_session: bool = True) -> list[str]:
    """Build the claude CLI command list."""
    cmd = [CLAUDE_CLI, "-p"]
    if continue_session and USE_SAME_SESSION:
        cmd.append("--continue")
    cmd.extend(EXTRA_ARGS)
    # Soul: inject personality and skills via system prompt file
    if SOUL_FILE.exists():
        cmd.extend(["--append-system-prompt-file", str(SOUL_FILE)])
    # MemPalace is registered as a user-level MCP server — no --mcp-config needed
    cmd.append(prompt)
    return cmd


def is_authorized(user_id: int) -> bool:
    return user_id in ALLOWED_IDS


# ---------------------------------------------------------------------------
# Bot command handlers
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, _) -> None:
    if not is_authorized(update.effective_user.id):
        return
    await update.message.reply_text(
        "Claude Code Telegram Bot is ready.\n"
        "Send any message to execute it as a Claude Code prompt.\n\n"
        "Commands:\n"
        "/cd <dir> — change working directory\n"
        "/status — show bot status\n"
        "/timeout <sec> — set execution timeout\n"
        "/session_new — start a fresh session\n"
        "/cancel — cancel running command"
    )


async def cmd_status(update: Update, _) -> None:
    if not is_authorized(update.effective_user.id):
        return
    uptime = int(time.time() - START_TIME)
    h, m, s = uptime // 3600, (uptime % 3600) // 60, uptime % 60
    running = "yes" if running_proc and running_proc.returncode is None else "no"
    mempalace = "enabled" if MEMPALACE_ENABLED else "disabled"
    session_mode = "continue (same session)" if USE_SAME_SESSION else "new each time"

    await update.message.reply_text(
        f"Status: running\n"
        f"Uptime: {h}h {m}m {s}s\n"
        f"Working dir: {current_cwd}\n"
        f"Timeout: {current_timeout}s\n"
        f"Session: {session_mode}\n"
        f"MemPalace: {mempalace}\n"
        f"Command running: {running}"
    )


async def cmd_cd(update: Update, context) -> None:
    if not is_authorized(update.effective_user.id):
        return
    global current_cwd
    if not context.args:
        await update.message.reply_text(f"Current dir: {current_cwd}\nUsage: /cd <path>")
        return
    new_dir = Path(os.path.expanduser(context.args[0]))
    if not new_dir.is_absolute():
        new_dir = current_cwd / new_dir
    new_dir = new_dir.resolve()
    if not new_dir.is_dir():
        await update.message.reply_text(f"Directory not found: {new_dir}")
        return
    current_cwd = new_dir
    await update.message.reply_text(f"Working directory changed to: {current_cwd}")


async def cmd_timeout(update: Update, context) -> None:
    if not is_authorized(update.effective_user.id):
        return
    global current_timeout
    if not context.args:
        await update.message.reply_text(f"Current timeout: {current_timeout}s\nUsage: /timeout <seconds>")
        return
    try:
        val = int(context.args[0])
        if val < 10 or val > 3600:
            raise ValueError
        current_timeout = val
        await update.message.reply_text(f"Timeout set to {current_timeout}s")
    except ValueError:
        await update.message.reply_text("Invalid value. Must be 10-3600.")


async def cmd_session_new(update: Update, context) -> None:
    """Start a fresh session immediately with an optional prompt."""
    if not is_authorized(update.effective_user.id):
        return
    prompt = " ".join(context.args) if context.args else "Hello, I'm starting a new session. Introduce yourself briefly."
    # Fake an update with this prompt and force new session
    context.user_data["new_session"] = True
    update.message.text = prompt
    await handle_message(update, context)


async def cmd_cancel(update: Update, _) -> None:
    if not is_authorized(update.effective_user.id):
        return
    global running_proc
    if running_proc and running_proc.returncode is None:
        try:
            running_proc.terminate()
            await asyncio.sleep(2)
            if running_proc.returncode is None:
                running_proc.kill()
        except ProcessLookupError:
            pass
        running_proc = None
        await update.message.reply_text("Command cancelled.")
    else:
        await update.message.reply_text("No command is currently running.")


# ---------------------------------------------------------------------------
# Main message handler — execute Claude Code
# ---------------------------------------------------------------------------


async def handle_message(update: Update, context) -> None:
    user = update.effective_user
    if not is_authorized(user.id):
        logger.warning("Unauthorized access attempt from user %s (%s)", user.id, user.username)
        return

    global running_proc
    if running_proc and running_proc.returncode is None:
        await update.message.reply_text("A command is already running. Use /cancel to stop it first.")
        return

    prompt = update.message.text
    if not prompt:
        return

    logger.info("User %s (%s): %s", user.id, user.username, prompt[:100])

    # Check for new session flag
    new_session = context.user_data.pop("new_session", False)

    # Show typing indicator while processing
    chat = update.message.chat

    async def keep_typing():
        """Send typing action every 5s to keep the indicator alive."""
        while True:
            try:
                await chat.send_action(ChatAction.TYPING)
            except Exception:
                pass
            await asyncio.sleep(5)

    cmd = build_claude_cmd(prompt, continue_session=not new_session)
    logger.info("Executing: %s", " ".join(cmd[:5]) + " ...")

    typing_task = asyncio.create_task(keep_typing())

    try:
        running_proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(current_cwd),
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                running_proc.communicate(),
                timeout=current_timeout,
            )
        except asyncio.TimeoutError:
            running_proc.terminate()
            await asyncio.sleep(2)
            if running_proc.returncode is None:
                running_proc.kill()
            running_proc = None
            typing_task.cancel()
            await update.message.reply_text(f"Command timed out after {current_timeout}s. Use /timeout to increase.")
            return

        running_proc = None
        typing_task.cancel()
        output = stdout.decode("utf-8", errors="replace").strip()
        err_output = stderr.decode("utf-8", errors="replace").strip()

        # If --continue produced empty output, retry without it (new session)
        if not output and not new_session and USE_SAME_SESSION:
            logger.warning("Empty output with --continue, retrying as new session...")
            typing_task = asyncio.create_task(keep_typing())
            retry_cmd = build_claude_cmd(prompt, continue_session=False)
            running_proc = await asyncio.create_subprocess_exec(
                *retry_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(current_cwd),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    running_proc.communicate(), timeout=current_timeout,
                )
            except asyncio.TimeoutError:
                running_proc.terminate()
                running_proc = None
                typing_task.cancel()
                await update.message.reply_text(f"Command timed out after {current_timeout}s.")
                return
            running_proc = None
            typing_task.cancel()
            output = stdout.decode("utf-8", errors="replace").strip()
            err_output = stderr.decode("utf-8", errors="replace").strip()

        if not output and err_output:
            output = f"[stderr]\n{err_output}"
        elif not output:
            output = "(no output)"

        # Send response in chunks
        chunks = chunk_message(output)
        for chunk in chunks:
            await update.message.reply_text(chunk)

        logger.info("Response sent (%d chars, %d chunks)", len(output), len(chunks))
        log_chat(user.id, user.username, prompt, output)

    except Exception as e:
        running_proc = None
        typing_task.cancel()
        logger.exception("Error executing Claude command")
        await update.message.reply_text(f"Error: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    logger.info("Starting Claude Code Telegram Bot...")
    logger.info("Allowed users: %s", ALLOWED_IDS)
    logger.info("Working directory: %s", current_cwd)
    logger.info("MemPalace: %s", "enabled" if MEMPALACE_ENABLED else "disabled")
    logger.info("Session mode: %s", "continue" if USE_SAME_SESSION else "new")

    builder = Application.builder().token(BOT_TOKEN)
    if API_BASE_URL != "https://api.telegram.org/bot":
        logger.info("Using custom API base URL: %s", API_BASE_URL)
        builder = builder.base_url(API_BASE_URL).base_file_url(
            API_BASE_URL.replace("/bot", "/file/bot")
        )
    app = builder.build()

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("cd", cmd_cd))
    app.add_handler(CommandHandler("timeout", cmd_timeout))
    app.add_handler(CommandHandler("session_new", cmd_session_new))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started. Polling for messages...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
