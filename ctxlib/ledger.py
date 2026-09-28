"""Turn one session's segments into labelled buckets with honest accounting.

The tool is scoped to a single session at a time, so nothing here labels a
session -- only the buckets inside it, and the items inside those.
"""
from __future__ import annotations

import math

from . import deploy as _deploy
from .adapters import ADAPTERS
from .base import (BUCKET_BY_KEY, BUCKETS, CONTEXT, CONVERSATION_KINDS,
                   EDIT_CLASS_COLOR, EDIT_CLASS_INFO, EPHEMERAL, PROSE, Segment)
from .labels import LabelStore, column, item_label

_labels = LabelStore()


def describe_session(ref) -> dict:
    """Identity only. Sessions are chosen, not labelled."""
    return {
        "key": ref.key, "adapter": ref.adapter, "sid": ref.sid,
        "name": ref.name, "cwd": ref.cwd, "status": ref.status,
        "transcript": ref.transcript, "updated_at": ref.updated_at,
        "pid": ref.pid,
    }


def _exact_total(usage: dict) -> int | None:
    """The provider's own number. Exact, unlike our per-segment estimates."""
    if not usage:
        return None
    for keys in (("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"),
                 ("total_tokens",), ("total_token_usage",)):
        vals = [usage.get(k) for k in keys]
        if any(isinstance(v, (int, float)) for v in vals):
            return int(sum(v for v in vals if isinstance(v, (int, float))))
    for v in usage.values():
        if isinstance(v, dict):
            got = _exact_total(v)
            if got:
                return got
    return None


def _reasoning_generated(usage) -> int:
    """Reasoning tokens the model produced over this session's life.

    Deduplicated by request, because one response is written as several records
    each carrying a copy of the same usage. This is OUTPUT, not context: the
    text was never stored and is never re-sent. Reported as a fact about the
    session, never added to any total.
    """
    return int((usage or {}).get("ctx_thinking_total") or 0)


def _sequence(segs, index, scale: float, labels: dict) -> list[dict]:
    """Every turn-by-turn record in the order it happened, across all five kinds.

    Explorer fans this out whenever one of those kinds is open, lighting the one
    you picked and dimming the rest. Drilling User Turns then shows your
    questions in place -- with the replies, calls and results still around them,
    greyed -- instead of a list of questions with the answers somewhere else.

    Each entry keeps the label it has in its own bucket, so what you say out
    loud means the same thing in either reading.
    """
    calls, results = {}, {}
    for sg in segs:
        m = sg.meta or {}
        if sg.kind == "tool_calls" and m.get("id"):
            calls[m["id"]] = sg
        elif sg.kind == "tool_results" and m.get("tool_use_id"):
            results[m["tool_use_id"]] = sg

    out = []
    for sg in sorted([x for x in segs if x.kind in CONVERSATION_KINDS],
                     key=lambda x: x.line):
        src = index.get(id(sg))
        if not src:
            continue
        row = {"kind": sg.kind, "label": labels.get(src["bucket"], "?"),
               "title": (sg.title or "").strip()[:70], "line": sg.line,
               "tokens": round(sg.tokens * scale), "src": src,
               "model": (sg.meta or {}).get("model") or ""}
        row["label"] = item_label(row["label"], src["i"])
        if sg.kind == "tool_calls":
            row["tool"] = (sg.meta or {}).get("tool") or "tool"
            res = results.get((sg.meta or {}).get("id") or "")
            if res is not None:
                row["result_src"] = index.get(id(res))
                row["result_tokens"] = round(res.tokens * scale)
        out.append(row)
    for n, r in enumerate(out):
        r["i"] = n
    return out


