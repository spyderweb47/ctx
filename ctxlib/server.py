"""Tiny stdlib HTTP server. No dependencies, so it runs anywhere python3 does."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import (active, board, chat, deploy, extract, ledger, mcplog,
               notebook, pipeline, workspace)
from .adapters import discover_all
from .base import EDIT_CLASS_COLOR, EDIT_CLASS_INFO

WEB = Path(__file__).resolve().parent.parent / "web"


_REFS: tuple[float, dict] = (0.0, {})


def _refs(ttl: float = 2.0) -> dict:
    """Session refs, memoised for a couple of seconds.

    The pulse poll asks for these every tick while you watch a live session;
    rescanning both stores each time would make watching a session cost more
    than reading it. Onboarding calls discover_all() directly, so the picker
    still sees a session the moment it appears.
    """
    global _REFS
    now = time.monotonic()
    if _REFS[1] and now - _REFS[0] < ttl:
        return _REFS[1]
    refs = {r.key: r for r in discover_all()}
    _REFS = (now, refs)
    return refs


def _inputs_for(ref, bd: dict, label: str) -> list:
    """What the extraction nodes feeding this analysis collected, with text.

    The text is fetched here rather than stored on the node: an extraction holds
    addresses, so the cell always reads what the transcript says now.
    """
    out = []
    for n in board.feeders(bd, label, {"extraction"}):
        ms = []
        for m in (n.get("matches") or [])[:120]:
            ms.append(dict(m, text=extract.text_of(ref, m["bucket"], m["i"])))
        out.append({"label": n["label"], "title": n.get("title", ""),
                    "query": n.get("query", ""), "summary": n.get("summary", ""),
                    "matches": ms})
    return out


def _review_send(key: str, ref, bd: dict, body: dict) -> dict:
    """Move an analysis's conclusion into a review node. Nothing is applied."""
    src = board.find(bd, body.get("from", ""))
    dst = board.find(bd, body.get("to", ""))
    if not src or not dst:
        return {"ok": False, "error": "both nodes must exist"}
    if dst.get("kind") != "review":
        return {"ok": False, "error": f"{dst['label']} is not a review node"}
    why = board.flow_error(src.get("kind", ""), "review")
    if why:
        return {"ok": False, "error": why}
    sent = list(dst.get("sent") or [])
    res = src.get("result") or {}
    sent.append({
        "from": src["label"], "title": src.get("title", ""),
        "body": (body.get("note") or src.get("body") or "")[:20000],
        "stdout": (res.get("stdout") or "")[:8000],
        "images": (res.get("images") or [])[:4],
        "at": time.time(),
    })
    board.connect(key, src["label"], dst["label"], ref.name)
    board.update(key, dst["label"], sent=sent, state="open", session_name=ref.name)
    return {"ok": True, "board": board.load(key, ref.name)}


def _review_stage(key: str, ref, bd: dict, body: dict) -> dict:
    """The one door from the canvas to the Deployment page."""
    n = board.find(bd, body.get("label", ""))
    if not n or n.get("kind") != "review":
        return {"ok": False, "error": "not a review node"}
    kind = body.get("kind") or ""
    payload = body.get("payload") or (n.get("proposal") or {}).get("payload") or {}
    try:
        item, _ = deploy.stage(key, kind, body.get("title") or n.get("title") or n["label"],
                               payload)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    board.update(key, n["label"], state="staged",
                 proposal={"kind": kind, "payload": payload, "deploy": item["label"]},
                 session_name=ref.name)
    return {"ok": True, "item": item, "board": board.load(key, ref.name)}


