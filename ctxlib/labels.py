"""Labels live INSIDE one session. The session itself is never labelled.

You pick a session once at onboarding, then everything you say is scoped to it:

    A            bucket A          (e.g. Tool Schemas)
    A1, A2, A3   items inside A
    B1, B2       items inside bucket B

Deliberately not real words. Anything dictated as a word -- "delta", "echo",
"mike" -- blurs into the surrounding sentence, and a transcript cannot tell the
label from the content around it. `A1` always reads as an identifier.

Past 26 buckets the letter widens spreadsheet-style: Z, AA, AB.
"""
from __future__ import annotations

import json
from pathlib import Path

STORE = Path.home() / ".ctx" / "labels.json"
_A = 26


def column(index: int) -> str:
    """0-based index -> A, B, ... Z, AA, AB (spreadsheet columns)."""
    s = ""
    n = index + 1
    while n > 0:
        n, r = divmod(n - 1, _A)
        s = chr(ord("A") + r) + s
    return s


def item_label(bucket: str, index: int) -> str:
    """('A', 0) -> 'A1'"""
    return f"{bucket}{index + 1}"


class LabelStore:
    """First-seen order, persisted per session.

    Labels MUST be stable across refreshes. If they reshuffle, you say "A1", the
    tool hears A1, and you are looking at something else -- silent confusion. So
    assignment is append-only and written to disk. Only the ORDER is stored, so
    the rendering scheme can change without renumbering anything.
    """

    def __init__(self, path: Path = STORE):
        self.path = path
        self._data: dict[str, list[str]] = {}
        self._load()

    def _load(self) -> None:
        try:
            self._data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self._data = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self._data, indent=2))
        except OSError:
            pass  # labels still work in-memory for this run

    def index(self, namespace: str, key: str) -> int:
        """Stable 0-based index for key, assigned on first sight."""
        seen = self._data.setdefault(namespace, [])
        if key not in seen:
            seen.append(key)
            self._save()
        return seen.index(key)
