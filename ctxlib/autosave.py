"""Keep the open ctx session file in step with the live working set.

board / deploy / pipeline each persist immediately to their own store; this
mirrors those writes into the `.ctxs` file of whichever ctx session is bound to
that harness key. It lives here rather than in workspace.py so the stores can
call it without importing workspace and creating a cycle.

Suspended while a ctx session is being opened or created -- during those, the
stores are being written *from* a file, and saving back mid-way would copy the
new contents into the previously-open session.
"""
from __future__ import annotations

_depth = 0


def suspend() -> None:
    global _depth
    _depth += 1


def resume() -> None:
    global _depth
    _depth = max(0, _depth - 1)


def suspended() -> bool:
    return _depth > 0


def touch(key: str) -> None:
    if _depth or not key:
        return
    from . import workspace          # late: workspace imports the stores
    try:
        workspace.mirror(key)
    except Exception:                # autosave must never break a write
        pass