def _exchanges(segs, index, scale: float) -> list[dict]:
    """The conversation resequenced as exchanges, in the order it happened.

    A bucket view answers "what is eating the window". It cannot answer "what
    did I ask, what did it do, and what came back", because the four halves of
    one exchange sit in four different buckets. This regroups them: one band per
    request, holding that request's reasoning, reply, and every tool call welded
    to the result it produced.

    The pairing is by tool_use id, not by position -- calls and results
    interleave, and a result can land several records after its call.
    """
    calls: dict[str, Segment] = {}
    results: dict[str, Segment] = {}
    for sg in segs:
        if sg.kind == "tool_calls" and (sg.meta or {}).get("id"):
            calls[sg.meta["id"]] = sg
        elif sg.kind == "tool_results" and (sg.meta or {}).get("tool_use_id"):
            results[sg.meta["tool_use_id"]] = sg
    paired = {id(results[k]) for k in results if k in calls}

    IN_EXCHANGE = {"user_turns", "assistant_turns", "thinking",
                   "tool_calls", "tool_results", "compact_summary"}
    ordered = sorted([sg for sg in segs if sg.kind in IN_EXCHANGE],
                     key=lambda x: x.line)

    out: list[dict] = []
    cur: dict | None = None

    def start(title: str, kind: str) -> dict:
        d = {"key": f"x{len(out) + 1}", "label": str(len(out) + 1),
             "title": title, "kind": kind, "model": "", "tokens": 0,
             "steps": [], "line": 0}
        out.append(d)
        return d

    for sg in ordered:
        # A real user message opens an exchange. Tool results also arrive as
        # role "user", which is why the turn counter cannot be the boundary.
        if sg.kind in ("user_turns", "compact_summary") or cur is None:
            cur = start((sg.title or sg.kind).strip()[:70] or "(empty)",
                        "compact" if sg.kind == "compact_summary" else "request")
            cur["line"] = sg.line
        if sg.kind == "tool_results" and id(sg) in paired:
            continue                      # shown on its call, not on its own
        mdl = (sg.meta or {}).get("model")
        if mdl and not cur["model"]:
            cur["model"] = mdl
        step = {"kind": sg.kind, "title": (sg.title or "").strip()[:70],
                "tokens": round(sg.tokens * scale), "line": sg.line,
                "src": index.get(id(sg))}
        if sg.kind == "tool_calls":
            step["tool"] = (sg.meta or {}).get("tool") or "tool"
            res = results.get((sg.meta or {}).get("id") or "")
            if res is not None:
                step["result_tokens"] = round(res.tokens * scale)
                step["result_src"] = index.get(id(res))
                step["tokens"] += step["result_tokens"]
        cur["steps"].append(step)
        cur["tokens"] += step["tokens"]

    for n, x in enumerate(out):
        for i, st in enumerate(x["steps"]):
            st["i"] = i
            st["label"] = f"{x['label']}.{i + 1}"
    return out

# Which bucket a staged change lands in, so Explorer can show the section as it
# is AND as it would be, side by side, instead of only as it is.
PENDING_BASE = 10000          # pending items are indexed above every real one
PENDING_BUCKET = {"system_prompt_file": "system_prompt",
                  "append_system_prompt": "system_prompt"}


def _pending(ref) -> dict[str, list[dict]]:
    """Config changes that are written but not yet in the package.

    They are drawn in the fan beside the real sections, marked, so the
    difference between what the session has and what you have prepared for it
    is visible in the same place you read the context.
    """
    out: dict[str, list[dict]] = {}
    key = ref.key
    try:
        items = (_deploy.load(key) or {}).get("items") or []
        live = _deploy.live_labels(key, ref.sid)
    except Exception:
        return out
    for i in items:
        bucket = PENDING_BUCKET.get(i.get("kind") or "")
        if not bucket or i.get("status") in (None, "staged"):
            continue
        text = (i.get("payload") or {}).get("text") or ""
        out.setdefault(bucket, []).append({
            "deploy": i.get("label"),
            # Derived, not stored. A row that was in effect under an earlier
            # launch is not in effect now if the running session dropped its
            # flag -- which is exactly what a second config deployment does.
            "status": ("in_effect" if i.get("label") in live
                       else "dropped" if i.get("status") in ("in_effect", "launched")
                       else i.get("status")),
            # in_effect means the session is running with the flag, so this text
            # IS in the package -- it just arrived by launch config instead of
            # through the transcript, which is why parsing alone cannot see it.
            "live": i.get("label") in live,
            "kind": i.get("kind"), "title": i.get("title") or i.get("label"),
            "text": text[:40000], "chars": len(text),
            "replaces": i.get("kind") == "system_prompt_file",
        })
    return out


