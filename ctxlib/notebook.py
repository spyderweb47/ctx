"""Run a node's Python in a subprocess and bring back text plus figures.

This is a notebook cell whose namespace already knows about the session: `ctx`
exposes the real ledger and the board, so a cell can plot the actual context
rather than a copy of it. Execution is a separate short-lived process with a
timeout -- a runaway cell cannot take the server with it.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import deps

TIMEOUT = 40

RUNNER = r'''
import base64, io, json, os, sys, traceback

_out = os.environ["CTX_OUT"]
_data = json.load(open(os.environ["CTX_DATA"], encoding="utf-8"))
_code = open(os.environ["CTX_CODE"], encoding="utf-8").read()

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:          # a cell that never plots still runs
    matplotlib = plt = None


class _Ctx:
    """The session, its buckets and the board -- already loaded."""
    def __init__(self, d):
        self.session = d.get("session") or {}
        self.buckets = d.get("buckets") or []
        self.nodes = d.get("nodes") or []
        self.est_total = d.get("est_total") or 0
        self.exact_total = d.get("exact_total")
        # Whatever the extraction nodes wired into this one collected. An
        # analysis works on a named subset it was given, not on "the session"
        # in the abstract -- that is the whole point of the two stages.
        self.inputs = d.get("inputs") or []

    @property
    def extracts(self):
        """{'N4': [match, ...]} -- every feeding extraction by label."""
        return {i["label"]: i.get("matches") or [] for i in self.inputs}

    def matches(self, label=None):
        """Flat list of matched records, optionally from one extraction."""
        out = []
        for i in self.inputs:
            if label and i["label"].upper() != str(label).upper():
                continue
            for m in i.get("matches") or []:
                out.append(dict(m, _from=i["label"], _query=i.get("query", "")))
        return out

    def texts(self, label=None):
        """The full text of every matched record that was fetched."""
        return [m["text"] for m in self.matches(label) if m.get("text")]

    def match_df(self):
        import pandas as pd
        rows = self.matches()
        cols = ["_from", "_query", "label", "bucket_title", "line", "turn",
                "tokens", "chars", "hits", "title"]
        return pd.DataFrame([{c: m.get(c) for c in cols} for m in rows],
                            columns=cols)

    def bucket(self, label):
        t = str(label).upper()
        for b in self.buckets:
            if b["label"].upper() == t or b["key"] == label or b["title"] == label:
                return b
        return None

    def items(self, label):
        b = self.bucket(label)
        return (b or {}).get("items") or []

    def node(self, label):
        t = str(label).upper()
        for n in self.nodes:
            if n["label"].upper() == t:
                return n
        return None

    def df(self):
        """Every item as a pandas DataFrame."""
        import pandas as pd
        rows = []
        for b in self.buckets:
            for i in b.get("items") or []:
                rows.append({"bucket": b["label"], "bucket_title": b["title"],
                             "edit_class": b["edit_class"], "item": i["label"],
                             "title": i["title"], "tokens": i["tokens"],
                             "chars": i["chars"], "turn": i["turn"], "line": i["line"]})
        return pd.DataFrame(rows)

    def bucket_df(self):
        import pandas as pd
        return pd.DataFrame([{"label": b["label"], "title": b["title"],
                              "tokens": b["tokens"], "pct": b["pct"],
                              "count": b["count"], "edit_class": b["edit_class"]}
                             for b in self.buckets])

    def __repr__(self):
        return f"<ctx {self.session.get('name','?')}: {len(self.buckets)} buckets>"


ctx = _Ctx(_data)
buf_out, buf_err = io.StringIO(), io.StringIO()
res = {"ok": True, "stdout": "", "stderr": "", "images": [], "error": ""}
_so, _se = sys.stdout, sys.stderr
try:
    sys.stdout, sys.stderr = buf_out, buf_err
    g = {"ctx": ctx, "plt": plt, "__name__": "__ctx_cell__"}
    exec(compile(_code, "<cell>", "exec"), g)
except BaseException:
    res["ok"] = False
    res["error"] = traceback.format_exc(limit=6)
finally:
    sys.stdout, sys.stderr = _so, _se

res["stdout"] = buf_out.getvalue()[-40000:]
res["stderr"] = buf_err.getvalue()[-8000:]

for num in (plt.get_fignums() if plt else []):
    fig = plt.figure(num)
    b = io.BytesIO()
    try:
        fig.savefig(b, format="png", dpi=124, bbox_inches="tight",
                    facecolor=fig.get_facecolor())
        res["images"].append("data:image/png;base64," +
                             base64.b64encode(b.getvalue()).decode())
    except Exception:
        pass
if plt:
    plt.close("all")

json.dump(res, open(_out, "w", encoding="utf-8"))
'''


def run(code: str, data: dict, timeout: int = TIMEOUT) -> dict:
    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "runner.py").write_text(RUNNER)
        (d / "code.py").write_text(code or "")
        (d / "data.json").write_text(json.dumps(data, default=str))
        env = dict(os.environ,
                   CTX_OUT=str(d / "out.json"), CTX_CODE=str(d / "code.py"),
                   CTX_DATA=str(d / "data.json"), MPLBACKEND="Agg")
        try:
            p = subprocess.run([deps.python(), str(d / "runner.py")], env=env,
                               capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"cell timed out after {timeout}s",
                    "stdout": "", "stderr": "", "images": [], "ms": timeout * 1000}
        try:
            res = json.loads((d / "out.json").read_text())
        except (OSError, ValueError):
            return {"ok": False, "stdout": p.stdout[-4000:],
                    "stderr": p.stderr[-4000:], "images": [],
                    "error": "cell produced no result"}
    res["ms"] = int((time.time() - t0) * 1000)
    res["at"] = time.time()
    return res
