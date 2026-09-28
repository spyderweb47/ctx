"""A rolling log of MCP calls, so the board can show what the agent is doing."""
from __future__ import annotations

import json
import time
from pathlib import Path

LOG = Path.home() / ".ctx" / "mcp.log"
KEEP = 400


def append(tool: str, args: dict, ok: bool = True, note: str = "") -> None:
    rec = {"ts": time.time(), "tool": tool,
           "args": {k: (v if isinstance(v, (int, float, bool)) else str(v)[:120])
                    for k, v in (args or {}).items()},
           "ok": bool(ok), "note": str(note)[:220]}
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def tail(n: int = 120) -> list[dict]:
    try:
        lines = LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    if len(lines) > KEEP * 2:                       # trim opportunistically
        try:
            LOG.write_text("\n".join(lines[-KEEP:]) + "\n")
            lines = lines[-KEEP:]
        except OSError:
            pass
    out = []
    for ln in lines[-n:]:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out
