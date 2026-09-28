"""Per-session board: nodes the user spawns and the MCP writes into.

This is the one part of ctx that is read-write. It never touches a transcript --
a board lives beside the session, so an agent can annotate, explain and plan
against the context without any risk to the session itself.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from . import autosave

ROOT = Path.home() / ".ctx" / "board"


def _path(key: str) -> Path:
    return ROOT / (re.sub(r"[^A-Za-z0-9._-]", "_", key) + ".json")


def load(key: str, session_name: str = "") -> dict:
    p = _path(key)
    try:
        d = json.loads(p.read_text())
        if isinstance(d, dict) and isinstance(d.get("nodes"), list):
            d.setdefault("edges", [])
            return d
    except (OSError, ValueError):
        pass
    # A fresh board always opens with the session itself as the first node.
    now = time.time()
    return {"version": 1, "seq": 1, "edges": [], "nodes": [{
        "id": "n1", "label": "N1", "kind": "session",
        "title": session_name or "Session context",
        "body": "", "x": 40, "y": 40, "created": now, "updated": now,
    }]}


def save(key: str, data: dict) -> dict:
    data["version"] = int(data.get("version", 0)) + 1
    p = _path(key)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, p)          # atomic: the MCP and the UI both write here
    except OSError:
        pass
    autosave.touch(key)
    return data


def find(data: dict, label: str) -> dict | None:
    t = (label or "").strip().upper()
    for n in data["nodes"]:
        if n["label"].upper() == t or n["id"].upper() == t:
            return n
    return None


# The canvas is a pipeline with three working kinds, each deliberately narrow:
#
#   extraction  gathers. It asks the context one question -- "which records
#               mention this?" -- and holds the answer as an ordered set of real
#               Explorer items, optionally with a summary. It does not think.
#   analysis    works. A notebook cell over whatever the extractions feeding it
#               collected: code, tables, charts, no limits.
#   review      holds a proposal still. Analyses send their conclusion here, an
#               agent audits it, and only from here does anything become a
#               staged deployment.
#
# The flow is one-way, and connect() enforces it: a review node cannot feed an
# analysis, and nothing reaches deployment except through a review. The point of
# the shape is that you always know which stage a thing is at.
FLOW = {
    "session":    {"extraction"},
    "extraction": {"extraction", "analysis"},
    "analysis":   {"analysis", "review"},
    "review":     set(),
    "context":    {"extraction", "analysis"},
    "note":       set(),                      # annotation, carries no data
    "prompt":     {"analysis"},
    "code":       {"analysis", "review"},     # legacy cells behave like analysis
    "output":     set(),
}

# What a node of each kind carries beyond title/body.
KIND_FIELDS = {
    "extraction": {"query": "", "matches": [], "summary": ""},
    "analysis":   {"result": None},
    "review":     {"sent": [], "audit": [], "proposal": None, "state": "open"},
}


def flow_error(src_kind: str, dst_kind: str) -> str:
    """Why this connection is not allowed, or '' when it is."""
    allowed = FLOW.get(src_kind)
    if allowed is None:
        return ""                             # unknown kind: do not block
    if dst_kind in allowed:
        return ""
    art = "an" if src_kind[:1] in "aeiou" else "a"
    if not allowed:
        return f"{art} {src_kind} node is an endpoint -- it feeds nothing"
    return (f"{art} {src_kind} node can only feed "
            + " or ".join(sorted(allowed)) + f", not {dst_kind}")


# kinds: session (permanent) · extraction · analysis · review · note · prompt
def add(key: str, title: str, body: str = "", kind: str = "note",
        x: float = 0, y: float = 0, session_name: str = "",
        ref: dict | None = None) -> tuple[dict, dict]:
    d = load(key, session_name)
    seq = int(d.get("seq", len(d["nodes"]))) + 1
    now = time.time()
    if not x and not y:                       # auto-place on a loose grid
        i = len(d["nodes"])
        x, y = 40 + (i % 4) * 250, 40 + (i // 4) * 190
    node = {"id": f"n{seq}", "label": f"N{seq}", "kind": kind,
            **{k: (list(v) if isinstance(v, list) else v)
               for k, v in (KIND_FIELDS.get(kind) or {}).items()},
            "title": title or f"Node N{seq}", "body": body or "",
            "ref": ref or None, "x": x, "y": y, "created": now, "updated": now}
    d["nodes"].append(node)
    d["seq"] = seq
    return node, save(key, d)


def write(key: str, label: str, body: str | None = None,
          title: str | None = None, session_name: str = "") -> tuple[dict | None, dict]:
    d = load(key, session_name)
    n = find(d, label)
    if n is None:
        return None, d
    if body is not None:
        n["body"] = body
    if title is not None:
        n["title"] = title
    n["updated"] = time.time()
    return n, save(key, d)


# Fields a caller may set directly, per kind. Anything else is refused rather
# than written, so a typo cannot quietly invent a field the UI never reads.
EXTRA_FIELDS = {"query", "matches", "summary",      # extraction
                "sent", "audit", "proposal", "state"}  # review


def update(key: str, label: str, title=None, body=None, kind=None,
           session_name: str = "", **fields) -> tuple[dict | None, dict]:
    """Edit a node in place. Used by the node editor and by the MCP agent."""
    d = load(key, session_name)
    n = find(d, label)
    if n is None:
        return None, d
    if title is not None:
        n["title"] = title
    if body is not None:
        n["body"] = body
    if kind is not None and n["kind"] != "session":
        n["kind"] = kind
        for k, v in (KIND_FIELDS.get(kind) or {}).items():
            n.setdefault(k, list(v) if isinstance(v, list) else v)
    for k, v in fields.items():
        if k in EXTRA_FIELDS and v is not None:
            n[k] = v
    n["updated"] = time.time()
    return n, save(key, d)


def set_result(key: str, label: str, result: dict, session_name: str = "") -> dict:
    """Store a code cell's last run beside its source."""
    d = load(key, session_name)
    n = find(d, label)
    if n is not None:
        n["result"] = result
        n["updated"] = time.time()
        save(key, d)
    return d


