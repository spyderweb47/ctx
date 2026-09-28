"""Ask the context one question and keep the answer.

An extraction is the stage before thinking: it gathers the records that mention
something and holds them as an ordered set, so the analysis that follows works
on a named subset rather than on "the session" in the abstract.

What comes back is not a copy of the text. Each hit carries the bucket and index
it came from, so it stays the same addressable item Explorer shows -- `K85` here
is `K85` there, and opening it reads the transcript, not a snapshot that can
drift out of date.
"""
from __future__ import annotations

import re

from .adapters import ADAPTERS
from .base import BUCKETS, CONVERSATION_KINDS
from .labels import LabelStore, column, item_label

_labels = LabelStore()

MAX_HITS = 200
EXCERPT = 240


def _excerpt(text: str, at: int, width: int = EXCERPT) -> str:
    """The match with enough either side to be worth reading."""
    half = width // 2
    start = max(0, at - half)
    end = min(len(text), at + half)
    body = text[start:end].replace("\n", " ").strip()
    return ("…" if start else "") + body + ("…" if end < len(text) else "")


def run(ref, query: str, *, buckets: list | None = None, regex: bool = False,
        limit: int = MAX_HITS) -> dict:
    """Every record matching `query`, in the order the session produced them.

    `buckets` narrows the search to particular bucket keys; the default searches
    everything that carries text. Matching is case-insensitive substring unless
    `regex` is set, in which case the pattern is used as given.
    """
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "an extraction needs something to look for"}
    try:
        pat = re.compile(q if regex else re.escape(q), re.I)
    except re.error as e:
        return {"ok": False, "error": f"bad pattern: {e}"}

    mod = ADAPTERS[ref.adapter]
    segs, _ = mod.parse(ref.transcript)

    # Rank inside each bucket exactly as the ledger does, so the index we hand
    # back addresses the same item Explorer draws.
    grouped: dict[str, list] = {}
    for s in segs:
        grouped.setdefault(s.kind, []).append(s)

    ns = f"buckets:{ref.key}"
    want = set(buckets) if buckets else None
    hits, scanned, truncated = [], 0, False
    for spec in BUCKETS:
        items = grouped.get(spec.key)
        if not items or (want and spec.key not in want):
            continue
        letter = column(_labels.index(ns, spec.key))
        for i, seg in enumerate(sorted(items, key=lambda x: -x.tokens)):
            scanned += 1
            if not seg.text:
                continue
            m = pat.search(seg.text)
            if not m:
                continue
            if len(hits) >= limit:
                truncated = True
                break
            hits.append({
                "bucket": spec.key, "bucket_title": spec.title,
                "bucket_label": letter, "i": i, "label": item_label(letter, i),
                "title": (seg.title or "").replace("\n", " ")[:70],
                "line": seg.line, "turn": seg.turn, "tokens": seg.tokens,
                "chars": seg.chars, "hits": len(pat.findall(seg.text)),
                "excerpt": _excerpt(seg.text, m.start()),
                "conversation": spec.key in CONVERSATION_KINDS,
            })
        if truncated:
            break

    hits.sort(key=lambda h: h["line"])          # the order it happened
    return {"ok": True, "query": q, "regex": bool(regex),
            "buckets": sorted(want) if want else [],
            "matches": hits, "count": len(hits),
            "tokens": sum(h["tokens"] for h in hits),
            "scanned": scanned, "truncated": truncated}


def text_of(ref, bucket: str, index: int, cap: int = 20000) -> str:
    """The full text behind one hit, for an analysis cell to work on."""
    from . import ledger
    d = ledger.bucket_text(ref, bucket, index)
    return "" if d.get("error") else (d.get("text") or "")[:cap]
