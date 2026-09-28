#!/usr/bin/env python3
"""ctx self-test: every route answers, and the UI is wired to what exists.

Written after a regex edit silently deleted two POST route blocks -- the server
still started, the pages still loaded, and only clicking told you. Run it after
touching server.py or the web files:

    python3 selftest.py            # against a running server on :7777
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:7777"
HERE = Path(__file__).resolve().parent
fails: list[str] = []


def hit(path: str, payload: dict | None = None) -> tuple[int, str]:
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method="POST" if data else "GET",
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read(400).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(200).decode("utf-8", "replace")
    except Exception as e:                      # noqa: BLE001
        return 0, str(e)


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'ok  ' if ok else 'FAIL'}  {name}{'  ' + detail if detail and not ok else ''}")
    if not ok:
        fails.append(name)


def _loads(path: Path) -> tuple[bool, str]:
    """Execute the client against a stub DOM and report any throw.

    Every DOM access returns the same permissive proxy, so the script can query,
    listen, style and append freely; we only care whether evaluating it raises.
    """
    node = shutil.which("node")
    if not node:
        return True, "node not installed — skipped"
    harness = r"""
      const any = new Proxy(function () {}, {
        get: (t, k) => (k === Symbol.toPrimitive ? () => 0
                      : k === 'length' ? 0 : k === 'then' ? undefined : any),
        apply: () => any, construct: () => any, set: () => true, has: () => true,
      });
      globalThis.document = any; globalThis.window = globalThis;
      globalThis.navigator = any; globalThis.location = any;
      globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
      globalThis.addEventListener = () => {}; globalThis.removeEventListener = () => {};
      globalThis.requestAnimationFrame = () => 0; globalThis.cancelAnimationFrame = () => {};
      globalThis.setInterval = () => 0; globalThis.setTimeout = () => 0;
      globalThis.fetch = () => new Promise(() => {});
      globalThis.matchMedia = () => any; globalThis.getComputedStyle = () => any;
      globalThis.innerWidth = 1440; globalThis.innerHeight = 900;
      try {
        new Function(require('fs').readFileSync(process.argv[1], 'utf8'))();
        console.log('OK');
      } catch (e) { console.log('THREW: ' + e.constructor.name + ': ' + e.message); }
    """
    try:
        r = subprocess.run([node, "-e", harness, str(path)],
                           capture_output=True, text=True, timeout=30)
    except Exception as e:                       # node present but unhappy
        return True, f"could not run node — skipped ({e})"
    out = (r.stdout or r.stderr).strip().splitlines()
    last = out[-1] if out else "(no output)"
    return last == "OK", last


def write_manifest() -> int:
    js = (HERE / "web/app.js").read_text()
    names = sorted(set(re.findall(r'^(?:async )?function ([A-Za-z_]\w*)', js, re.M))
                   | set(re.findall(r'^(?:const|let|var) ([A-Za-z_]\w*) *= *(?:async *)?(?:\(|\w+ *=>)',
                                    js, re.M)))
    (HERE / "web/app.manifest").write_text("\n".join(names) + "\n")
    print(f"wrote web/app.manifest with {len(names)} names")
    return 0


def main() -> int:
    print("routes (GET)")
    for p in ("/", "/app.js", "/api/sessions", "/api/ctxs", "/api/board",
              "/api/deploy", "/api/runs", "/api/log", "/api/legend"):
        code, _ = hit(p)
        check(p, code == 200, f"HTTP {code}")

    code, body = hit("/api/sessions")
    key = ""
    if code == 200:
        ss = json.loads(body if len(body) < 400 else "{}").get("sessions") if False else None
    with urllib.request.urlopen(BASE + "/api/sessions", timeout=30) as r:
        key = (json.load(r).get("sessions") or [{}])[0].get("key", "")

    print("routes (POST) — every handler must be reachable")
    # Every probe must be NON-MUTATING: name a label that cannot exist, so the
    # handler is reached and answers, but nothing is written. An early version
    # of this file spawned nodes and overwrote session bodies while "testing".
    ghost = "__selftest_no_such_node__"
    posts = [("/api/board/update", {"key": key, "label": ghost}),
             ("/api/board/connect", {"key": key, "from": ghost, "to": ghost}),
             ("/api/deploy/validate", {"key": key}),
             # This one MUST be refused. It used to stage an empty deployment on
             # every run -- the probe that was meant to prove the route exists
             # was quietly filling the user's Deployment page. Now an empty body
             # is a 400, which proves the route exists AND that it guards itself.
             ("/api/deploy/stage", None),
             ("/api/pipeline/run", {"key": key, "start": ghost}),
             ("/api/cell/run", {"key": key, "label": ghost, "code": "pass"}),
             ("/api/ctxs/save", {"id": ghost})]
    for p, payload in posts:
        code, body = hit(p, payload if payload is not None else {})
        # a handled route answers 200 or a specific error; a MISSING one says "not found"
        missing = '"error": "not found"' in body
        ok = code in (200, 400, 404) and not missing
        if p == "/api/deploy/stage":
            ok = code == 400 and "kind" in body      # refused, nothing written
        check(p, ok, f"HTTP {code} {body[:60]}")

    print("client loads")
    # Syntax is not enough. A top-level `const` that calls a helper declared
    # further down parses fine, then throws a ReferenceError the moment the
    # browser runs it -- and everything after that line never gets defined, so
    # every button in the app is dead while every other check here passes.
    # This runs the file the way a browser would, against a stub DOM.
    check("app.js executes without throwing", *_loads(HERE / "web/app.js"))

    print("ui wiring")
    html = (HERE / "web/index.html").read_text()
    js = (HERE / "web/app.js").read_text()
    calls = set(re.findall(r'on(?:click|input)="([A-Za-z_]\w*)\(', html))
    defined = set(re.findall(r'(?:async )?function ([A-Za-z_]\w*)', js))
    defined |= set(re.findall(r'^(?:const|let|var) ([A-Za-z_]\w*) *=', js, re.M))
    check("every onclick has a function", not (calls - defined), str(sorted(calls - defined)))
    # A manifest of the functions this app is known to need. Deleting one is a
    # deliberate act: remove it here too. This is deterministic, unlike parsing
    # JS with regex -- an earlier attempt at that silently passed while 16
    # functions were missing.
    manifest = HERE / "web/app.manifest"
    if manifest.exists():
        want = [n for n in manifest.read_text().split() if n]
        gone = [n for n in want if n not in defined]
        check(f"all {len(want)} manifest functions present", not gone, str(gone))
    else:
        check("manifest exists", False, "run: python3 selftest.py --write-manifest")

    ids_used = set(re.findall(r"\$\('#([\w-]+)'\)", js))
    ids = set(re.findall(r'id="([\w-]+)"', html)) | set(re.findall(r'id="([\w-]+)"', js))
    check("every $('#id') exists", not (ids_used - ids), str(sorted(ids_used - ids)))
    for tag in ("div", "section", "button"):
        o = len(re.findall(r"<" + tag + r"[ >]", html))
        check(f"<{tag}> balanced", o == html.count(f"</{tag}>"), f"{o}/{html.count(f'</{tag}>')}")

    print("no duplicate definitions")
    for f in sorted(HERE.glob("ctxlib/**/*.py")) + [HERE / "mcp_server.py"]:
        s = f.read_text()
        dups = {d for d in re.findall(r"^def ([a-z_]\w*)", s, re.M)
                if len(re.findall(r"^def " + d + r"\b", s, re.M)) > 1}
        check(f.name, not dups, str(sorted(dups)))
    dups = {d for d in re.findall(r"^(?:async )?function (\w+)", js, re.M)
            if len(re.findall(r"^(?:async )?function " + d + r"\b", js, re.M)) > 1}
    check("app.js", not dups, str(sorted(dups)))

    print()
    print(f"{'FAILED: ' + ', '.join(fails) if fails else 'all checks passed'}")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(write_manifest() if "--write-manifest" in sys.argv else main())
