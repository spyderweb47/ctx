"""Staged changes, and the machinery that makes applying them survivable.

Nothing here writes to a transcript until three things hold:
  1. the session is not running  (its process holds the conversation in RAM and
     would append over our edit from its own memory)
  2. a snapshot exists           (so any deploy can be undone)
  3. the result validates        (a dangling tool_use makes the provider reject
     the whole conversation with a 400, and the error will not say why)
A deploy that fails validation is rolled back from the snapshot, not left half-applied.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import autosave

ROOT = Path.home() / ".ctx"
DEPLOY = ROOT / "deploy"
SNAPS = ROOT / "snapshots"
ASSETS = ROOT / "assets"


def path(key: str) -> Path:
    """Where this session's deployments live. Public: the pulse stats it."""
    return _path(key)


def _path(key: str) -> Path:
    return DEPLOY / (re.sub(r"[^A-Za-z0-9._-]", "_", key) + ".json")


def load(key: str) -> dict:
    try:
        d = json.loads(_path(key).read_text())
        if isinstance(d, dict) and isinstance(d.get("items"), list):
            return d
    except (OSError, ValueError):
        pass
    return {"version": 1, "seq": 0, "items": []}


def save(key: str, d: dict) -> dict:
    d = _clean_saved(d)
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


# What each kind of deployment must carry to mean anything. apply() reads
# exactly these keys, so a deployment without its key could never be applied.
REQUIRED = {"ops": "ops", "drop_items": "lines", "replace_item": "line",
            "system_prompt_file": "text", "append_system_prompt": "text",
            "inject_system_prompt": "text"}


def _record(v):
    """A transcript record is one line of JSON text. Coerce, never guess."""
    if v is None or isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


def _clean_saved(d: dict) -> dict:
    """Coerce op records on the way into the store, whatever door they came through.

    stage() validates what it is given, but it is not the only writer: opening a
    ctx session calls save() directly with whatever that file holds, which
    restored un-coerced records and undid a repair. Normalising here covers
    every writer, because they all end up in save().
    """
    for i in d.get("items") or []:
        for o in (i.get("payload") or {}).get("ops") or []:
            if isinstance(o, dict) and o.get("json") is not None:
                o["json"] = _record(o["json"])
    return d


def _normalise_ops(ops) -> list:
    """A transcript record is ONE LINE OF JSON TEXT, so `json` must be a string.

    Agents naturally send the record as an object instead, and nothing caught
    it: the Deployment page called .slice() on it and threw, taking the whole
    panel down, and apply_ops would have called .rstrip() on it and failed the
    write. Coerce here, once, at the only door these come through.
    """
    if not isinstance(ops, list):
        raise ValueError("payload.ops must be a list")
    out = []
    for n, o in enumerate(ops, 1):
        if not isinstance(o, dict):
            raise ValueError(f"op {n} is not an object")
        o = dict(o)
        kind = (o.get("op") or "").lower()
        if kind not in ("replace", "delete", "insert"):
            raise ValueError(f"op {n}: unknown op {o.get('op')!r}")
        if kind in ("replace", "insert"):
            v = o.get("json")
            if v is None:
                raise ValueError(f"op {n} ({kind}) needs a json record")
            v = _record(v).strip()
            try:                       # it has to BE a record, not just text
                json.loads(v)
            except ValueError as e:
                raise ValueError(f"op {n} ({kind}): json is not a valid record: {e}")
            if "\n" in v:
                raise ValueError(f"op {n} ({kind}): a record cannot contain a newline")
            o["json"] = v
        out.append(o)
    return out


def stage(key: str, kind: str, title: str, payload: dict) -> tuple[dict, dict]:
    """Stage one change for review. Raises ValueError if it says nothing.

    A deployment has to name what it changes. This used to default a missing
    kind to `drop_items` with an empty payload, which produced a deployment
    that could never be applied -- and since the route needs no more than an
    empty body to get there, anything that touched it silently filled the
    Deployment page with junk. Refusing is the fix: a caller with nothing to
    say gets an error, not a row.
    """
    kind = (kind or "").strip()
    need = REQUIRED.get(kind)
    if not need:
        raise ValueError(f"unknown deployment kind {kind!r}; expected one of "
                         + ", ".join(sorted(REQUIRED)))
    payload = payload or {}
    if payload.get(need) in (None, "", [], {}):
        raise ValueError(f"{kind} needs a non-empty payload.{need}")
    if kind == "ops":
        payload = dict(payload, ops=_normalise_ops(payload["ops"]))
    d = load(key)
    seq = int(d.get("seq", 0)) + 1
    item = {"id": f"d{seq}", "label": f"D{seq}", "kind": kind,
            "title": title or f"Deployment D{seq}", "payload": payload or {},
            "status": "staged", "created": time.time(), "deployed_at": None,
            "snapshot": None, "note": ""}
    d["items"].append(item)
    d["seq"] = seq
    return item, save(key, d)


