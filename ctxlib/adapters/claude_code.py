"""Claude Code adapter.

Store shape:
  ~/.claude/sessions/<pid>.json          live registry (sessionId, cwd, status)
  ~/.claude/projects/<slug>/<sid>.jsonl  append-only transcript
"""
from __future__ import annotations

import json
from pathlib import Path

from ..base import SIGNATURE, Segment, SessionRef, pid_alive, strip_images

NAME = "claude-code"
ROOT = Path.home() / ".claude"


def _transcripts() -> dict[str, Path]:
    out: dict[str, Path] = {}
    proj = ROOT / "projects"
    if not proj.is_dir():
        return out
    for f in proj.glob("*/*.jsonl"):
        out[f.stem] = f
    return out


def discover() -> list[SessionRef]:
    tx = _transcripts()
    refs: dict[str, SessionRef] = {}

    # Live registry first -- these carry real names and status.
    for f in sorted((ROOT / "sessions").glob("*.json")) if (ROOT / "sessions").is_dir() else []:
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        sid = d.get("sessionId")
        if not sid or sid not in tx:
            continue
        alive = pid_alive(d.get("pid"))
        refs[sid] = SessionRef(
            adapter=NAME, sid=sid,
            name=d.get("name") or Path(d.get("cwd", "")).name or sid[:8],
            cwd=d.get("cwd", ""), transcript=str(tx[sid]),
            status="running" if alive else ("stopped" if alive is False else "unknown"),
            updated_at=str(d.get("updatedAt", "")), pid=d.get("pid"),
        )

    # Then any transcript with no live registry entry -- a stopped session.
    for sid, path in tx.items():
        if sid in refs:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        refs[sid] = SessionRef(
            adapter=NAME, sid=sid, name=path.parent.name.strip("-").split("-")[-1] or sid[:8],
            cwd=path.parent.name, transcript=str(path), status="stopped",
            updated_at=str(int(mtime * 1000)),
        )
    return list(refs.values())


_ATTACH = {
    "prompt_snapshot": None,          # handled specially: two buckets
    "skill_listing": "skills",
    "mcp_instructions_delta": "mcp_instructions",
    "agent_listing_delta": "agents",
    "deferred_tools_delta": "tool_schemas",
    "deferred_tools_record": "tool_schemas",
    "instructions": "project_instructions",
    "environment": "environment",
    "model": "environment",
    "date": "environment",
    "date_change": "environment",
    "session_context": "environment",
    "auto_mode": "environment",
    "remote_session_change": "environment",
    "command_permissions": "environment",
    "task_reminder": "reminders",
    "total_tokens_reminder": "reminders",
    "silent_turn_reminder": "reminders",
    "ultra_effort_enter": "reminders",
    "queued_command": "reminders",
    "file": "file_reads",
    "edited_text_file": "file_reads",
    "compact_file_reference": "file_reads",
    "read_truncation_notice": "file_reads",
    "hook_additional_context": "hooks",
}


def _txt(v) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def _txt_img(v) -> tuple[str, int, list]:
    """Serialise content, lifting images out so they are priced and shown properly."""
    if v is None:
        return "", 0, []
    if isinstance(v, str):
        return v, 0, []
    clean, tok, imgs = strip_images(v)
    return json.dumps(clean, ensure_ascii=False), tok, imgs


def _scan(path: str) -> dict:
    """One pass to find where the live context actually begins.

    A transcript is an append-only log, not a picture of the context window.
    Two things in it are historical and are NOT in the package the CLI sends:

    * everything before the last compaction. When a session compacts, the CLI
      throws the conversation away and replaces it with a summary; the file
      keeps the discarded turns anyway.
    * every prompt snapshot but the last. The system prompt and tool schemas
      are regenerated at each launch, so only the newest one is live.
    """
    cut = 0          # line of the last compact summary -- the live prefix
    snap = 0         # line of the newest prompt_snapshot
    compactions = 0
    total = 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            total = lineno
            try:
                r = json.loads(raw)
            except ValueError:
                continue
            if r.get("isCompactSummary") or r.get("subtype") == "compact_boundary":
                cut = lineno
                compactions += 1
            if r.get("type") == "attachment" \
                    and (r.get("attachment") or {}).get("type") == "prompt_snapshot":
                snap = lineno
    return {"cut": cut, "snap": snap, "compactions": compactions, "total": total}


