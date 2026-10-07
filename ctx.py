#!/usr/bin/env python3
"""ctx -- see and label what is actually in an AI coding session's context.

  python3 ctx.py serve [--port N] [--no-open]
  python3 ctx.py list
  python3 ctx.py deps            # install the notebook's pandas + matplotlib now
"""
from __future__ import annotations

import sys

from ctxlib import ledger
from ctxlib.adapters import discover_all


def cmd_list() -> None:
    """Onboarding: the sessions you can choose to work inside."""
    rows = discover_all()
    print(f"{'#':>3}  {'ADAPTER':<12} {'STATUS':<8} NAME")
    for n, r in enumerate(rows, 1):
        s = ledger.describe_session(r)
        print(f"{n:>3}  {s['adapter']:<12} {s['status']:<8} {s['name'][:46]}")
    print(f"\n{len(rows)} sessions. Pick one in the browser; buckets inside it are A, B, C.")


def main() -> int:
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "serve"
    if cmd == "list":
        cmd_list()
        return 0
    if cmd == "deps":
        from ctxlib import deps
        print(f"notebook cells run under: {deps.ensure()}")
        return 0
    if cmd == "serve":
        from ctxlib.server import serve
        port = 7777
        if "--port" in argv:
            port = int(argv[argv.index("--port") + 1])
        serve(port=port, open_browser="--no-open" not in argv)
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
