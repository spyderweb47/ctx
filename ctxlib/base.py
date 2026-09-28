"""Normalized context model, shared by every adapter.

Everything an adapter produces is a Segment. Segments group into Buckets.
A Bucket's edit_class is the single most important field in this tool: it tells
you whether changing it costs you a restart, and by what mechanism.
"""
from __future__ import annotations

import base64
import math
import os
import struct
from dataclasses import dataclass, field

# --- edit classes -----------------------------------------------------------
# LIVE    : annotate/steer now. Nothing on disk changes. No restart.
# RESTART : lives in the transcript. Rewrite the .jsonl, then resume the SAME
#           session id. Not a fork. Requires the session to be stopped first,
#           because the running process holds the conversation in RAM and will
#           keep appending from its own memory.
# CONFIG  : NOT fixable by rewriting the transcript. The transcript only records
#           what was sent; the CLI regenerates these from config + launch flags
#           on every start. Delete it from the .jsonl and it comes straight back.
LIVE, RESTART, CONFIG = "live", "restart", "config"

# Colors encode STATE, so they are a status palette, not a categorical one.
# Validated with the dataviz validator against the #181b23 dark surface:
# lightness band, chroma floor, CVD separation (worst pair dE 16.0, target >= 8),
# normal-vision floor and contrast all PASS. Every band also carries its letter,
# title and badge text, so state is never conveyed by color alone.
EDIT_CLASS_COLOR = {
    "live": "#0aa89a",      # teal
    "restart": "#b45309",   # burnt amber
    "config": "#7c6df2",    # violet
}

EDIT_CLASS_INFO = {
    LIVE: {
        "badge": "LIVE",
        "restart": False,
        "title": "Editable now",
        "how": "Annotate or steer in place. Nothing is rewritten, so the running "
               "session keeps going untouched.",
    },
    RESTART: {
        "badge": "NEEDS RESTART",
        "restart": True,
        "title": "Transcript rewrite",
        "how": "Stop the session, rewrite its transcript, then resume under the "
               "SAME session id. This is not a fork -- same id, same file. You "
               "lose only ephemeral state (the TUI process, shell cwd, in-flight "
               "background tasks) and the first turn re-bills the prompt cache.",
    },
    CONFIG: {
        "badge": "NEEDS RESTART + FLAGS",
        "restart": True,
        "title": "Launch config only",
        "how": "This is regenerated at launch, so editing the transcript does "
               "nothing -- it reappears. Change it with launch flags instead, "
               "then restart.",
    },
}


def image_tokens(w: int, h: int) -> int:
    """What an image actually costs on the wire.

    Providers bill images by area, not by payload: roughly (w x h) / 750 after
    the long edge is scaled down to 1568px. Measuring the base64 instead
    overcounts by ~50x -- a 2000x1566 PNG is 1.2M base64 characters but only
    ~4.2k tokens.
    """
    m = max(w, h)
    if m > 1568:
        s = 1568 / m
        w, h = w * s, h * s
    return min(1600, max(1, int(w * h / 750)))


def _dims(b64: str) -> tuple[int, int] | None:
    """Read PNG/GIF dimensions from the header alone -- no full decode."""
    try:
        head = base64.b64decode(b64[:64] + "==", validate=False)
    except Exception:
        return None
    if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
        return struct.unpack(">II", head[16:24])
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", head[6:10])
    return None


DEFAULT_IMAGE_TOKENS = 1100          # a typical screenshot when dims are unreadable


MAX_IMAGES = 12          # per segment, so one item cannot flood the panel


def strip_images(node):
    """Lift image payloads out of the text, keeping them for display.

    Returns (clean_node, image_tokens, images). The base64 leaves the text so it
    neither distorts the token estimate nor floods the renderer, but it is not
    thrown away -- each image comes back as a data URI the panel can show.
    """
    total = 0
    images: list[dict] = []

    def walk(n):
        nonlocal total
        if isinstance(n, list):
            return [walk(x) for x in n]
        if isinstance(n, dict):
            if n.get("type") == "image":
                src = n.get("source") or {}
                data = src.get("data") or ""
                if data:
                    wh = _dims(data)
                    tok = image_tokens(*wh) if wh else DEFAULT_IMAGE_TOKENS
                    total += tok
                    kb = len(data) * 3 // 4096
                    label = f"{wh[0]}x{wh[1]}" if wh else "unknown size"
                    if len(images) < MAX_IMAGES:
                        mime = src.get("media_type") or "image/png"
                        images.append({"uri": f"data:{mime};base64,{data}",
                                       "w": wh[0] if wh else None,
                                       "h": wh[1] if wh else None,
                                       "kb": kb, "tokens": tok})
                    return {"type": "image",
                            "image": f"<{label} · {kb} KB · {tok} tokens>"}
            return {k: walk(v) for k, v in n.items()}
        return n

    return walk(node), total, images