def build(ref) -> dict:
    mod = ADAPTERS[ref.adapter]
    segs, usage = mod.parse(ref.transcript)

    grouped: dict[str, list[Segment]] = {}
    for s in segs:
        grouped.setdefault(s.kind, []).append(s)

    # The context total counts what is actually in the package. Ephemeral
    # buckets are reported beside it, never inside it.
    eph_keys = {b.key for b in BUCKETS if b.category == EPHEMERAL}
    pending = _pending(ref)
    live_extra = sum(math.ceil(pj["chars"] / PROSE)
                     for lst in pending.values() for pj in lst if pj["live"])
    # --system-prompt-file REPLACES the prompt, so when one is live the
    # transcript's own sections are no longer being sent. Counting both would
    # invent context that does not exist.
    superseded = {b for b, lst in pending.items()
                  if any(pj["live"] and pj["replaces"] for pj in lst)}
    raw_total = (sum(s.tokens for s in segs
                     if s.kind not in eph_keys and s.kind not in superseded)
                 + live_extra) or 1

    # Calibration. Measured densities get the SHAPE right -- which bucket is
    # heavy, and by how much relative to the others -- but no character rule
    # reproduces a tokenizer, and what a given CLI version actually packs varies
    # (signatures, retained reasoning, injections we cannot see). We do have one
    # true number: the provider's own input count for the last call. So the
    # shares come from the measurement and the total comes from the provider,
    # and the factor between them is shown rather than buried.
    exact = _exact_total(usage)
    scale = (exact / raw_total) if (exact and raw_total) else 1.0
    est_total = exact if exact else raw_total
    ns = f"buckets:{ref.key}"
    seg_index: dict[int, dict] = {}
    buckets = []
    for spec in BUCKETS:
        items = grouped.get(spec.key)
        if not items:
            continue
        live_here = sum(math.ceil(pj["chars"] / PROSE)
                        for pj in (pending.get(spec.key) or []) if pj["live"])
        gone = spec.key in superseded
        tok = round(((0 if gone else sum(i.tokens for i in items)) + live_here) * scale)
        info = EDIT_CLASS_INFO[spec.edit_class]
        letter = column(_labels.index(ns, spec.key))
        order = sorted(items, key=lambda x: -x.tokens)
        for n, it in enumerate(order):
            seg_index[id(it)] = {"bucket": spec.key, "i": n}
        ranked = order[:200]
        buckets.append({
            "key": spec.key, "title": spec.title, "label": letter,
            "edit_class": spec.edit_class, "badge": info["badge"],
            "color": EDIT_CLASS_COLOR[spec.edit_class],
            "restart": info["restart"], "how": info["how"],
            "class_title": info["title"], "note": spec.note,
            "tokens": tok, "chars": sum(i.chars for i in items),
            # what is actually drawn: the real sections still being sent, plus
            # every injection attached to this bucket
            "count": (0 if gone else len(items)) + len(pending.get(spec.key) or []),
            # replaced outright by a live --system-prompt-file: still on disk,
            # no longer sent, so it is reported here and drawn nowhere
            "superseded": gone,
            "superseded_count": len(items) if gone else 0,
            "superseded_tokens": round(sum(i.tokens for i in items) * scale) if gone else 0,
            "pct": round(100 * tok / est_total, 1),
            "category": spec.category, "editable": spec.editable,
            "items": [
                {"i": n, "label": item_label(letter, n), "imgs": len(i.images),
                 "title": (i.title or "").replace("\n", " ")[:70],
                 "tokens": i.tokens, "chars": i.chars, "turn": i.turn,
                 "line": i.line, "meta": i.meta}
                for n, i in enumerate([] if gone else ranked)
            ] + [
                # not in the package: what this section WOULD hold
                {"i": PENDING_BASE + k, "label": f"{letter}+{k + 1}", "imgs": 0,
                 "pending": True, "live": pj["live"],
                 "deploy": pj["deploy"], "status": pj["status"],
                 "replaces": pj["replaces"], "text": pj["text"],
                 "title": ("replaces" if pj["replaces"] else "adds") + " \u00b7 " + pj["title"][:56],
                 "tokens": round(math.ceil(pj["chars"] / PROSE) * scale),
                 "chars": pj["chars"], "turn": 0, "line": 0, "meta": {}}
                for k, pj in enumerate(pending.get(spec.key) or [])
            ],
        })
    # Three surfaces, from one pass:
    #   buckets   -- in the package AND changeable. Explorer draws these.
    #   fixed     -- in the package, no lever. Counted, not drawn.
    #   ephemeral -- not in the package at all.
    ephemeral = [b for b in buckets if b["category"] == EPHEMERAL]
    ctx_b = [b for b in buckets if b["category"] == CONTEXT]
    fixed = sorted([b for b in ctx_b if not b["editable"]], key=lambda b: b["label"])
    buckets = sorted([b for b in ctx_b if b["editable"]], key=lambda b: b["label"])

    return {
        "session": describe_session(ref),
        "buckets": buckets,
        # Not in the window. Shown so you can see what reasoning cost, never
        # added to est_total.
        "ephemeral": ephemeral,
        # The five turn-by-turn kinds, merged in the order they happened.
        "sequence": _sequence(segs, seg_index, scale,
                              {b["key"]: b["label"] for b in buckets + fixed}),
        "conversation_kinds": list(CONVERSATION_KINDS),
        # In the package but with no lever -- reported, never drawn as
        # something you could act on.
        "fixed": fixed,
        "editable_total": sum(b["tokens"] for b in buckets),
        "fixed_total": sum(b["tokens"] for b in fixed),
        "cut": usage.get("ctx_cut") or {},
        "signature_chars": int(usage.get("ctx_signature_chars") or 0),
        # Output, not context. Never summed into est_total.
        "reasoning_generated": _reasoning_generated(usage),
        "requests": int(usage.get("ctx_requests") or 0),
        "est_total": est_total,
        "exact_total": exact,
        # What the character rules alone said, before the provider's number was
        # applied, and by how much they were out. Kept visible: a calibration
        # far from 1.0 means the shares are less trustworthy too.
        "raw_total": raw_total,
        "calibrated": bool(exact),
        "calibration": round(scale, 3) if exact else None,
        "drift_pct": (round(100 * (raw_total - exact) / exact, 1) if exact else None),
        "segment_count": len(segs),
        "needs_restart": sorted({b["label"] for b in buckets if b["restart"]}),
    }


