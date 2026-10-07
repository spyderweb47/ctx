"""Talk to the session from inside ctx, instead of from a second terminal.

The meta-agent pattern works, but it costs a whole second harness per project:
ten projects meant twenty terminals and remembering which one was pointed where.
This spawns that agent headlessly from the server instead. It is the same
Claude Code, with the same login and the same ctx tools -- it just has no
terminal of its own, and it is always pointed at the ctx session you have open.

Two rules it runs under:

* **It cannot touch your machine.** Bash, Write, Edit and friends are denied.
  It reads the context, searches it, builds on the canvas, and stages changes.
* **It cannot apply anything.** That is the MCP server's own guarantee, not a
  setting here: deploy_stage stages, and applying stays a human click.

Each ctx session gets its own agent conversation, resumed by id, so the chat
remembers what you were talking about.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from . import autosave

ROOT = Path.home() / ".ctx" / "chat"
HERE = Path(__file__).resolve().parent.parent
MCP_SERVER = HERE / "mcp_server.py"

# It answers about a SESSION'S CONTEXT, not about a codebase. Left with Read,
# Glob and Grep it behaves like a coding agent: the first run spent 21 turns and
# $0.16 reading ctx's own source to work out what the active session was, when
# one `current_session` call would have told it. Deny the filesystem entirely
# and the ctx tools become the only way to learn anything -- which is correct.
DENIED = ("Bash", "BashOutput", "KillShell", "Write", "Edit", "NotebookEdit",
          "Read", "Glob", "Grep", "WebFetch", "WebSearch", "Agent", "Task",
          "TodoWrite", "Artifact")
MAX_TURNS = 24
TIMEOUT = 600

BRIEF = """You are the assistant built into ctx, a context-engineering tool. The
person is looking at a ctx session in their browser and talking to you from a
panel inside it.

Your subject is THE CONTEXT OF THE SESSION ctx IS POINTED AT -- what is in that
window, what it costs, what is wrong with it, and what to change. You are not a
coding assistant and there is no repository to explore; you have no filesystem
tools on purpose.

Everything you know comes from the ctx tools:
- current_session -- the session and its buckets, with what each costs and the
  lever for changing it. Start here; it is cheap.
- get_bucket("B") / get_item("B1") -- one bucket's items, one item's full text.
- extract_run -- search the whole context for a keyword or phrase; it returns
  real labelled records. Use this when asked about a topic, value or claim.
- board_add / board_write / board_connect / board_remove -- the Labs canvas.
- review_read / review_audit -- audit a proposal.
- deploy_stage / deploy_stage_ops -- stage a change for the person to apply.

How to answer:
- Lead with the answer. These replies appear in a narrow side panel, so be
  short: a few sentences, or a tight list. No preamble, no recap of the
  question.
- Cite labels. "K85 and L46 carry it, 3.2k tokens" beats "there are some tool
  results about it". Labels are how the person navigates and dictates.
