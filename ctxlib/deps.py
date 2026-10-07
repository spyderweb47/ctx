"""Give the notebook what it needs, without touching the system Python.

The server, the ledger and the MCP server are standard library only. The one
place that needs more is a Labs notebook cell: pandas and matplotlib. Rather
than ask for a pip step before first use, ctx checks on ``serve`` and, if the
interpreter it runs under cannot import them, builds a private venv under
``~/.ctx/venv`` and installs there.

A venv rather than ``pip install --user`` because Debian, Ubuntu and Homebrew
ship an externally-managed Python (PEP 668) that refuses a plain pip install
outright. A venv built with ``--system-site-packages`` works on all of them,
keeps anything already installed visible, and never alters the system tree.

Set ``CTX_NO_AUTO_DEPS=1`` to skip the check, or ``CTX_VENV`` to move the venv.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parent.parent / "requirements.txt"
VENV = Path(os.environ.get("CTX_VENV") or Path.home() / ".ctx" / "venv")
MODULES = ("pandas", "matplotlib")

_chosen: str | None = None


def _venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _has(python: str | Path, modules=MODULES) -> bool:
    """Can this interpreter import every module? Checked in a subprocess so a
    half-installed package cannot poison the server's own import state."""
    code = "import " + ", ".join(modules)
    try:
        return subprocess.run([str(python), "-c", code], capture_output=True,
                              timeout=120).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _found() -> str | None:
    """An interpreter that already has the deps, if one exists."""
    if _has(sys.executable):
        return sys.executable
    vp = _venv_python()
    if vp.exists() and _has(vp):
        return str(vp)
    return None


def python() -> str:
    """The interpreter notebook cells run under: the one with the deps, else
    the server's own (cells that need nothing beyond the stdlib still work)."""
    global _chosen
    if _chosen is None:
        _chosen = _found() or sys.executable
    return _chosen


def ensure(log=print) -> str:
    """Install on first run. Returns the interpreter cells will use."""
    global _chosen
    if os.environ.get("CTX_NO_AUTO_DEPS"):
        return python()
    have = _found()
    if have:
        _chosen = have
        return have

    vp = _venv_python()
    log(f"     notebook needs {', '.join(MODULES)}; installing into {VENV} (one time)")
    try:
        if not vp.exists():
            subprocess.run([sys.executable, "-m", "venv", "--system-site-packages",
                            str(VENV)], check=True, capture_output=True, text=True)
        subprocess.run([str(vp), "-m", "pip", "install", "--quiet",
                        "--disable-pip-version-check", "-r", str(REQUIREMENTS)],
                       check=True)
    except subprocess.CalledProcessError as e:
        err = (e.stderr or "").strip()[-600:] if hasattr(e, "stderr") else ""
        log(f"     install failed{': ' + err if err else ''}")
    except OSError as e:
        log(f"     install failed: {e}")

    if vp.exists() and _has(vp):
        log("     installed; Labs cells will run under the venv")
        _chosen = str(vp)
        return _chosen
    log(f"     Labs cells that need pandas/matplotlib will not run until you install them:\n"
        f"       {sys.executable} -m pip install -r {REQUIREMENTS}")
    _chosen = sys.executable
    return _chosen
