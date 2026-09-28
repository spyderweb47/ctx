"""ctx sessions: the named workspace you open, save and switch between.

A ctx session binds a name to one harness session (Claude Code, Codex, …) and
carries everything you build against it -- the Labs board, staged deployments,
run history. The harness transcript is never stored here; only your work is.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from . import autosave, board, deploy, pipeline

ROOT = Path.home() / ".ctx" / "sessions"
ACTIVE = Path.home() / ".ctx" / "active.json"


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._ -]", "", name or "").strip().replace(" ", "-")
    return (s or "untitled")[:60]


def _path(sid: str) -> Path:
    return ROOT / (sid + ".json")


def listing() -> list[dict]:
    out = []
    try:
        for f in sorted(ROOT.glob("*.json"), key=lambda x: -x.stat().st_mtime):
            try:
                d = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            h = d.get("harness") or {}
            out.append({"id": f.stem, "name": d.get("name", f.stem),
                        "harness_key": h.get("key", ""), "harness_name": h.get("name", ""),
                        "adapter": h.get("adapter", ""), "cwd": h.get("cwd", ""),
                        "nodes": len(((d.get("board") or {}).get("nodes")) or []),
                        "deployments": len(((d.get("deploy") or {}).get("items")) or []),
                        "runs": len(((d.get("runs") or {}).get("runs")) or []),
                        "created": d.get("created", 0), "updated": d.get("updated", 0)})
    except OSError:
        pass
    return out


def active() -> dict:
    try:
        return json.loads(ACTIVE.read_text())
    except (OSError, ValueError):
        return {}


def set_active(sid: str, key: str) -> None:
    try:
        ACTIVE.parent.mkdir(parents=True, exist_ok=True)
        ACTIVE.write_text(json.dumps({"ctx_session": sid, "key": key}, indent=2))
    except OSError:
        pass


def clear_active() -> None:
    """Close the ctx session; the harness key stays so Explorer still works."""
    d = active()
    d.pop("ctx_session", None)
    try:
        ACTIVE.write_text(json.dumps(d, indent=2))
    except OSError:
        pass


def read(sid: str) -> dict | None:
    try:
        return json.loads(_path(sid).read_text())
    except (OSError, ValueError):
        return None


def _write(sid: str, d: dict) -> None:
    try:
        ROOT.mkdir(parents=True, exist_ok=True)
        p = _path(sid)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=2))
        os.replace(tmp, p)
    except OSError:
        pass


def create(name: str, ref) -> dict:
    """New ctx session bound to a harness session. Starts with an empty board."""
    base = _slug(name)
    sid, n = base, 2
    while _path(sid).exists():
        sid, n = f"{base}-{n}", n + 1
    now = time.time()
    d = {"ctxs": 1, "id": sid, "name": name or base, "created": now, "updated": now,
         "harness": {"key": ref.key, "name": ref.name, "adapter": ref.adapter,
                     "cwd": ref.cwd, "sid": ref.sid},
         "board": {"version": 1, "seq": 1, "edges": [], "nodes": [{
             "id": "n1", "label": "N1", "kind": "session", "title": ref.name,
             "body": "", "x": 60, "y": 60, "created": now, "updated": now}]},
         "deploy": {"version": 1, "seq": 0, "items": []},
         "runs": {"version": 1, "seq": 0, "runs": []}}
    _write(sid, d)
    open_(sid)
    return {"ok": True, "id": sid, "name": d["name"], "key": ref.key}


def open_(sid: str) -> dict:
    """Load a ctx session's work into the live working set and make it active."""
    d = read(sid)
    if not d:
        return {"ok": False, "error": f"no ctx session {sid!r}"}
    key = (d.get("harness") or {}).get("key", "")
    autosave.suspend()
    try:
        board.save(key, d.get("board") or {"nodes": [], "edges": []})
        deploy.save(key, d.get("deploy") or {"seq": 0, "items": []})
        pipeline.save(key, d.get("runs") or {"seq": 0, "runs": []})
    finally:
        autosave.resume()
    set_active(sid, key)
    return {"ok": True, "id": sid, "name": d.get("name", sid), "key": key,
            "harness": d.get("harness") or {}}


def mirror(key: str) -> None:
    """Mirror the live stores into the ctx session bound to this harness key.

    Only the bound session is written, so a stray write against another harness
    can never land in the open session's file.
    """
    a = active()
    sid = a.get("ctx_session")
    if sid and a.get("key") == key:
        save(sid)


def save(sid: str) -> dict:
    d = read(sid)
    if not d:
        return {"ok": False, "error": f"no ctx session {sid!r}"}
    key = (d.get("harness") or {}).get("key", "")
    d["board"] = board.load(key)
    d["deploy"] = deploy.load(key)
    d["runs"] = pipeline.load(key)
    d["updated"] = time.time()
    _write(sid, d)
    return {"ok": True, "id": sid, "saved": d["updated"]}


def rename(sid: str, name: str) -> dict:
    d = read(sid)
    if not d:
        return {"ok": False, "error": "not found"}
    d["name"] = name or d["name"]
    d["updated"] = time.time()
    _write(sid, d)
    return {"ok": True, "name": d["name"]}


def delete(sid: str) -> dict:
    try:
        _path(sid).unlink()
        return {"ok": True}
    except OSError as e:
        return {"ok": False, "error": str(e)}
