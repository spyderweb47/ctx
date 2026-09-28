"""Pipelines: walk the Labs graph, resolve it to one prompt, park it for the agent.

ctx does not call a model. It resolves a chain of nodes into a single brief,
creates the output node the answer belongs in, and queues the run. The MCP agent
picks the run up, does the thinking, and writes the result back. That keeps the
heavy lifting where the intelligence is and keeps ctx a deterministic tool.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from . import autosave

from . import board

ROOT = Path.home() / ".ctx" / "runs"


def _path(key: str) -> Path:
    return ROOT / (re.sub(r"[^A-Za-z0-9._-]", "_", key) + ".json")


def load(key: str) -> dict:
    try:
        d = json.loads(_path(key).read_text())
        if isinstance(d, dict) and isinstance(d.get("runs"), list):
            return d
    except (OSError, ValueError):
        pass
    return {"version": 1, "seq": 0, "runs": []}


def save(key: str, d: dict) -> dict:
    d["version"] = int(d.get("version", 0)) + 1
    p = _path(key)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=2))
        os.replace(tmp, p)
    except OSError:
        pass
    autosave.touch(key)
    return d


# chain() lives in board.py -- one traversal, used by both
chain = board.chain


def brief(nodes: list[dict]) -> str:
    parts = []
    for n in nodes:
        head = f"### {n['label']} — {n['title']}  [{n['kind']}]"
        body = (n.get("body") or "").strip() or "(empty)"
        parts.append(f"{head}\n\n{body}")
    return "\n\n---\n\n".join(parts)


def queue(key: str, start_label: str, session_name: str = "") -> dict:
    bd = board.load(key, session_name)
    nodes = board.chain(bd, start_label)
    if not nodes:
        return {"ok": False, "error": f"no node {start_label!r}"}
    if len(nodes) == 1:
        return {"ok": False, "error": f"{start_label} has no outgoing edges — "
                                      "connect it to at least one node first."}

    # the answer needs somewhere to land before the agent starts
    last = nodes[-1]
    out_node, bd = board.add(key, f"Output of {start_label}", "", "output",
                             float(last.get("x", 0)) + 260, float(last.get("y", 0)),
                             session_name)
    board.connect(key, last["label"], out_node["label"], session_name)  # (ok, board)

    d = load(key)
    seq = int(d.get("seq", 0)) + 1
    run = {"id": f"r{seq}", "label": f"R{seq}", "start": start_label,
           "chain": [n["label"] for n in nodes], "input": brief(nodes),
           "output_node": out_node["label"], "status": "queued",
           "created": time.time(), "done_at": None, "output": "", "note": ""}
    d["runs"].append(run)
    d["seq"] = seq
    save(key, d)
    return {"ok": True, "run": run, "output_node": out_node["label"]}


def pending(key: str) -> list[dict]:
    return [r for r in load(key)["runs"] if r["status"] == "queued"]


def find_run(d: dict, label: str) -> dict | None:
    t = (label or "").strip().upper()
    for r in d["runs"]:
        if r["label"].upper() == t or r["id"].upper() == t:
            return r
    return None


def complete(key: str, label: str, output: str, session_name: str = "") -> dict:
    d = load(key)
    r = find_run(d, label) if label else (pending(key) or [None])[0]
    if r is None:
        return {"ok": False, "error": "no such run"}
    if r["status"] == "done":
        return {"ok": False, "error": f"{r['label']} is already complete"}
    r["output"] = output or ""
    r["status"] = "done"
    r["done_at"] = time.time()
    save(key, d)
    board.write(key, r["output_node"], body=output or "",
                title=f"Output of {r['start']} · {r['label']}",
                session_name=session_name)
    return {"ok": True, "run": r, "node": r["output_node"]}


def drop(key: str, label: str) -> dict:
    d = load(key)
    r = find_run(d, label)
    if r:
        d["runs"] = [x for x in d["runs"] if x["id"] != r["id"]]
        save(key, d)
    return d
