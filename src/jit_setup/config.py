"""Configuration management — ~/.jitx/config.json."""

import json
import uuid
from pathlib import Path

CONFIG_DIR = Path.home() / ".jitx"
CONFIG_FILE = CONFIG_DIR / "config.json"


def load_config() -> dict:
    """Load config from disk. Returns empty dict if no config."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(data: dict):
    """Merge data into existing config and save."""
    config = load_config()
    config.update(data)
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(
        json.dumps(config, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def get_device_id() -> str:
    """Get or create a stable device ID for rate limiting."""
    config = load_config()
    did = config.get("device_id")
    if did:
        return did
    did = str(uuid.uuid4())
    save_config({"device_id": did})
    return did


def get_llm_config() -> dict:
    """Return LLM-specific config (if user set one)."""
    config = load_config()
    return config.get("llm", {})