def bucket_text(ref, key: str, index: int) -> dict:
    mod = ADAPTERS[ref.adapter]
    segs, usage = mod.parse(ref.transcript)

    # A prepared change is a real, addressable item in the fan (A+1), so it has
    # to resolve here too. It used to 404, and a client that fetched it got an
    # empty body under a filled-in header -- the change looked like it had no
    # content when the content was simply never served.
    if index >= PENDING_BASE:
        pend = (_pending(ref).get(key) or [])
        k = index - PENDING_BASE
        if not 0 <= k < len(pend):
            return {"error": "no such pending change"}
        pj = pend[k]
        spec = BUCKET_BY_KEY.get(key)
        return {"images": [], "image_tokens": 0, "pending": True,
                "live": pj["live"], "deploy": pj["deploy"], "status": pj["status"],
                "replaces": pj["replaces"],
                "title": pj["title"], "text": pj["text"],
                "tokens": math.ceil(pj["chars"] / PROSE), "chars": pj["chars"],
                "turn": 0, "line": 0, "meta": {"deploy": pj["deploy"]},
                "transcript": ref.transcript,
                "edit_class": spec.edit_class if spec else "config"}

    items = sorted([s for s in segs if s.kind == key], key=lambda x: -x.tokens)
    if not 0 <= index < len(items):
        return {"error": "no such item"}
    s = items[index]
    spec = BUCKET_BY_KEY.get(key)
    # An item with no text should say why, not open blank. Thinking is the only
    # kind where emptiness is the normal, expected state.
    why = ""
    if key == "thinking" and not s.text:
        sig = (s.meta or {}).get("signature_chars") or 0
        why = ("Claude Code does not store reasoning text. This block kept a "
               f"{sig:,}-character signature and discarded the body, so there is "
               "nothing to show — the bytes were never written. Measured across "
               "this store: 65 of 5,745 thinking blocks have text (1.1%), and "
               "only from two older CLI builds.\n\n"
               "The size is not nothing, though: the signature is resident, at "
               "~1 token per 3.21 chars, which is what this row costs. To reclaim "
               "it, drop old thinking blocks in a transcript rewrite — keep the "
               "ones in the turn still in flight.")
    return {
        "no_text": bool(why), "why": why,
        "images": s.images, "image_tokens": s.extra_tokens,
        "title": s.title, "text": s.text, "tokens": s.tokens, "chars": s.chars,
        "turn": s.turn, "line": s.line, "meta": s.meta,
        "transcript": ref.transcript,
        "edit_class": spec.edit_class if spec else "restart",
    }
