"""The one session the tool is currently pointed at.

Shared on disk so the canvas and the MCP server always agree: pick a session in
the browser and session 2 is looking at the same thing, with no handshake.
"""
from __future__ import annotations

import json
from pathlib import Path

STORE = Path.home() / ".ctx" / "active.json"


def get() -> str | None:
    try:
        return json.loads(STORE.read_text()).get("key")
    except (OSError, ValueError):
        return None


def read() -> dict:
    try:
        return json.loads(STORE.read_text())
    except (OSError, ValueError):
        return {}


def set(key: str) -> None:  # noqa: A001
    """Point at a harness session, preserving any ctx session binding.

    A ctx session owns which harness it is attached to. Overwriting this file
    with only the key used to silently unbind it -- which sent a refresh back to
    onboarding and let an agent write its nodes onto a different board.
    """
    d = read()
    if d.get("ctx_session") and d.get("key") and d["key"] != key:
        return                      # pinned by an open ctx session
    d["key"] = key
    try:
        STORE.parent.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(d, indent=2))
    except OSError:
        pass