def find(d: dict, label: str) -> dict | None:
    t = (label or "").strip().upper()
    for i in d["items"]:
        if i["label"].upper() == t or i["id"].upper() == t:
            return i
    return None


def drop(key: str, label: str) -> dict:
    d = load(key)
    i = find(d, label)
    if i:
        d["items"] = [x for x in d["items"] if x["id"] != i["id"]]
        save(key, d)
    return d


def drop_many(key: str, labels: list, sid: str = "", force: bool = False) -> dict:
    """Remove deployment rows, refusing the ones that are load-bearing.

    Two rows must not just vanish:

    * an APPLIED one holds the snapshot its rollback reads. Delete the row and
      the transcript edit stays, with nothing left pointing at the file that
      would undo it. Roll it back first, or pass force.
    * one that is IN EFFECT is a flag in a live process. Removing the row does
      not stop the session sending it -- it only removes your record of why.
      That is a warning, not a refusal: the row is yours to keep or drop.

    The written asset file is never deleted here. A running session may be
    reading it, and a file left behind is recoverable where one deleted from
    under a live process is not.
    """
    d = load(key)
    live = live_labels(key, sid) if sid else set()
    removed, skipped, warned = [], [], []
    ids = set()
    for raw in labels or []:
        i = find(d, str(raw))
        if not i:
            skipped.append({"label": str(raw), "why": "no such deployment"})
            continue
        if i.get("status") == "deployed" and not force:
            skipped.append({"label": i["label"],
                            "why": "applied to the transcript — roll it back first, "
                                   "or its snapshot is orphaned"})
            continue
        if i["id"] in ids:
            continue
        ids.add(i["id"])
        row = {"label": i["label"], "kind": i.get("kind", ""),
               "title": i.get("title", ""), "status": i.get("status", "")}
        if i["label"] in live:
            row["warning"] = ("a running session still carries this flag; removing "
                              "the row does not stop it")
            warned.append(i["label"])
        removed.append(row)
    if ids:
        d["items"] = [x for x in d["items"] if x["id"] not in ids]
        d = save(key, d)
    return {"removed": removed, "skipped": skipped, "warned": warned,
            "items_left": len(d.get("items") or []), "deploy": d}


# ---------------------------------------------------------------- validation
def validate(path: str) -> tuple[bool, str]:
    """Every tool_use must have a matching tool_result, and every line must parse."""
    uses: dict[str, int] = {}
    results: set[str] = set()
    n = 0
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for lineno, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                n += 1
                try:
                    r = json.loads(raw)
                except ValueError:
                    return False, f"line {lineno}: not valid JSON"
                msg = r.get("message")
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content")
                if not isinstance(content, list):
                    continue
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "tool_use" and b.get("id"):
                        uses[b["id"]] = lineno
                    elif b.get("type") == "tool_result" and b.get("tool_use_id"):
                        results.add(b["tool_use_id"])
    except OSError as e:
        return False, f"cannot read transcript: {e}"

    if not n:
        return False, "transcript is empty"
    dangling = [(i, ln) for i, ln in uses.items() if i not in results]
    # the final tool_use may legitimately have no result yet
    if len(dangling) > 1:
        ln = sorted(x[1] for x in dangling)
        return False, (f"{len(dangling)} tool_use blocks have no tool_result "
                       f"(lines {ln[:5]}). The provider would reject this with a 400.")
    return True, f"{n} records, {len(uses)} tool calls, all paired"


def snapshot(path: str, key: str) -> str | None:
    try:
        SNAPS.mkdir(parents=True, exist_ok=True)
        tag = re.sub(r"[^A-Za-z0-9._-]", "_", key)
        dst = SNAPS / f"{tag}-{int(time.time())}.jsonl"
        shutil.copy2(path, dst)
        return str(dst)
    except OSError:
        return None


# ------------------------------------------------------------------- applying
BOUNDARY = "__SYSTEM_PROMPT_DYNAMIC_BOUNDARY__"