@dataclass
class Segment:
    """One attributable piece of context."""
    kind: str                  # bucket key
    title: str                 # short human line
    text: str                  # raw content, for the detail page
    turn: int = 0
    line: int = 0              # 1-based line in the transcript, for "open file"
    meta: dict = field(default_factory=dict)
    extra_tokens: int = 0      # images: billed by area, not by payload length
    images: list = field(default_factory=list)   # data URIs lifted out of `text`

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def tokens(self) -> int:
        # Estimate. Turn totals from the provider's own usage field are exact;
        # per-segment numbers are not. The UI says so rather than laundering it.
        d = DENSITY.get(self.kind, PROSE)
        return (math.ceil(len(self.text) / d) if self.text else 0) + self.extra_tokens


# The two things a session is made of. They are not the same kind of thing and
# must never be added together:
#
#   CONTEXT   -- persistent. Stored in the transcript and re-sent in every
#                package from here on. This is the context window.
#   EPHEMERAL -- transient. Generated during one turn, used to produce that
#                turn's answer, then dropped. The store keeps only a signature;
#                the text is never written. It is not part of the window.
CONTEXT = "context"
EPHEMERAL = "ephemeral"


# Chars per token, measured -- not assumed.
#
# The old flat 4.0 came from nowhere and ran ~50% under the provider's own
# count. These come from 1,836 clean request-to-request deltas across this
# store: pairs where the content added between two API calls was >=92% a single
# kind, so chars/token falls straight out of the input growth. Quartiles were
# tight (json 1.64-2.17, prose 2.33-2.72, signature 3.06-3.33), which is why
# three constants are enough and a fitted curve is not.
JSONISH = 1.89          # tool inputs, tool results, schemas -- punctuation-dense
PROSE = 2.49            # prose and markdown
SIGNATURE = 3.21        # base64 thinking signatures

DENSITY: dict[str, float] = {
    "tool_calls": JSONISH,
    "tool_results": JSONISH,
    "tool_schemas": JSONISH,
    "file_reads": JSONISH,
    "environment": JSONISH,
    "hooks": JSONISH,
    "events": JSONISH,
    "other": JSONISH,
}


@dataclass
class BucketSpec:
    key: str
    title: str
    edit_class: str
    note: str = ""
    category: str = CONTEXT
    # Is there a lever that changes what this contributes to the NEXT package?
    # Explorer shows only buckets where this is true: the point of the tool is
    # to edit the context, and a bucket you cannot change is scenery. The others
    # are still counted in the total -- they are in the package -- but they are
    # reported as one fixed number instead of drawn as something to act on.
    editable: bool = True


