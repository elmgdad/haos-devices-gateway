"""What Device Hub did and saw: commands, removals, renames, keys, and devices going offline.

The newest 500 entries are kept in /data/activity.json. Writes are batched a few
seconds apart so a dashboard sending many commands does not wear the SD card.
"""

import asyncio
import secrets
import time
from collections import deque

from . import store

STORE = "activity"
LIMIT = 500
KINDS = ("command", "scene", "remove", "change", "offline", "online", "key", "error")


class Activity:
    def __init__(self):
        saved = store.load(STORE) or {}
        self.entries = deque(saved.get("entries") or [], maxlen=LIMIT)
        self.listeners = []
        self._flush = None

    def log(self, kind, text, *, device_id=None, device=None, ok=True, via="panel"):
        entry = {
            "id": secrets.token_hex(6),
            "at": time.time(),
            "kind": kind,
            "text": text,
            "device_id": device_id,
            "device": device,
            "ok": ok,
            "via": via,
        }
        self.entries.appendleft(entry)
        self._save_soon()
        for listener in list(self.listeners):
            listener(entry)
        return entry

    def list(self, limit=200, device_id=None, kind=None, device_ids=None):
        shown = []
        for entry in self.entries:
            if device_id and entry["device_id"] != device_id:
                continue
            if device_ids is not None and entry["device_id"] not in device_ids:
                continue
            if kind and entry["kind"] != kind:
                continue
            shown.append(entry)
            if len(shown) >= limit:
                break
        return shown

    def clear(self):
        self.entries.clear()
        store.remove(STORE)

    def _save_soon(self):
        if self._flush is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._save()
            return
        self._flush = loop.call_later(5, self._save)

    def _save(self):
        self._flush = None
        store.save(STORE, {"entries": list(self.entries)})


def say(value):
    """A command value the way a person reads it."""
    if value is True:
        return "on"
    if value is False:
        return "off"
    if value is None or value == "":
        return "pressed"
    return str(value)