def inject_ops(path: str, text: str, mode: str = "append") -> list[dict]:
    """Ops that write `text` into the prompt the transcript has RECORDED.

    The alternative to a launch flag, and the better one if it holds: the CLI
    records the system prompt on a conversation's first request and replays that
    record on every later request and resume. A flag has to be re-passed on each
    launch and is silently dropped by the next one; a record is just there.

    Idempotent -- re-applying will not stack the same text twice. Every snapshot
    record in the file is rewritten, so whichever one the CLI reads carries it.
    """
    ops = []
    for n, raw in enumerate(open(path, encoding="utf-8", errors="replace"), 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        a = r.get("attachment") or {}
        if r.get("type") != "attachment" or a.get("type") != "prompt_snapshot":
            continue
        sp = a.get("systemPrompt")
        if not isinstance(sp, list):
            continue
        if mode == "replace":
            # Replace means replace. The recorded prompt becomes this text and
            # nothing else -- no base instruction, no boundary, none of the
            # sections the CLI wrote. Anything less is an append wearing a
            # different name.
            new = [text]
            if new == list(sp):
                continue
        elif mode == "replace_base":
            # The softer one, kept because it is sometimes what you want: swap
            # only the base instruction and leave __SYSTEM_PROMPT_DYNAMIC_BOUNDARY__
            # and the per-machine sections after it alone.
            cut = next((k for k, sec in enumerate(sp) if BOUNDARY in str(sec)), None)
            new = [text] + list(sp[cut:]) if cut is not None else [text]
            if new == list(sp):
                continue
        else:
            if sp and sp[-1] == text:
                continue                    # already injected; nothing to do
            new = [x for x in sp if x != text] + [text]
        a = dict(a, systemPrompt=new)
        ops.append({"op": "replace", "line": n,
                    "json": json.dumps(dict(r, attachment=a), ensure_ascii=False)})
    return ops


def _prompt_shape(path: str) -> str:
    """What the recorded prompt holds now -- stated after every apply, because
    'it replaced' and 'it appended' should never be a matter of opinion."""
    last = None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                try:
                    r = json.loads(raw)
                except ValueError:
                    continue
                a = r.get("attachment") or {}
                if r.get("type") == "attachment" and a.get("type") == "prompt_snapshot":
                    sp = a.get("systemPrompt")
                    if isinstance(sp, list):
                        last = sp
    except OSError:
        return "unreadable"
    if last is None:
        return "no recorded prompt"
    chars = sum(len(x) for x in last)
    return f"{len(last)} section(s), {chars:,} chars"


def _undo_record(path: str, kind: str, item: dict) -> dict | None:
    """The original text of every line a replace-only deployment will touch.

    Only replaces can be undone in place. A delete or an insert shifts every
    later line, so the positions recorded here would no longer mean the same
    thing once the session appends more; those fall back to the snapshot.
    """
    if kind == "inject_system_prompt":
        ops = inject_ops(path, (item.get("payload") or {}).get("text") or "",
                         (item.get("payload") or {}).get("mode") or "append")
    elif kind == "ops":
        ops = (item.get("payload") or {}).get("ops") or []
    elif kind == "replace_item":
        ops = [{"op": "replace", "line": (item.get("payload") or {}).get("line")}]
    else:
        return None
    if any((o.get("op") or "").lower() != "replace" for o in ops):
        return None                     # shifts lines; not surgically undoable
    want = {int(o["line"]) for o in ops if o.get("line")}
    if not want:
        return None
    before = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for n, raw in enumerate(fh, 1):
                if n in want:
                    before[str(n)] = raw.rstrip("\n")
    except OSError:
        return None
    return {"lines": before} if len(before) == len(want) else None


def apply_ops(path: str, ops: list[dict]) -> tuple[int, str]:
    """The deployment document: an ordered list of replace / delete / insert.

    The file is rebuilt rather than patched in place. A JSONL record's length
    changes when you edit it, so every following byte offset moves -- there is
    no in-place edit to make. Rebuilding into a temp file and swapping it in one
    os.replace also means a crash mid-write cannot leave a half-file behind.
    """
    drop: set[int] = set()
    repl: dict[int, str] = {}
    after: dict[int, list[str]] = {}
    for o in ops or []:
        kind = (o.get("op") or "").lower()
        if kind == "delete":
            for ln in o.get("lines", []) or ([o["line"]] if o.get("line") else []):
                drop.add(int(ln))
        elif kind == "replace":
            repl[int(o.get("line", 0))] = (_record(o.get("json")) or "").rstrip("\n")
        elif kind == "insert":
            ln = int(o.get("after_line", 0))
            after.setdefault(ln, []).append((_record(o.get("json")) or "").rstrip("\n"))

    out: list[str] = []
    changed = 0
    for pre in after.get(0, []):          # insert at the very top
        out.append(pre); changed += 1
    with open(path, encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            if lineno in drop:
                changed += 1
            elif lineno in repl:
                out.append(repl[lineno]); changed += 1
            else:
                out.append(raw.rstrip("\n"))
            for extra in after.get(lineno, []):
                out.append(extra); changed += 1
    tmp = path + ".ctxtmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    os.replace(tmp, path)
    summary = (f"{len(drop)} deleted, {len(repl)} replaced, "
               f"{sum(len(v) for v in after.values())} inserted")
    return changed, summary


def _rewrite(path: str, drop_lines: set[int], replace: dict[int, str]) -> int:
    out, changed = [], 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        for lineno, raw in enumerate(fh, 1):
            if lineno in drop_lines:
                changed += 1
                continue
            if lineno in replace:
                out.append(replace[lineno]); changed += 1; continue
            out.append(raw.rstrip("\n"))
    tmp = path + ".ctxtmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    os.replace(tmp, path)
    return changed


def apply(key: str, label: str, ref) -> dict:
    """ref is a SessionRef. Returns a result dict; never raises."""
    from .base import pid_alive
    d = load(key)
    item = find(d, label)
    if item is None:
        return {"ok": False, "error": f"no deployment {label!r}"}
    if item["status"] == "deployed":
        return {"ok": False, "error": f"{item['label']} is already deployed"}

    kind = item["kind"]

    # config-class deployments write a file beside the session, never the transcript
    if kind in ("system_prompt_file", "append_system_prompt"):
        try:
            ASSETS.mkdir(parents=True, exist_ok=True)
            f = ASSETS / f"{re.sub(r'[^A-Za-z0-9._-]', '_', key)}-{item['id']}.txt"
            f.write_text(item["payload"].get("text", ""))
        except OSError as e:
            return {"ok": False, "error": str(e)}
        flag = ("--system-prompt-file" if kind == "system_prompt_file"
                else "--append-system-prompt-file")
        # ARMED, not deployed. Writing the file changes no session. It takes a
        # launch that carries the flag, and -- verified by experiment against
        # this CLI -- a *resume* ignores the flag outright unless the prompt
        # snapshot is turned off:
        #   new session      + --append-system-prompt  -> applied
        #   resume           + --append-system-prompt  -> IGNORED
        #   resume + --system-prompt-snapshot off      -> applied
        # `--system-prompt-snapshot on` is the default and replays the prompt
        # recorded on the conversation's first request, "even when a later
        # launch passes different text". So a resume must disable it.
        item["status"] = "armed"; item["deployed_at"] = None
        item["note"] = (f"wrote {f} — armed, not yet in effect. "
                        "Launch the session with the flag below, then verify.")
        item["flags"] = f'--system-prompt-snapshot off {flag} "{f}"'
        save(key, d)
        return {"ok": True, "file": str(f), "armed": True,
                "command": f'claude --resume {ref.sid} {item["flags"]}',
                "note": "Config-class: nothing has changed yet. The system "
                        "prompt is recorded on a conversation's first request "
                        "and replayed on every resume, so this only lands when "
                        "the session is launched with these flags."}

    # transcript-class: the session must be stopped
    if pid_alive(ref.pid):
        return {"ok": False, "error": f"session is still running (pid {ref.pid}). "
                                      "Stop it first -- a live session holds the "
                                      "conversation in RAM and would overwrite this."}

    snap = snapshot(ref.transcript, key)
    if not snap:
        return {"ok": False, "error": "could not take a snapshot; refusing to write"}

    # Exactly which lines this deployment rewrites, and what they said before.
    # Without it, rollback can only restore the whole file -- which throws away
    # every turn the session has had since, and a session usually keeps running.
    undo = _undo_record(ref.transcript, kind, item)

    try:
        if kind == "inject_system_prompt":
            # transcript-class on purpose: it edits the recorded prompt, so it
            # survives a relaunch instead of riding on a flag that will not.
            ops = inject_ops(ref.transcript, item["payload"].get("text") or "",
                             item["payload"].get("mode") or "append")
            if not ops:
                return {"ok": False, "error": "nothing to change -- this transcript "
                                              "records no system prompt, or the text "
                                              "is already in it"}
            n, summary = apply_ops(ref.transcript, ops)
            item["note"] = (f"{summary} · recorded prompt is now "
                            + _prompt_shape(ref.transcript))
        elif kind == "ops":
            n, summary = apply_ops(ref.transcript, item["payload"].get("ops") or [])
            item["note"] = summary
        elif kind == "drop_items":
            lines = {int(x) for x in item["payload"].get("lines", [])}
            n = _rewrite(ref.transcript, lines, {})
        elif kind == "replace_item":
            ln = int(item["payload"].get("line", 0))
            n = _rewrite(ref.transcript, set(), {ln: item["payload"].get("json", "")})
        else:
            return {"ok": False, "error": f"unknown deployment kind {kind!r}"}
    except Exception as e:
        shutil.copy2(snap, ref.transcript)
        return {"ok": False, "error": f"write failed, rolled back: {e}"}

    ok, why = validate(ref.transcript)
    if not ok:
        shutil.copy2(snap, ref.transcript)
        return {"ok": False, "error": f"validation failed, rolled back: {why}"}

    item["status"] = "deployed"; item["deployed_at"] = time.time()
    item["snapshot"] = snap
    item["undo"] = undo
    item["note"] = f"{item.get('note') or str(n) + ' records changed'} · {why}"
    save(key, d)
    return {"ok": True, "changed": n, "validation": why, "snapshot": snap,
            "note": item["note"],
            "command": f"claude --resume {ref.sid}"}


def rollback(key: str, label: str, ref, force_snapshot: bool = False) -> dict:
    """Undo a deployment without undoing the session.

    Copying the snapshot back is the blunt version and it is usually wrong: the
    session keeps running after a deploy, so a wholesale restore silently
    discards every turn since. When the deployment recorded an undo (all its ops
    were replaces), put those exact lines back and leave the rest of the file
    alone.
    """
    d = load(key); item = find(d, label)
    if not item:
        return {"ok": False, "error": f"no deployment {label!r}"}
    undo = item.get("undo") or {}
    lines = undo.get("lines") or {}

    if lines and not force_snapshot:
        repl = {int(k): v for k, v in lines.items()}
        snap = snapshot(ref.transcript, key)      # safety net for the undo itself
        try:
            n, _ = apply_ops(ref.transcript,
                             [{"op": "replace", "line": k, "json": v}
                              for k, v in repl.items()])
        except Exception as e:
            if snap:
                shutil.copy2(snap, ref.transcript)
            return {"ok": False, "error": f"undo failed, rolled back: {e}"}
        ok, why = validate(ref.transcript)
        if not ok and snap:
            shutil.copy2(snap, ref.transcript)
            return {"ok": False, "error": f"undo did not validate, restored: {why}"}
        item["status"] = "staged"; item["deployed_at"] = None
        item["note"] = f"undone in place — {n} line(s) restored, later turns kept"
        save(key, d)
        return {"ok": True, "restored": f"{n} line(s)", "surgical": True,
                "validation": why}

    if not item.get("snapshot"):
        return {"ok": False, "error": "no undo record and no snapshot"}
    try:
        kept = sum(1 for _ in open(ref.transcript, errors="replace"))
        was = sum(1 for _ in open(item["snapshot"], errors="replace"))
        shutil.copy2(item["snapshot"], ref.transcript)
    except OSError as e:
        return {"ok": False, "error": str(e)}
    item["status"] = "staged"; item["deployed_at"] = None
    item["note"] = f"restored from snapshot ({kept - was} later record(s) discarded)"
    save(key, d)
    return {"ok": True, "restored": item["snapshot"], "surgical": False,
            "discarded": max(0, kept - was)}


def armed_flags(key: str) -> tuple[str, list[str]]:
    """Flags every armed config-class deployment needs on the next launch.

    Without this the Run button relaunched with a bare `claude --resume <id>`
    and silently dropped the very flag the deployment produced -- the file was
    written, the status said deployed, and nothing reached the session.
    """
    d = load(key)
    parts, labels = [], []
    for i in d.get("items") or []:
        if i.get("status") == "armed" and i.get("flags"):
            parts.append(i["flags"])
            labels.append(i["label"])
    return " ".join(parts), labels


def mark_launched(key: str, labels: list[str]) -> None:
    d = load(key)
    for i in d.get("items") or []:
        if i["label"] in labels:
            i["status"] = "launched"
            i["deployed_at"] = time.time()
            i["note"] = (i.get("note", "").split(" — ")[0]
                         + " — launched with the flag; verify to confirm it landed")
    save(key, d)


def asset_path(key: str, item_id: str) -> Path:
    return ASSETS / f"{re.sub(r'[^A-Za-z0-9._-]', '_', key)}-{item_id}.txt"


def live_labels(key: str, sid: str) -> set:
    """Which config deployments the RUNNING session actually carries, right now.

    Read from the live process, never from a stored status. A status is a record
    of one past check: relaunch the session with a different flag and the old row
    still claims to be in effect while the process has already dropped it.
    """
    cmd = _live_command(sid)
    if not cmd:
        return set()
    out = set()
    for i in (load(key).get("items") or []):
        if i.get("kind") in ("system_prompt_file", "append_system_prompt") \
                and str(asset_path(key, i["id"])) in cmd:
            out.add(i["label"])
    return out


_PS: tuple = (0.0, "")


def _ps_snapshot(ttl: float = 1.5) -> str:
    """Process table, memoised briefly -- the pulse asks for this every tick."""
    global _PS
    now = time.monotonic()
    if _PS[1] and now - _PS[0] < ttl:
        return _PS[1]
    try:
        out = subprocess.run(["ps", "-eo", "pid=,args="], capture_output=True,
                             text=True, timeout=5).stdout
    except Exception:                       # Windows, or no ps -- not fatal
        out = ""
    _PS = (now, out)
    return out


def _live_command(sid: str) -> str:
    """The command line of a running process for this session, if there is one."""
    out = _ps_snapshot()
    # Match the launch signature, not just the id: any tool that merely mentions
    # the session -- a curl to this very endpoint, for one -- carries the id too.
    needle = f"claude --resume {sid}"
    for line in out.splitlines():
        if needle in line:
            return line.strip()
    return ""


CONFIG_KINDS = ("system_prompt_file", "append_system_prompt")


def derive(key: str, sid: str, d: dict | None = None) -> dict:
    """The deployment list with config statuses re-derived from the live process.

    A stored status is a record of one past check. The canvas already derived
    `live` on every read while this page kept showing the stored field, so the
    same deployment could read "dropped" in Explorer and "in effect" here. One
    source of truth: what the running session actually carries, asked fresh.

    Only config kinds are derived. A transcript deployment is `deployed` because
    the file was rewritten -- that is a fact about the past and no process can
    change it.
    """
    d = d if d is not None else load(key)
    live = live_labels(key, sid) if sid else set()
    out = []
    for i in d.get("items") or []:
        if i.get("kind") not in CONFIG_KINDS:
            out.append(i)
            continue
        i = dict(i, stored_status=i.get("status"))
        if i["label"] in live:
            i["status"] = "in_effect"
        elif i.get("status") in ("in_effect", "launched"):
            i["status"] = "dropped"
            i["note"] = ("no running session carries this flag \u2014 it was "
                         "relaunched without it, or stopped")
        out.append(i)
    return dict(d, items=out, live=sorted(live))


FLAG_TO_MODE = {"append_system_prompt": "append", "system_prompt_file": "replace"}


def convert_to_inject(key: str, label: str) -> dict:
    """Turn a flag-based config deployment into an injection, keeping its label.

    The flag mechanism was the only one we had before injection was shown to
    work. It is the weaker one: a flag must be re-passed on every launch and the
    next launch drops it silently. The text is identical either way, so a row
    that has not been applied can simply change mechanism in place -- and keep
    its label, because the user refers to these by name.

    An APPLIED row is refused. Its status is a fact about something that already
    happened; rewriting its kind would make the history lie.
    """
    d = load(key)
    i = find(d, label)
    if not i:
        return {"ok": False, "error": f"no deployment {label!r}"}
    mode = FLAG_TO_MODE.get(i.get("kind") or "")
    if not mode:
        return {"ok": False, "error": f"{i['label']} is {i.get('kind')!r}, "
                                      "which is not a flag-based config change"}
    if i.get("status") == "deployed":
        return {"ok": False, "error": f"{i['label']} was already applied; "
                                      "stage a new injection instead"}
    text = (i.get("payload") or {}).get("text") or ""
    if not text:
        return {"ok": False, "error": f"{i['label']} carries no text"}
    i["kind"] = "inject_system_prompt"
    i["payload"] = {"text": text, "mode": mode}
    i["status"] = "staged"
    i["deployed_at"] = None
    i.pop("flags", None)
    i.pop("stored_status", None)
    i["note"] = (f"converted from {mode == 'append' and 'append_system_prompt' or 'system_prompt_file'}"
                 " — now written into the recorded prompt, so it survives a relaunch")
    return {"ok": True, "item": i, "deploy": save(key, d)}


def verify_config(key: str, label: str, ref) -> dict:
    """Is the change actually in the session's package? Two ways to know.

    Reading the transcript alone is NOT enough, and assuming it was is a trap
    worth spelling out: the flag that makes a config change apply to an existing
    conversation is `--system-prompt-snapshot off`, and *off means never record*.
    So the moment the change can work, the transcript stops carrying the evidence
    for it -- an earlier version of this function reported "not in effect" for a
    session that was running with the flag right there in its argv.

    So: look at the running process first, then fall back to the recorded
    snapshot for sessions that keep one.
    """
    d = load(key)
    item = find(d, label)
    if not item:
        return {"ok": False, "error": f"no deployment {label!r}"}
    text = (item.get("payload") or {}).get("text") or ""
    if not text:
        return {"ok": False, "error": "nothing to verify"}
    probe = " ".join(text.split())[:80]
    newest = None
    try:
        with open(ref.transcript, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                try:
                    r = json.loads(raw)
                except ValueError:
                    continue
                a = r.get("attachment") or {}
                if r.get("type") == "attachment" and a.get("type") == "prompt_snapshot":
                    newest = a.get("systemPrompt")
    except OSError as e:
        return {"ok": False, "error": str(e)}
    blob = " ".join(newest) if isinstance(newest, list) else str(newest or "")
    recorded = probe in " ".join(blob.split())

    asset = str(ASSETS / f"{re.sub(r'[^A-Za-z0-9._-]', '_', key)}-{item['id']}.txt")
    cmd = _live_command(ref.sid)
    live = bool(cmd) and asset in cmd
    landed = live or recorded
    # Not landed means not in effect, whatever the row said before -- including
    # rows written by the older code that called this "deployed" on the strength
    # of having written a file.
    item["status"] = "in_effect" if landed else "armed"
    how = ("the running session was launched with this flag"
           + (" (--system-prompt-snapshot off, so it is applied fresh every "
              "request and deliberately not recorded in the transcript)"
              if "--system-prompt-snapshot off" in cmd else "") if live
           else "found in the session's recorded system prompt" if recorded else "")
    if not item.get("flags"):
        f = ASSETS / f"{re.sub(r'[^A-Za-z0-9._-]', '_', key)}-{item['id']}.txt"
        flag = ("--system-prompt-file" if item["kind"] == "system_prompt_file"
                else "--append-system-prompt-file")
        item["flags"] = f'--system-prompt-snapshot off {flag} "{f}"'
    if not landed:
        item["deployed_at"] = None
    item["note"] = (how if landed
                    else "not in effect: no running session carries this flag, and "
                         "it is not in the recorded system prompt. Run the session "
                         "with the flags below.")
    save(key, d)
    return {"ok": True, "landed": landed, "status": item["status"],
            "live": live, "recorded": recorded,
            "command": cmd, "note": item["note"]}


def run(ref, key: str = "") -> dict:
    """Relaunch the session under its OWN id -- a resume, never a fork."""
    extra, labels = armed_flags(key) if key else ("", [])
    cmd = f"claude --resume {ref.sid}" + (f" {extra}" if extra else "")
    cwd = ref.cwd or str(Path.home())
    try:
        if sys.platform == "darwin":
            script = (f'tell application "Terminal" to do script '
                      f'"cd {json.dumps(cwd)[1:-1]} && {cmd}"')
            subprocess.Popen(["osascript", "-e", script,
                              "-e", 'tell application "Terminal" to activate'])
            if labels:
                mark_launched(key, labels)
            return {"ok": True, "command": cmd, "launched": "Terminal",
                    "carried": labels}
    except Exception:
        pass
    return {"ok": True, "command": cmd, "launched": None, "carried": labels,
            "note": "Run this yourself -- same session id, so it resumes rather than forks."}