def parse(path: str) -> tuple[list[Segment], dict]:
    segs: list[Segment] = []
    usage: dict = {}
    turn = 0
    think_total = 0
    sig_chars = 0
    seen_req: set = set()           # usage is repeated per content block -- see below
    scan = _scan(path)
    cut, snap = scan["cut"], scan["snap"]

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

            # Before the last compaction: on disk, but not in the package. The
            # newest prompt snapshot is exempt -- the system prompt and schemas
            # are resent every turn wherever they sit in the file.
            if lineno < cut and lineno != snap:
                continue
            if r.get("isCompactSummary") or r.get("subtype") == "compact_boundary":
                m = r.get("message")
                body = (m or {}).get("content") if isinstance(m, dict) else None
                segs.append(Segment("compact_summary", "compact summary",
                                    _txt(body), turn, lineno,
                                    {"compactions": scan["compactions"]}))
                continue

            if rtype == "attachment":
                a = r.get("attachment") or {}
                at = a.get("type", "?")
                if at == "prompt_snapshot":
                    if lineno != snap:
                        continue          # superseded by a later launch
                    sp = a.get("systemPrompt")
                    if sp:
                        segs.append(Segment("system_prompt", "System prompt snapshot",
                                            _txt(sp), turn, lineno, {"attachment": at}))
                    for t in (a.get("tools") or []):
                        segs.append(Segment("tool_schemas", t.get("name", "tool"),
                                            _txt(t), turn, lineno,
                                            {"tool": t.get("name")}))
                    continue
                bucket = _ATTACH.get(at, "other")
                body = a.get("content") or a.get("text") or a.get("snapshot") \
                    or a.get("context") or a.get("files") or a
                text, imgtok, imgs = _txt_img(body)
                segs.append(Segment(bucket, at.replace("_", " "), text,
                                    turn, lineno, {"attachment": at},
                                    extra_tokens=imgtok, images=imgs))
                continue

            msg = r.get("message")
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if msg.get("usage"):
                usage = msg["usage"]
                # One API response is written as several records -- one per
                # content block -- and each carries a COPY of the same usage.
                # Summing them counted the same reasoning two or three times
                # over, so count each request once.
                rid = r.get("requestId") or msg.get("id") or r.get("uuid")
                if rid not in seen_req:
                    seen_req.add(rid)
                    det = usage.get("output_tokens_details") or {}
                    think_total += int(det.get("thinking_tokens") or 0)
            mdl = msg.get("model") or ""
            content = msg.get("content")
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            if not isinstance(content, list):
                continue

            if role == "user":
                turn += 1
            for blk in content:
                if not isinstance(blk, dict):
                    continue
                bt = blk.get("type")
                if bt == "text":
                    segs.append(Segment("user_turns" if role == "user" else "assistant_turns",
                                        (blk.get("text") or "")[:70], blk.get("text") or "",
                                        turn, lineno, {"model": mdl} if mdl else {}))
                elif bt == "thinking":
                    sig = blk.get("signature") or ""
                    sig_chars += len(sig)
                    # The body is empty on disk, so the signature is the only
                    # thing here with a size -- and it is the part that is
                    # actually resident in the package.
                    # Title it by what is actually there. 140 rows all saying
                    # "thinking", all opening empty, told you nothing; the
                    # signature's size is the one real fact about each block.
                    segs.append(Segment("thinking",
                                        f"signature · {len(sig):,} chars"
                                        + (" · text kept" if blk.get("thinking") else ""),
                                        blk.get("thinking") or "", turn, lineno,
                                        {"signature_chars": len(sig), "model": mdl,
                                         "text_persisted": bool(blk.get("thinking"))},
                                        extra_tokens=round(len(sig) / SIGNATURE)))
                elif bt == "tool_use":
                    segs.append(Segment("tool_calls", blk.get("name", "tool"),
                                        _txt(blk.get("input")), turn, lineno,
                                        {"tool": blk.get("name"), "id": blk.get("id"),
                                         "model": mdl}))
                elif bt == "tool_result":
                    body, imgtok, imgs = _txt_img(blk.get("content"))
                    segs.append(Segment("tool_results", f"result {blk.get('tool_use_id','')[:12]}",
                                        body, turn, lineno,
                                        {"tool_use_id": blk.get("tool_use_id"),
                                         "image_tokens": imgtok} if imgtok else
                                        {"tool_use_id": blk.get("tool_use_id")},
                                        extra_tokens=imgtok, images=imgs))
    usage = dict(usage, ctx_thinking_total=think_total,
                 ctx_signature_chars=sig_chars, ctx_requests=len(seen_req),
                 ctx_cut={"records_total": scan["total"],
                          "records_dropped": max(0, cut - 1),
                          "compactions": scan["compactions"],
                          "boundary_line": cut})
    return segs, usage