def move(key: str, label: str, x: float, y: float, session_name: str = "") -> dict:
    d = load(key, session_name)
    n = find(d, label)
    if n:
        n["x"], n["y"] = x, y
        n["updated"] = time.time()
        save(key, d)
    return d


def remove(key: str, label: str, session_name: str = "") -> dict:
    d = load(key, session_name)
    n = find(d, label)
    if n and n["kind"] != "session":       # the session node is permanent
        d["nodes"] = [x for x in d["nodes"] if x["id"] != n["id"]]
        d["edges"] = [e for e in d["edges"]
                      if e["from"] != n["id"] and e["to"] != n["id"]]
        save(key, d)
    return d


def remove_many(key: str, labels: list, session_name: str = "") -> dict:
    """Delete canvas nodes by label, reporting exactly what happened to each.

    Deleting is the one board operation with nothing to undo, so it answers in
    detail rather than silently: what went, what did not and why, and how many
    edges went with them. Only the board is touched -- never a transcript, never
    a deployment.
    """
    d = load(key, session_name)
    removed, skipped = [], []
    gone_ids = set()
    for raw in labels or []:
        n = find(d, str(raw))
        if not n:
            skipped.append({"label": str(raw), "why": "no such node"})
            continue
        if n["kind"] == "session":
            skipped.append({"label": n["label"],
                            "why": "the session node is permanent"})
            continue
        if n["id"] in gone_ids:
            continue                        # named twice in one call
        gone_ids.add(n["id"])
        removed.append({"label": n["label"], "kind": n.get("kind", ""),
                        "title": n.get("title", "")})
    if not gone_ids:
        return {"removed": [], "skipped": skipped, "edges_removed": 0, "board": d}
    before = len(d["edges"])
    d["nodes"] = [x for x in d["nodes"] if x["id"] not in gone_ids]
    d["edges"] = [e for e in d["edges"]
                  if e["from"] not in gone_ids and e["to"] not in gone_ids]
    return {"removed": removed, "skipped": skipped,
            "edges_removed": before - len(d["edges"]), "board": save(key, d)}


# ------------------------------------------------------------------ edges
def connect(key: str, a: str, b: str, session_name: str = "") -> tuple[bool, dict]:
    """Directional: a -> b. Ignores self-links and duplicates, enforces FLOW."""
    d = load(key, session_name)
    na, nb = find(d, a), find(d, b)
    if not na or not nb or na["id"] == nb["id"]:
        return False, d
    why = flow_error(na.get("kind", ""), nb.get("kind", ""))
    if why:
        d = dict(d, error=f"{na['label']} \u2192 {nb['label']}: {why}")
        return False, d
    for e in d["edges"]:
        if e["from"] == na["id"] and e["to"] == nb["id"]:
            return False, d
    d["edges"].append({"from": na["id"], "to": nb["id"]})
    return True, save(key, d)


def feeders(d: dict, label: str, kinds: set | None = None) -> list[dict]:
    """Nodes with an edge INTO this one -- what an analysis is working from."""
    n = find(d, label)
    if not n:
        return []
    ids = {e["from"] for e in d.get("edges") or [] if e["to"] == n["id"]}
    return [x for x in d["nodes"]
            if x["id"] in ids and (kinds is None or x.get("kind") in kinds)]


def disconnect(key: str, a: str, b: str, session_name: str = "") -> dict:
    d = load(key, session_name)
    na, nb = find(d, a), find(d, b)
    if na and nb:
        d["edges"] = [e for e in d["edges"]
                      if not (e["from"] == na["id"] and e["to"] == nb["id"])]
        save(key, d)
    return d


def chain(data: dict, start: str) -> list[dict]:
    """Nodes reachable from `start`, in breadth order -- one pipeline run."""
    n = find(data, start)
    if not n:
        return []
    by_id = {x["id"]: x for x in data["nodes"]}
    order, seen, queue = [], {n["id"]}, [n["id"]]
    while queue:
        cur = queue.pop(0)
        order.append(by_id[cur])
        for e in data["edges"]:
            if e["from"] == cur and e["to"] not in seen:
                seen.add(e["to"]); queue.append(e["to"])
    return order
