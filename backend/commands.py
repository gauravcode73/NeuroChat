"""
Command handler for the Offline AI Assistant.

Supported commands (typed in the chat input):
  /help                 → list all commands
  /clear                → reset conversation memory
  /save [name]          → save conversation to a JSON file
  /load [name]          → load a saved conversation
  /exit                 → signal the frontend to close
  /model [name]         → switch the active Ollama model
  /status               → show current model and memory stats
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Tuple

from config import CONVERSATIONS_DIR, DEFAULT_MODEL

logger = logging.getLogger(__name__)

_SAVE_DIR = Path(CONVERSATIONS_DIR)


def _ensure_dir() -> None:
    _SAVE_DIR.mkdir(parents=True, exist_ok=True)


# ──────────────────────────────────────────────────────────────
# Public helpers
# ──────────────────────────────────────────────────────────────

def is_command(text: str) -> bool:
    """Return True if the text starts with '/'."""
    return text.strip().startswith("/")


def parse_command(text: str) -> Tuple[str, Optional[str]]:
    """
    Split a command string into (command, argument).
    e.g. '/save my_chat' → ('save', 'my_chat')
         '/clear'        → ('clear', None)
    """
    parts = text.strip().lstrip("/").split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else None
    return cmd, arg


def handle_command(cmd: str, arg: Optional[str], engine: "ChatEngine") -> dict:  # noqa: F821
    """
    Dispatch a command and return a result dict:
      { "action": str, "message": str, "data": any }
    """
    if cmd == "help":
        return _cmd_help()

    if cmd == "clear":
        engine.memory.clear()
        return {"action": "clear", "message": "✅ Conversation memory cleared.", "data": None}

    if cmd == "save":
        return _cmd_save(arg, engine)

    if cmd == "load":
        return _cmd_load(arg, engine)

    if cmd == "exit":
        return {"action": "exit", "message": "👋 Goodbye! Close the browser tab to exit.", "data": None}

    if cmd == "model":
        if not arg:
            return {"action": "info", "message": f"🤖 Current model: **{engine.model}**\nUsage: `/model <name>`", "data": None}
        old = engine.model
        engine.model = arg.strip()
        return {"action": "model_changed", "message": f"🔄 Model switched from `{old}` → `{engine.model}`", "data": engine.model}

    if cmd == "status":
        return {
            "action": "status",
            "message": (
                f"**Status**\n"
                f"- Model: `{engine.model}`\n"
                f"- Messages in context: `{engine.memory.message_count}`\n"
                f"- Long-term memory: `{'enabled' if engine.memory.long.available else 'disabled'}`\n"
                f"- Saved conversations: `{_count_saves()}`"
            ),
            "data": None,
        }

    if cmd == "conversations" or cmd == "list":
        return _cmd_list_saves()

    return {
        "action": "unknown",
        "message": f"❓ Unknown command: `/{cmd}`\nType `/help` for available commands.",
        "data": None,
    }


# ──────────────────────────────────────────────────────────────
# Individual command implementations
# ──────────────────────────────────────────────────────────────

def _cmd_help() -> dict:
    help_text = (
        "**Available Commands**\n\n"
        "| Command | Description |\n"
        "|---------|-------------|\n"
        "| `/clear` | Reset conversation memory |\n"
        "| `/save [name]` | Save current conversation |\n"
        "| `/load [name]` | Load a saved conversation |\n"
        "| `/list` | List saved conversations |\n"
        "| `/model [name]` | Switch active model |\n"
        "| `/status` | Show model & memory info |\n"
        "| `/exit` | Exit the application |\n"
        "| `/help` | Show this help message |\n"
    )
    return {"action": "help", "message": help_text, "data": None}


def _cmd_save(name: Optional[str], engine: "ChatEngine") -> dict:  # noqa: F821
    _ensure_dir()
    if not name:
        import datetime
        name = datetime.datetime.now().strftime("chat_%Y%m%d_%H%M%S")

    # Sanitise filename
    safe_name = "".join(c for c in name if c.isalnum() or c in ("_", "-")).rstrip()
    if not safe_name:
        safe_name = "conversation"

    path = _SAVE_DIR / f"{safe_name}.json"
    data = engine.memory.to_dict()
    data["model"] = engine.model

    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return {"action": "saved", "message": f"💾 Conversation saved as **{safe_name}**.", "data": safe_name}
    except OSError as exc:
        logger.error("Save failed: %s", exc)
        return {"action": "error", "message": f"❌ Could not save: {exc}", "data": None}


def _cmd_load(name: Optional[str], engine: "ChatEngine") -> dict:  # noqa: F821
    _ensure_dir()
    if not name:
        saves = _list_save_names()
        if not saves:
            return {"action": "error", "message": "❌ No saved conversations found.", "data": None}
        return {
            "action": "list",
            "message": "**Saved conversations:**\n" + "\n".join(f"- `{s}`" for s in saves),
            "data": saves,
        }

    safe_name = "".join(c for c in name if c.isalnum() or c in ("_", "-")).rstrip()
    path = _SAVE_DIR / f"{safe_name}.json"

    if not path.exists():
        return {"action": "error", "message": f"❌ Conversation `{safe_name}` not found.", "data": None}

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        engine.memory.clear()
        engine.memory.load_dict(data)
        if "model" in data:
            engine.model = data["model"]
        return {
            "action": "loaded",
            "message": f"📂 Loaded conversation **{safe_name}** ({engine.memory.message_count} messages).",
            "data": {"messages": engine.memory.short.to_serialisable(), "model": engine.model},
        }
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Load failed: %s", exc)
        return {"action": "error", "message": f"❌ Could not load: {exc}", "data": None}


def _cmd_list_saves() -> dict:
    saves = _list_save_names()
    if not saves:
        return {"action": "list", "message": "📁 No saved conversations yet.", "data": []}
    return {
        "action": "list",
        "message": "**Saved conversations:**\n" + "\n".join(f"- `{s}`" for s in saves),
        "data": saves,
    }


def _list_save_names() -> list:
    _ensure_dir()
    return [p.stem for p in sorted(_SAVE_DIR.glob("*.json"))]


def _count_saves() -> int:
    if not _SAVE_DIR.exists():
        return 0
    return sum(1 for _ in _SAVE_DIR.glob("*.json"))
