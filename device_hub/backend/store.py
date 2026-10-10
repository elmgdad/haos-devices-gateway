"""Small JSON files under /data (or ./.data in dev mode)."""

import json
import os
from pathlib import Path

ADDON = bool(os.environ.get("SUPERVISOR_TOKEN"))
DATA_DIR = Path(
    os.environ.get("DATA_DIR")
    or ("/data" if ADDON else Path(__file__).resolve().parents[2] / ".data")
)


def load(name, default=None):
    path = DATA_DIR / f"{name}.json"
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def save(name, value):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{name}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False))
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def remove(name):
    path = DATA_DIR / f"{name}.json"
    if path.exists():
        path.unlink()


def settings():
    return {"z2m_base_topic": "zigbee2mqtt", "default_cidr": "", **(load("settings", {}) or {})}


def addon_options():
    """Values from the Home Assistant app configuration page (/data/options.json)."""
    path = DATA_DIR / "options.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