def reveal(path: str) -> str:
    """Open a file in the user's editor, falling back to the OS handler."""
    p = str(path)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", p])
        elif sys.platform.startswith("win"):
            import os
            os.startfile(p)  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", p])
        return "opened"
    except Exception as e:
        return f"could not open: {e}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # The page and its client change constantly while ctx is being built;
        # a cached app.js silently strands the user on an old build.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(json.dumps(obj).encode(), "application/json", code)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path in ("/", "/index.html"):
                return self._send((WEB / "index.html").read_bytes(), "text/html; charset=utf-8")

            # static assets from web/ -- flat, no traversal
            if not u.path.startswith("/api/"):
                name = u.path.lstrip("/")
                f = WEB / name
                if "/" not in name and f.is_file():
                    ctype = {"js": "application/javascript", "css": "text/css",
                             "svg": "image/svg+xml", "json": "application/json"}.get(
                        name.rsplit(".", 1)[-1], "application/octet-stream")
                    return self._send(f.read_bytes(), ctype + "; charset=utf-8")

            if u.path == "/app.js":
                return self._send((WEB / "app.js").read_bytes(),
                                  "application/javascript; charset=utf-8")

            if u.path == "/api/legend":
                return self._json({k: {**v, "color": EDIT_CLASS_COLOR[k]}
                                   for k, v in EDIT_CLASS_INFO.items()})

            if u.path == "/api/sessions":
                # Onboarding only: choose the one session to work inside.
                return self._json({"sessions": [ledger.describe_session(r) for r in discover_all()],
                                   "active": active.get()})

            if u.path == "/api/session":
                key = q.get("key") or active.get() or ""
                r = _refs().get(key)
                if not r:
                    return self._json({"error": "unknown session"}, 404)
                # Picking in the browser sets the active session on disk, so the
                # MCP server in session 2 is looking at the same thing.
                active.set(key)
                return self._json(ledger.build(r))

            if u.path == "/api/pulse":
                # Has the session moved on? One stat call, cheap enough to ask
                # every second or two, so the canvas can tell you it is stale
                # instead of quietly showing you a context that no longer exists.
                key = q.get("key") or active.get() or ""
                r = _refs().get(key)
                if not r:
                    return self._json({"ok": False, "error": "unknown session"}, 404)
                try:
                    st = os.stat(r.transcript)
                except OSError as e:
                    return self._json({"ok": False, "error": str(e)})
                # The package changes for three different reasons, and watching
                # only the transcript missed two of them: a relaunch with
                # different flags changes what is sent without writing a record,
                # and staging or applying a deployment changes what Explorer
                # should draw. Fingerprint all three.
                live = deploy.live_labels(r.key, r.sid)
                try:
                    dep = os.stat(deploy.path(r.key)).st_mtime
                except OSError:
                    dep = 0
                return self._json({"ok": True, "mtime": st.st_mtime,
                                   "size": st.st_size, "status": r.status,
                                   "flags": "|".join(sorted(live)),
                                   "deploy": dep})

            if u.path == "/api/chat":
                key = q.get("key") or active.get() or ""
                return self._json(chat.load(key))

            if u.path == "/api/chat/events":
                return self._json(chat.events(q.get("run", ""),
                                              int(q.get("after") or 0)))

            if u.path == "/api/board":
                key = q.get("key") or active.get() or ""
                r = _refs().get(key)
                return self._json(board.load(key, r.name if r else ""))

            if u.path == "/api/deploy":
                key = q.get("key") or active.get() or ""
                r = _refs().get(key)
                # derived, not stored: the same answer Explorer gives
                d = deploy.derive(key, r.sid if r else "")
                if r:
                    ok, why = deploy.validate(r.transcript)
                    d["transcript_ok"] = ok
                    d["transcript_note"] = why
                    d["running"] = r.status == "running"
                    d["resume_cmd"] = f"claude --resume {r.sid}"
                return self._json(d)

            if u.path == "/api/runs":
                key = q.get("key") or active.get() or ""
                return self._json(pipeline.load(key))

            if u.path == "/api/ctxs":
                act = dict(workspace.active())
                cur = workspace.read(act.get("ctx_session") or "")
                if cur:
                    act["name"] = cur.get("name", "")
                    act["saved"] = cur.get("updated", 0)
                return self._json({"sessions": workspace.listing(), "active": act})

            if u.path == "/api/log":
                return self._json({"calls": mcplog.tail(int(q.get("n", 120)))})

            if u.path == "/api/item":
                r = _refs().get(q.get("key") or active.get() or "")
                if not r:
                    return self._json({"error": "unknown session"}, 404)
                return self._json(ledger.bucket_text(r, q.get("bucket", ""), int(q.get("i", 0))))

            return self._json({"error": "not found"}, 404)
        except Exception as e:
            return self._json({"error": str(e)}, 500)

    def _body(self) -> dict:
        """Read a JSON request body.

        Content-Length is the common case, but a client that streams (Node's
        http.request does this by default) sends chunked instead. Reading only
        Content-Length made those arrive as an empty dict and the handler then
        answered a confusing "pick a session" -- silently wrong is worse than
        loud, so both encodings are handled.
        """
        raw = b""
        if (self.headers.get("Transfer-Encoding") or "").lower() == "chunked":
            while True:
                line = self.rfile.readline().strip()
                if not line:
                    break
                try:
                    size = int(line.split(b";")[0], 16)
                except ValueError:
                    break
                if size == 0:
                    self.rfile.readline()
                    break
                raw += self.rfile.read(size)
                self.rfile.readline()
        else:
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                raw = self.rfile.read(n)
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return {}

    def do_HEAD(self):
        # browsers never HEAD these, but health checks and curl -I do
        self.send_response(200)
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.end_headers()

    def do_POST(self):
        u = urlparse(self.path)
        body = self._body()
        if u.path.startswith("/api/board/"):
            key = body.get("key") or active.get() or ""
            r = _refs().get(key)
            name = r.name if r else ""
            op = u.path.rsplit("/", 1)[-1]
            if op == "add":
                node, d = board.add(key, body.get("title", ""), body.get("body", ""),
                                    body.get("kind", "note"),
                                    body.get("x", 0), body.get("y", 0), name,
                                    body.get("ref"))
                return self._json({"node": node, "board": d})
            if op == "write":
                node, d = board.write(key, body.get("label", ""), body.get("body"),
                                      body.get("title"), name)
                return self._json({"node": node, "board": d})
            if op == "move":
                return self._json({"board": board.move(key, body.get("label", ""),
                                                       body.get("x", 0), body.get("y", 0), name)})
            if op == "update":
                node, d = board.update(key, body.get("label", ""), body.get("title"),
                                       body.get("body"), body.get("kind"), name)
                return self._json({"node": node, "board": d})
            if op == "connect":
                ok, d = board.connect(key, body.get("from", ""), body.get("to", ""), name)
                return self._json({"ok": ok, "board": d})
            if op == "disconnect":
                return self._json({"board": board.disconnect(key, body.get("from", ""),
                                                             body.get("to", ""), name)})
            if op == "remove":
                return self._json({"board": board.remove(key, body.get("label", ""), name)})
            return self._json({"error": "unknown board op"}, 404)

        if u.path.startswith("/api/chat/"):
            key = body.get("key") or active.get() or ""
            op = u.path.rsplit("/", 1)[-1]
            if op == "send":
                return self._json(chat.send(key, body.get("text", ""),
                                            body.get("model", "")))
            if op == "stop":
                return self._json(chat.stop(body.get("run", "")))
            if op == "clear":
                return self._json({"ok": True, "chat": chat.clear(key)})
            return self._json({"error": "unknown chat op"}, 404)

        if u.path.startswith("/api/extract/"):
            key = body.get("key") or active.get() or ""
            r = _refs().get(key)
            if not r:
                return self._json({"ok": False, "error": "unknown session"}, 404)
            op = u.path.rsplit("/", 1)[-1]
            label = body.get("label", "")
            if op == "run":
                res = extract.run(r, body.get("query", ""),
                                  buckets=body.get("buckets") or None,
                                  regex=bool(body.get("regex")),
                                  limit=int(body.get("limit") or extract.MAX_HITS))
                if res.get("ok") and label:
                    board.update(key, label, query=res["query"],
                                 matches=res["matches"], session_name=r.name)
                    res["board"] = board.load(key, r.name)
                return self._json(res)
            if op == "summary":
                board.update(key, label, summary=body.get("summary", ""),
                             session_name=r.name)
                return self._json({"ok": True, "board": board.load(key, r.name)})
            return self._json({"error": "unknown extract op"}, 404)

        if u.path.startswith("/api/review/"):
            key = body.get("key") or active.get() or ""
            r = _refs().get(key)
            if not r:
                return self._json({"ok": False, "error": "unknown session"}, 404)
            op = u.path.rsplit("/", 1)[-1]
            bd = board.load(key, r.name)
            if op == "send":
                return self._json(_review_send(key, r, bd, body))
            if op == "audit":
                n = board.find(bd, body.get("label", ""))
                if not n or n.get("kind") != "review":
                    return self._json({"ok": False, "error": "not a review node"}, 404)
                audit = list(n.get("audit") or [])
                audit.append({"note": body.get("note", ""), "by": body.get("by", "agent"),
                              "verdict": body.get("verdict", "note"), "at": time.time()})
                board.update(key, n["label"], audit=audit, session_name=r.name)
                return self._json({"ok": True, "board": board.load(key, r.name)})
            if op == "stage":
                return self._json(_review_stage(key, r, bd, body))
            return self._json({"error": "unknown review op"}, 404)

        if u.path.startswith("/api/deploy/"):
            key = body.get("key") or active.get() or ""
            r = _refs().get(key)
            op = u.path.rsplit("/", 1)[-1]
            if op == "stage":
                try:
                    item, d = deploy.stage(key, body.get("kind", ""),
                                           body.get("title", ""),
                                           body.get("payload") or {})
                except ValueError as e:
                    return self._json({"ok": False, "error": str(e)}, 400)
                return self._json({"item": item, "deploy": d})
            if op == "drop":
                return self._json({"deploy": deploy.drop(key, body.get("label", ""))})
            if not r:
                return self._json({"ok": False, "error": "unknown session"}, 404)
            if op == "apply":
                return self._json(deploy.apply(key, body.get("label", ""), r))
            if op == "convert":
                return self._json(deploy.convert_to_inject(key, body.get("label", "")))
            if op == "verify":
                return self._json(deploy.verify_config(key, body.get("label", ""), r))
            if op == "rollback":
                return self._json(deploy.rollback(key, body.get("label", ""), r))
            if op == "run":
                return self._json(deploy.run(r, key))
            if op == "validate":
                ok, why = deploy.validate(r.transcript)
                return self._json({"ok": ok, "note": why})
            return self._json({"error": "unknown deploy op"}, 404)

        if u.path.startswith("/api/pipeline/"):
            key = body.get("key") or active.get() or ""
            r = _refs().get(key)
            name = r.name if r else ""
            op = u.path.rsplit("/", 1)[-1]
            if op == "run":
                return self._json(pipeline.queue(key, body.get("start", ""), name))
            if op == "complete":
                return self._json(pipeline.complete(key, body.get("run", ""),
                                                    body.get("output", ""), name))
            if op == "drop":
                return self._json({"runs": pipeline.drop(key, body.get("label", ""))})
            return self._json({"error": "unknown pipeline op"}, 404)

        if u.path == "/api/cell/run":
            key = body.get("key") or active.get() or ""
            r = _refs().get(key)
            if not r:
                return self._json({"ok": False, "error": "unknown session"}, 404)
            label = body.get("label", "")
            bd = board.load(key, r.name)
            node = board.find(bd, label)
            code = body.get("code")
            if code is None:
                code = (node or {}).get("body", "")
            data = ledger.build(r)
            data["nodes"] = bd["nodes"]
            data["inputs"] = _inputs_for(r, bd, label)
            res = notebook.run(code, data)
            if node is not None:
                board.update(key, label, body=code, session_name=r.name)
                board.set_result(key, label, res, r.name)
            return self._json(res)

        if u.path.startswith("/api/ctxs/"):
            op = u.path.rsplit("/", 1)[-1]
            if op == "create":
                r = _refs().get(body.get("key", ""))
                if not r:
                    return self._json({"ok": False, "error": "pick a harness session"}, 404)
                return self._json(workspace.create(body.get("name", ""), r))
            if op == "open":
                return self._json(workspace.open_(body.get("id", "")))
            if op == "save":
                return self._json(workspace.save(body.get("id", "")))
            if op == "rename":
                return self._json(workspace.rename(body.get("id", ""), body.get("name", "")))
            if op == "close":
                workspace.clear_active()
                return self._json({"ok": True})
            if op == "delete":
                return self._json(workspace.delete(body.get("id", "")))
            return self._json({"error": "unknown ctxs op"}, 404)

        if u.path == "/api/open":
            r = _refs().get(body.get("key") or active.get() or "")
            if not r:
                return self._json({"error": "unknown session"}, 404)
            return self._json({"status": reveal(r.transcript), "path": r.transcript})
        return self._json({"error": "not found"}, 404)


def serve(port: int = 7777, open_browser: bool = True):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    n = len(discover_all())
    print(f"ctx  ->  {url}")
    print(f"     {n} sessions found across claude-code + codex")
    if open_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