- Give real numbers from the tools, never estimates of your own.
- You can stage changes; you can never apply one. Say what you staged and that
  applying is their click."""

_runs: dict[str, dict] = {}
_lock = threading.Lock()


def _path(key: str) -> Path:
    return ROOT / (re.sub(r"[^A-Za-z0-9._-]", "_", key) + ".json")


def load(key: str) -> dict:
    try:
        d = json.loads(_path(key).read_text())
        if isinstance(d, dict) and isinstance(d.get("messages"), list):
            return d
    except (OSError, ValueError):
        pass
    return {"version": 1, "agent_sid": "", "messages": []}


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


def clear(key: str) -> dict:
    """Forget the conversation AND the agent session, so the next message
    starts clean rather than resuming something you cannot see."""
    return save(key, {"version": 0, "agent_sid": "", "messages": []})


def _mcp_config() -> str:
    cfg = {"mcpServers": {"ctx": {"command": sys.executable,
                                  "args": [str(MCP_SERVER)]}}}
    fd, path = tempfile.mkstemp(suffix=".json", prefix="ctx-mcp-")
    with os.fdopen(fd, "w") as fh:
        json.dump(cfg, fh)
    return path


def _argv(text: str, resume: str, model: str, cfg: str) -> list[str]:
    argv = ["claude", "-p", text,
            "--output-format", "stream-json", "--verbose",
            "--mcp-config", cfg, "--strict-mcp-config",
            "--permission-mode", "bypassPermissions",
            "--disallowedTools", *DENIED,
            "--append-system-prompt", BRIEF,
            "--max-turns", str(MAX_TURNS)]
    if model:
        argv += ["--model", model]
    if resume:
        argv += ["--resume", resume]
    return argv


def _push(run: dict, ev: dict) -> None:
    with _lock:
        run["events"].append(ev)


def _pump(run: dict, key: str, text: str, model: str) -> None:
    """Drive one exchange and translate the harness stream into chat events."""
    d = load(key)
    cfg = _mcp_config()
    argv = _argv(text, d.get("agent_sid", ""), model, cfg)
    reply, tools, sid = [], [], d.get("agent_sid", "")
    try:
        home = ROOT / "agent"
        home.mkdir(parents=True, exist_ok=True)
        p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, bufsize=1, cwd=str(home))
        run["proc"] = p
        for line in p.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            t = e.get("type")
            if e.get("session_id"):
                sid = e["session_id"]
            if t == "system" and e.get("subtype") == "init":
                _push(run, {"t": "ready", "tools": len(e.get("tools") or []),
                            "mcp": [s.get("name") for s in (e.get("mcp_servers") or [])]})
            elif t == "assistant":
                for b in (e.get("message") or {}).get("content") or []:
                    if b.get("type") == "text" and (b.get("text") or "").strip():
                        reply.append(b["text"])
                        _push(run, {"t": "text", "v": b["text"]})
                    elif b.get("type") == "tool_use":
                        name = (b.get("name") or "").replace("mcp__ctx__", "")
                        tools.append(name)
                        _push(run, {"t": "tool", "v": name,
                                    "arg": _brief(b.get("input"))})
            elif t == "user":
                for b in (e.get("message") or {}).get("content") or []:
                    if b.get("type") == "tool_result":
                        _push(run, {"t": "did", "v": _brief(b.get("content"), 120)})
            elif t == "result":
                _push(run, {"t": "done", "subtype": e.get("subtype"),
                            "turns": e.get("num_turns"),
                            "cost": round(e.get("total_cost_usd") or 0, 4)})
        err = (p.stderr.read() or "").strip()
        p.wait(timeout=10)
        if p.returncode and not reply:
            _push(run, {"t": "error", "v": err[-400:] or f"exited {p.returncode}"})
    except FileNotFoundError:
        _push(run, {"t": "error", "v": "the `claude` CLI is not on PATH"})
    except Exception as e:                      # a chat box must not take the server
        _push(run, {"t": "error", "v": f"{type(e).__name__}: {e}"})
    finally:
        try:
            os.unlink(cfg)
        except OSError:
            pass
        d = load(key)
        d["agent_sid"] = sid
        d["messages"].append({"role": "assistant", "text": "\n\n".join(reply),
                              "tools": tools, "at": time.time()})
        save(key, d)
        with _lock:
            run["done"] = True


def send(key: str, text: str, model: str = "") -> dict:
    """Start one exchange. Returns immediately; the client polls for events."""
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "nothing to send"}
    d = load(key)
    d["messages"].append({"role": "user", "text": text, "at": time.time()})
    save(key, d)
    rid = uuid.uuid4().hex[:12]
    run = {"id": rid, "events": [], "done": False, "started": time.time()}
    with _lock:
        _runs[rid] = run
        for old, r in list(_runs.items()):      # keep the table small
            if r.get("done") and time.time() - r.get("started", 0) > 1800:
                _runs.pop(old, None)
    threading.Thread(target=_pump, args=(run, key, text, model), daemon=True).start()
    return {"ok": True, "run": rid}


def events(rid: str, after: int = 0) -> dict:
    run = _runs.get(rid)
    if not run:
        return {"ok": False, "error": "unknown run"}
    with _lock:
        evs = run["events"][after:]
        return {"ok": True, "events": evs, "next": after + len(evs),
                "done": run["done"],
                "elapsed": round(time.time() - run["started"], 1)}


def stop(rid: str) -> dict:
    run = _runs.get(rid)
    p = (run or {}).get("proc")
    if p and p.poll() is None:
        p.terminate()
        return {"ok": True, "stopped": rid}
    return {"ok": False, "error": "not running"}


def _brief(v, n: int = 90) -> str:
    if v is None:
        return ""
    if not isinstance(v, str):
        try:
            v = json.dumps(v, ensure_ascii=False)
        except (TypeError, ValueError):
            v = str(v)
    v = " ".join(v.split())
    return v[:n] + ("…" if len(v) > n else "")