# Ordered: the canvas lays buckets out in this order around the root.
BUCKETS: list[BucketSpec] = [
    # VERIFIED 2026-09-21, not assumed: the CLI records the system prompt on a
    # conversation's first request and replays that record on every resume, so
    # editing the record in the transcript changes what the session sends.
    # Injected a codeword into a stopped session's prompt_snapshot, resumed it,
    # asked for the codeword: it answered with the injected word. This is a
    # TRANSCRIPT edit, not a launch-flag one -- and it is the better lever,
    # because a flag must be re-passed on every launch and the next launch
    # drops it silently, while a record simply stays.
    BucketSpec("system_prompt", "System Prompt", RESTART,
               "Inject into the recorded prompt (survives relaunches), or "
               "override per launch with --system-prompt-file / "
               "--append-system-prompt. A launch with "
               "--system-prompt-snapshot off ignores the record and renders "
               "fresh; a compaction re-renders it."),
    BucketSpec("tool_schemas", "Tool Schemas", CONFIG,
               "--mcp-config to drop servers, --allowedTools/--disallowedTools "
               "to narrow the set, --bare for the minimum. Often the single "
               "largest fixed cost before you type a word."),
    BucketSpec("mcp_instructions", "MCP Instructions", CONFIG,
               "--mcp-config, or drop the server. Each connected server prepends "
               "its own instructions whether or not you call it."),
    BucketSpec("skills", "Skill Listing", CONFIG,
               "--bare, --plugin-dir, or enabledPlugins in settings.json. Every "
               "installed skill costs its description before you use one."),
    BucketSpec("agents", "Agent Listing", CONFIG,
               "--agents, or remove the agent files it lists."),
    BucketSpec("project_instructions", "Project Instructions", CONFIG,
               "CLAUDE.md / AGENTS.md on disk, or --add-dir"),
    BucketSpec("environment", "Environment", CONFIG,
               "Injected at launch from cwd, model, date and mode. --model and "
               "--add-dir change what it says; nothing removes the block.",
               editable=False),
    BucketSpec("compact_summary", "Compact Summary", RESTART,
               "What survived the last compaction. Everything before it was "
               "discarded by the CLI and is not in the package, however much "
               "of it the transcript still holds."),
    BucketSpec("user_turns", "User Turns", RESTART,
               "Rewrite the record. Your own words are usually worth keeping "
               "verbatim -- they are what the rest of the context is for."),
    BucketSpec("assistant_turns", "Assistant Turns", RESTART,
               "Rewrite the record. Long explanations compress well; decisions "
               "and commitments do not."),
    BucketSpec("thinking", "Thinking (signatures)", RESTART,
               "The reasoning TEXT is never stored and never re-sent. The "
               "signature is, and it is resident: measured at ~1 token per 3.21 "
               "chars of signature. Delete old thinking blocks in a rewrite to "
               "reclaim it -- keep the ones in the turn still in flight."),
    BucketSpec("tool_calls", "Tool Calls", RESTART,
               "Rewrite the input. Keep the tool_use id -- its tool_result is "
               "paired to it, and an unpaired block is a 400."),
    BucketSpec("tool_results", "Tool Results", RESTART,
               "Usually the biggest reclaimable bucket. Replace with a summary "
               "rather than deleting, to keep the tool_use/tool_result pairing."),
    BucketSpec("file_reads", "File Reads", RESTART,
               "Watch for the same file read several times, or read then edited."),
    BucketSpec("reminders", "Reminders", RESTART,
               "Re-injected by the CLI on every turn. Deleting them from the "
               "transcript removes the history, not the next one.",
               editable=False),
    BucketSpec("hooks", "Hook Injections", RESTART,
               "hooks in settings.json -- the harness runs them, so stopping "
               "the injection means changing the hook, not the transcript."),
    BucketSpec("events", "Events", RESTART,
               "Harness bookkeeping, written by the CLI.", editable=False),
    BucketSpec("notes", "Your Notes", LIVE,
               "Your own labels and annotations. Never touches the transcript, "
               "and never reaches the model.", editable=False),
    BucketSpec("other", "Other", RESTART,
               "Unclassified records. Left out of the editable surface until "
               "they are understood well enough to name.", editable=False),
]

BUCKET_BY_KEY = {b.key: b for b in BUCKETS}

# The turn-by-turn record, which Explorer shows as ONE band called Conversation
# rather than five. Split across five buckets you can see what kind of content
# is heavy; you cannot see what happened. Grouped, the band fans out into
# exchanges and an exchange fans out into its steps, so both readings live in
# one view instead of two.
CONVERSATION_KINDS = ("compact_summary", "user_turns", "assistant_turns",
                      "thinking", "tool_calls", "tool_results")
CONVERSATION = BucketSpec(
    "conversation", "Conversation", RESTART,
    "One band per request, holding its reasoning, its reply and every tool "
    "call welded to the result it produced. Rewrite the records and resume the "
    "same id.")


@dataclass
class SessionRef:
    """A session an adapter found, before its transcript is parsed."""
    adapter: str
    sid: str
    name: str
    cwd: str
    transcript: str
    status: str = "unknown"     # running | stopped | unknown
    updated_at: str = ""
    pid: int | None = None

    @property
    def key(self) -> str:
        return f"{self.adapter}:{self.sid}"


def pid_alive(pid: int | None) -> bool | None:
    """True/False, or None when the platform will not tell us cheaply."""
    if not pid:
        return None
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True          # exists, owned by someone else
    except (OSError, AttributeError):
        return None          # Windows without the signal, etc.
