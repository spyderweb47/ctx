"""Codex adapter.

Store shape:
  ~/.codex/session_index.jsonl                      index (id, thread_name)
  ~/.codex/sessions/**/rollout-<ts>-<id>.jsonl      transcript
  ~/.codex/archived_sessions/rollout-<ts>-<id>.jsonl

Same shape as Claude Code: home store + append-only JSONL + an index. Different
record names, identical structure -- which is the whole point of the adapter layer.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..base import Segment, SessionRef, strip_images

NAME = "codex"
ROOT = Path.home() / ".codex"


def _rollouts() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for sub in ("sessions", "archived_sessions"):
        d = ROOT / sub
        if not d.is_dir():
            continue
        for f in d.rglob("rollout-*.jsonl"):
            # rollout-<ISO timestamp>-<uuid>.jsonl ; uuid is the last 5 dash groups
            parts = f.stem.split("-")
            if len(parts) >= 5:
                out["-".join(parts[-5:])] = f
    return out


def discover() -> list[SessionRef]:
    rolls = _rollouts()
    names: dict[str, tuple[str, str]] = {}
    idx = ROOT / "session_index.jsonl"
    if idx.is_file():
        try:
            for raw in idx.read_text(errors="replace").splitlines():
                if not raw.strip():
                    continue
                d = json.loads(raw)
                names[d.get("id", "")] = (d.get("thread_name", ""), d.get("updated_at", ""))
        except (OSError, ValueError):
            pass

    refs = []
    for sid, path in rolls.items():
        name, updated = names.get(sid, ("", ""))
        cwd = ""
        try:  # session_meta is the first record
            first = json.loads(path.open(encoding="utf-8", errors="replace").readline())
            cwd = (first.get("payload") or {}).get("cwd", "")
        except (OSError, ValueError):
            pass
        if not updated:
            try:
                updated = str(int(path.stat().st_mtime * 1000))
            except OSError:
                updated = ""
        refs.append(SessionRef(
            adapter=NAME, sid=sid, name=name or Path(cwd).name or sid[:8],
            cwd=cwd, transcript=str(path),
            # Codex has one shared ipc.sock, not a per-session pid, so liveness
            # is not cheaply knowable. Say so rather than guess.
            status="unknown", updated_at=str(updated),
        ))
    return refs


def _txt_img(v) -> tuple[str, int, list]:
    """Serialise, lifting images out so they are priced by area and renderable.

    Mirrors the Claude Code adapter -- both stores embed base64 images, and
    measuring the payload instead of the pixels overcounts by ~50x.
    """
    tok, imgs = 0, []
    if isinstance(v, (list, dict)):
        v, tok, imgs = strip_images(v)
    return _txt(v), tok, imgs


def _txt(v) -> str:
    if isinstance(v, (list, dict)):
        v, _, _ = strip_images(v)
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        parts = []
        for b in v:
            if isinstance(b, dict):
                parts.append(b.get("text") or b.get("content") or json.dumps(b, ensure_ascii=False))
            else:
                parts.append(str(b))
        return "\n".join(parts)
    return json.dumps(v, ensure_ascii=False)


def parse(path: str) -> tuple[list[Segment], dict]:
    segs: list[Segment] = []
    usage: dict = {}
    turn = 0

    with open(path, encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                r = json.loads(raw)
            except ValueError:
                continue
            rtype = r.get("type")
            p = r.get("payload") or {}

            if rtype == "session_meta":
                segs.append(Segment("environment", "session meta", _txt(p), turn, lineno))
            elif rtype == "turn_context":
                # model, sandbox, personality, approval policy -- all launch config
                segs.append(Segment("environment", "turn context", _txt(p), turn, lineno))
            elif rtype == "response_item":
                pt = p.get("type")
                if pt == "message":
                    role = p.get("role")
                    text, itok, imgs = _txt_img(p.get("content"))
                    if role == "user":
                        turn += 1
                        segs.append(Segment("user_turns", text[:70], text, turn, lineno,
                                            extra_tokens=itok, images=imgs))
                    elif role == "developer":
                        # Codex injects its preamble as developer messages: these
                        # are regenerated each launch, so they are CONFIG.
                        segs.append(Segment("system_prompt", text[:70], text, turn, lineno,
                                            extra_tokens=itok, images=imgs))
                    else:
                        segs.append(Segment("assistant_turns", text[:70], text, turn, lineno,
                                            extra_tokens=itok, images=imgs))
                elif pt == "reasoning":
                    segs.append(Segment("thinking", "reasoning", _txt(
                        p.get("summary") or p.get("content")), turn, lineno))
                elif pt in ("function_call", "custom_tool_call"):
                    segs.append(Segment("tool_calls", p.get("name", "tool"),
                                        _txt(p.get("arguments") or p.get("input")),
                                        turn, lineno, {"tool": p.get("name")}))
                elif pt in ("function_call_output", "custom_tool_call_output"):
                    body, itok, imgs = _txt_img(p.get("output"))
                    segs.append(Segment("tool_results", "result", body, turn, lineno,
                                        extra_tokens=itok, images=imgs))
                else:
                    segs.append(Segment("other", str(pt), _txt(p), turn, lineno))
            elif rtype == "event_msg":
                pt = p.get("type")
                if pt == "token_count":
                    info = p.get("info") or p
                    if isinstance(info, dict):
                        usage = info
                    continue
                segs.append(Segment("events", str(pt), _txt(p), turn, lineno))
            else:
                segs.append(Segment("other", str(rtype), _txt(p), turn, lineno))
    return segs, usage
