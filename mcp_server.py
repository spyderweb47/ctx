#!/usr/bin/env python3
"""ctx MCP server (stdio).

Reads the session, and writes to its BOARD -- never to a transcript. The board
is a scratch surface beside the session, so an agent can explain, annotate and
plan against the context with no risk to the session itself.

Scoped to ONE session, exactly like the canvas. You pick a session in the
browser and that choice is shared on disk, so this server is already looking at
the same thing -- no handshake, no session ids to pass around.

Inside that session, addressing is just: bucket 'A', item 'A1'.

Read-only by design. See README for what writing needs first.
"""
from __future__ import annotations

import json
import sys
import time

from ctxlib import (active, board, deploy, extract, ledger, mcplog, notebook,
                    pipeline)
from ctxlib.adapters import discover_all

TOOLS = [
    {"name": "current_session",
     "description": "The session ctx is pointed at right now (chosen in the "
                    "browser). 'buckets' is the EDITABLE surface -- what is in "
                    "the package the CLI sends AND has a lever to change it, "
                    "each with label A, B, C, its token share and the lever. "
                    "'fixed' is in the package with no lever; 'ephemeral' "
                    "(thinking) is not in the package at all; 'cut' says how "
                    "many records were dropped as pre-compaction history.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_bucket",
     "description": "One bucket of the current session by its letter, e.g. 'B'. "
                    "Returns its items (B1, B2, B3...), edit class, and how to "
                    "change it.",
     "inputSchema": {"type": "object",
                     "properties": {"bucket": {"type": "string",
                                               "description": "Bucket letter, e.g. 'B'"}},
                     "required": ["bucket"]}},
    {"name": "get_item",
     "description": "The full text of one item by its label, e.g. 'B1'. The "
                    "letter is the bucket, the number is the item within it.",
     "inputSchema": {"type": "object",
                     "properties": {"item": {"type": "string",
                                             "description": "Item label, e.g. 'B1'"}},
                     "required": ["item"]}},
    {"name": "board_list",
     "description": "Every node on the current session's board, with its label "
                    "(N1, N2...), title and body. N1 is always the session itself.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "board_add",
     "description": "Spawn a new node on the board and return its label. Use this "
                    "to add an explanation, a finding or a plan the user can see "
                    "on the canvas.",
     "inputSchema": {"type": "object",
                     "properties": {"title": {"type": "string"},
                                    "body": {"type": "string",
                                             "description": "Markdown. Shown in the detail panel."}},
                     "required": ["title"]}},
    {"name": "board_write",
     "description": "Fill or replace a node's detail panel by label, e.g. 'N1'. "
                    "This is how you answer a question the user asked about a node "
                    "-- they see it appear on the board.",
     "inputSchema": {"type": "object",
                     "properties": {"node": {"type": "string", "description": "e.g. 'N1'"},
                                    "body": {"type": "string", "description": "Markdown."},
                                    "title": {"type": "string"}},
                     "required": ["node"]}},
    {"name": "board_connect",
     "description": "Draw a directional link between two Labs nodes, e.g. from "
                    "'N2' to 'N3'. Chains of links are pipelines.",
     "inputSchema": {"type": "object",
                     "properties": {"from": {"type": "string"}, "to": {"type": "string"}},
                     "required": ["from", "to"]}},
    {"name": "board_remove",
     "description": "Delete one or more nodes from the LABS CANVAS by label "
                    "(N4, or [\"N4\",\"N5\"]). Their edges go with them. This "
                    "touches the canvas only -- never the transcript, never a "
                    "staged deployment, never anything in Explorer. The session "
                    "node N1 is permanent and is refused. There is no undo, so "
                    "delete what the user named and nothing else.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "node": {"type": "string",
                                  "description": "one node label, e.g. 'N4'"},
                         "nodes": {"type": "array", "items": {"type": "string"},
                                   "description": "several labels at once"}}}},
    {"name": "board_pipeline",
     "description": "The ordered chain of nodes reachable from a start node, with "
                    "their bodies -- read this to execute a pipeline the user built.",
     "inputSchema": {"type": "object",
                     "properties": {"start": {"type": "string"}},
                     "required": ["start"]}},
    {"name": "board_update",
     "description": "Edit a Labs node in place: its title, body or kind "
                    "(note | prompt | context | output).",
     "inputSchema": {"type": "object",
                     "properties": {"node": {"type": "string"},
                                    "title": {"type": "string"},
                                    "body": {"type": "string"},
                                    "kind": {"type": "string"}},
                     "required": ["node"]}},
    {"name": "pipeline_pending",
     "description": "Pipelines the user has pressed Run on and that are waiting "
                    "for you. Each gives a resolved brief and the output node to "
                    "write the answer into. Check this whenever the user says they "
                    "ran a pipeline.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "pipeline_complete",
     "description": "Finish a queued run: your answer goes into its output node "
                    "and appears on the user's board. Omit 'run' to complete the "
                    "oldest queued one.",
     "inputSchema": {"type": "object",
                     "properties": {"run": {"type": "string", "description": "e.g. 'R1'"},
                                    "output": {"type": "string", "description": "Markdown."}},
                     "required": ["output"]}},
    {"name": "cell_run",
     "description": "Run a Labs code node's Python and return stdout plus any "
                    "figures it drew. The cell's namespace already has `ctx`: "
                    "ctx.buckets, ctx.items('B'), ctx.nodes, ctx.df(), "
                    "ctx.bucket_df(), ctx.session. Pass `code` to replace the "
                    "node's source first.",
     "inputSchema": {"type": "object",
                     "properties": {"node": {"type": "string"},
                                    "code": {"type": "string"}},
                     "required": ["node"]}},
    {"name": "extract_run",
     "description": "Fill an EXTRACTION node: search the session's context for "
                    "a keyword or phrase and attach every record that mentions "
                    "it. Returns the matches with their Explorer labels (K85, "
                    "L46...), so they stay addressable. This is the gathering "
                    "stage -- it selects, it does not interpret.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "node": {"type": "string", "description": "extraction node, e.g. 'N4'"},
                         "query": {"type": "string", "description": "keyword or phrase"},
                         "regex": {"type": "boolean", "description": "treat query as a regex"},
                         "buckets": {"type": "array", "items": {"type": "string"},
                                     "description": "limit to bucket keys, e.g. ['tool_results']"},
                         "limit": {"type": "integer"}},
                     "required": ["node", "query"]}},
    {"name": "extract_summary",
     "description": "Write the optional summary on an extraction node -- what "
                    "the gathered records amount to. Summarising is optional; "
                    "the matches stand on their own.",
     "inputSchema": {"type": "object",
                     "properties": {"node": {"type": "string"},
                                    "summary": {"type": "string"}},
                     "required": ["node", "summary"]}},
    {"name": "extract_read",
     "description": "The full text of the records an extraction node holds, so "
                    "you can read what it gathered rather than only the excerpts.",
     "inputSchema": {"type": "object",
                     "properties": {"node": {"type": "string"},
                                    "max": {"type": "integer",
                                            "description": "how many records (default 10)"}},
                     "required": ["node"]}},
    {"name": "review_audit",
     "description": "Add an audit note to a REVIEW node: what you checked and "
                    "what you found. verdict is 'pass', 'concern' or 'note'. "
                    "Auditing never applies anything -- staging stays a human "
                    "click.",
     "inputSchema": {"type": "object",
                     "properties": {"node": {"type": "string"},
                                    "note": {"type": "string"},
                                    "verdict": {"type": "string",
                                                "enum": ["pass", "concern", "note"]}},
                     "required": ["node", "note"]}},
    {"name": "review_read",
     "description": "Everything a review node holds: what each analysis sent, "
                    "the audit so far, and the proposal if one is set. Read this "
                    "before auditing.",
     "inputSchema": {"type": "object",
                     "properties": {"node": {"type": "string"}},
                     "required": ["node"]}},
    {"name": "deploy_remove",
     "description": "Remove one or more items from the DEPLOYMENT page by label "
                    "(D13, or [\"D13\",\"D14\"]). This deletes the staged "
                    "change only -- it never edits a transcript and never undoes "
                    "a change that was already applied. An APPLIED item is "
                    "refused, because its snapshot is what a rollback reads: "
                    "roll it back first. An item whose flag a running session "
                    "still carries is removed with a warning -- dropping the row "
                    "does not stop the session sending it. There is no undo.",
     "inputSchema": {"type": "object",
                     "properties": {
                         "item": {"type": "string",
                                  "description": "one deployment label, e.g. 'D13'"},
                         "items": {"type": "array", "items": {"type": "string"},
                                   "description": "several labels at once"},
                         "force": {"type": "boolean",
                                   "description": "also remove an applied item, "
                                                  "orphaning its rollback snapshot. "
                                                  "Only when the user says so."}}}},
    {"name": "deploy_stage_ops",
     "description": "Stage a deployment document: an ordered list of edits to the "
                    "transcript. Each op is {op:'replace', line, json} | "
                    "{op:'delete', lines:[...]} | {op:'insert', after_line, json}. "
                    "json must be one complete JSONL record. Staged only -- the "
                    "user reviews and clicks Deploy.",
     "inputSchema": {"type": "object",
                     "properties": {"title": {"type": "string"},
                                    "ops": {"type": "array"}},
                     "required": ["title", "ops"]}},
    {"name": "deploy_stage",
     "description": "Stage a change for the Deployment tab. It is NOT applied -- the "
                    "user reviews and clicks Deploy. kind is one of: "
                    "system_prompt_file (payload.text), append_system_prompt "
                    "(payload.text), inject_system_prompt (payload.text, "
                    "payload.mode append|replace \u2014 edits the prompt the "
                    "transcript RECORDS, so it survives a relaunch instead of "
                    "riding on a flag), drop_items (payload.lines = transcript line "
                    "numbers), replace_item (payload.line + payload.json).",
     "inputSchema": {"type": "object",
                     "properties": {"kind": {"type": "string"},
                                    "title": {"type": "string"},
                                    "payload": {"type": "object"}},
                     "required": ["kind", "title", "payload"]}},
    {"name": "deploy_list",
     "description": "Everything staged or deployed for this session, plus whether "
                    "the transcript currently validates.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_sessions",
     "description": "Onboarding only: every session on this machine, so you can "
                    "tell the user what to pick. Does not change the selection.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def _ref():
    key = active.get()
    if not key:
        return None
    for r in discover_all():
        if r.key == key:
            return r
    return None


def _split(label: str) -> tuple[str, int | None]:
    """'B1' -> ('B', 0).  'B' -> ('B', None)."""
    t = (label or "").strip().upper()
    letters = "".join(c for c in t if c.isalpha())
    digits = "".join(c for c in t if c.isdigit())
    return letters, (int(digits) - 1 if digits else None)


def call(name: str, args: dict):
    if name == "list_sessions":
        return {"sessions": [ledger.describe_session(r) for r in discover_all()],
                "active": active.get(),
                "note": "The user picks a session in the ctx browser window."}

    ref = _ref()
    if ref is None:
        return {"error": "No session selected yet. Ask the user to pick one in "
                         "the ctx window (http://127.0.0.1:7777)."}

    if name.startswith("board_"):
        key = active.get() or ""
        if name == "board_list":
            return board.load(key, ref.name)
        if name == "board_add":
            node, d = board.add(key, args.get("title", ""), args.get("body", ""),
                                "note", 0, 0, ref.name)
            return {"node": node, "count": len(d["nodes"])}
        if name == "board_update":
            node, d = board.update(key, args.get("node", ""), args.get("title"),
                                   args.get("body"), args.get("kind"), ref.name)
            if node is None:
                return {"error": f"no node {args.get('node')!r}",
                        "available": [n["label"] for n in d["nodes"]]}
            return {"node": node}
        if name == "board_connect":
            ok, d = board.connect(key, args.get("from", ""), args.get("to", ""), ref.name)
            return {"connected": ok, "edges": len(d["edges"])}
        if name == "board_remove":
            labels = args.get("nodes") or ([args["node"]] if args.get("node") else [])
            if not labels:
                return {"error": "name the node(s) to delete"}
            r = board.remove_many(key, labels, ref.name)
            return {"removed": r["removed"], "skipped": r["skipped"],
                    "edges_removed": r["edges_removed"],
                    "nodes_left": len(r["board"]["nodes"])}
        if name == "board_pipeline":
            d = board.load(key, ref.name)
            ch = board.chain(d, args.get("start", ""))
            if not ch:
                return {"error": f"no node {args.get('start')!r}",
                        "available": [n["label"] for n in d["nodes"]]}
            return {"pipeline": [{"label": n["label"], "kind": n["kind"],
                                  "title": n["title"], "body": n["body"]} for n in ch]}
        if name == "board_write":
            node, d = board.write(key, args.get("node", ""), args.get("body"),
                                  args.get("title"), ref.name)
            if node is None:
                return {"error": f"no node {args.get('node')!r}",
                        "available": [n["label"] for n in d["nodes"]]}
            return {"node": node}

    if name.startswith("pipeline_"):
        key = active.get() or ""
        if name == "pipeline_pending":
            runs = pipeline.pending(key)
            return {"pending": runs,
                    "note": ("Do the work each brief asks for, then call "
                             "pipeline_complete with the run label and your answer.")
                    if runs else "Nothing queued."}
        if name == "pipeline_complete":
            return pipeline.complete(key, args.get("run", ""),
                                     args.get("output", ""), ref.name)

    if name == "cell_run":
        key = active.get() or ""
        bd = board.load(key, ref.name)
        node = board.find(bd, args.get("node", ""))
        if node is None:
            return {"error": f"no node {args.get('node')!r}",
                    "available": [n["label"] for n in bd["nodes"]]}
        code = args.get("code")
        if code is None:
            code = node.get("body", "")
        data = ledger.build(ref)
        data["nodes"] = bd["nodes"]
        res = notebook.run(code, data)
        board.update(key, node["label"], body=code, kind="code", session_name=ref.name)
        board.set_result(key, node["label"], res, ref.name)
        return res

    if name.startswith("extract_"):
        key = active.get() or ""
        label = args.get("node", "")
        bd = board.load(key, ref.name)
        n = board.find(bd, label)
        if not n:
            return {"error": f"no node {label!r}"}
        if n.get("kind") != "extraction":
            return {"error": f"{n['label']} is a {n.get('kind')} node, not an extraction"}
        if name == "extract_run":
            res = extract.run(ref, args.get("query", ""),
                              buckets=args.get("buckets") or None,
                              regex=bool(args.get("regex")),
                              limit=int(args.get("limit") or extract.MAX_HITS))
            if not res.get("ok"):
                return res
            board.update(key, label, query=res["query"], matches=res["matches"],
                         session_name=ref.name)
            return {"node": label, "query": res["query"], "count": res["count"],
                    "tokens": res["tokens"], "truncated": res["truncated"],
                    "matches": res["matches"]}
        if name == "extract_summary":
            board.update(key, label, summary=args.get("summary", ""),
                         session_name=ref.name)
            return {"node": label, "summary": args.get("summary", "")}
        if name == "extract_read":
            cap = int(args.get("max") or 10)
            out = []
            for m in (n.get("matches") or [])[:cap]:
                out.append({"label": m["label"], "bucket": m["bucket_title"],
                            "line": m["line"], "tokens": m["tokens"],
                            "text": extract.text_of(ref, m["bucket"], m["i"])})
            return {"node": label, "query": n.get("query", ""),
                    "shown": len(out), "held": len(n.get("matches") or []),
                    "records": out}
        return {"error": f"unknown tool {name}"}

    if name.startswith("review_"):
        key = active.get() or ""
        label = args.get("node", "")
        bd = board.load(key, ref.name)
        n = board.find(bd, label)
        if not n:
            return {"error": f"no node {label!r}"}
        if n.get("kind") != "review":
            return {"error": f"{n['label']} is a {n.get('kind')} node, not a review"}
        if name == "review_read":
            return {"node": label, "title": n.get("title", ""),
                    "state": n.get("state", "open"),
                    "sent": n.get("sent") or [], "audit": n.get("audit") or [],
                    "proposal": n.get("proposal"),
                    "feeding": [x["label"] for x in board.feeders(bd, label)]}
        if name == "review_audit":
            audit = list(n.get("audit") or [])
            audit.append({"note": args.get("note", ""), "by": "agent",
                          "verdict": args.get("verdict", "note"), "at": time.time()})
            board.update(key, label, audit=audit, session_name=ref.name)
            return {"node": label, "entries": len(audit),
                    "verdict": args.get("verdict", "note")}
        return {"error": f"unknown tool {name}"}

    if name.startswith("deploy_"):
        key = active.get() or ""
        if name == "deploy_list":
            d = deploy.derive(key, ref.sid)
            ok, why = deploy.validate(ref.transcript)
            return {"items": d["items"], "transcript_ok": ok, "transcript_note": why,
                    "running": ref.status == "running"}
        if name == "deploy_remove":
            labels = args.get("items") or ([args["item"]] if args.get("item") else [])
            if not labels:
                return {"error": "name the deployment(s) to remove"}
            r = deploy.drop_many(key, labels, ref.sid, bool(args.get("force")))
            return {"removed": r["removed"], "skipped": r["skipped"],
                    "warned": r["warned"], "items_left": r["items_left"]}
        if name == "deploy_stage_ops":
            item, d = deploy.stage(key, "ops", args.get("title", ""),
                                   {"ops": args.get("ops") or []})
            return {"item": item, "ops": len(args.get("ops") or []),
                    "note": "Staged only. The user reviews and clicks Deploy."}
        if name == "deploy_stage":
            try:
                item, _ = deploy.stage(key, args.get("kind", ""), args.get("title", ""),
                                       args.get("payload") or {})
            except ValueError as e:
                return {"error": str(e)}
            return {"item": item,
                    "note": "Staged only. The user reviews it in the Deployment tab "
                            "and clicks Deploy."}

    data = ledger.build(ref)

    if name == "current_session":
        slim = {k: v for k, v in data.items() if k != "buckets"}
        slim["buckets"] = [{k: v for k, v in b.items() if k != "items"}
                           for b in data["buckets"]]
        return slim

    if name in ("get_bucket", "get_item"):
        raw = args.get("bucket") if name == "get_bucket" else args.get("item")
        letter, idx = _split(raw or "")
        bucket = next((b for b in data["buckets"] if b["label"] == letter), None)
        if bucket is None:
            return {"error": f"no bucket {letter!r}",
                    "available": [f"{b['label']} = {b['title']}" for b in data["buckets"]]}
        if name == "get_bucket":
            return {"session": data["session"], "bucket": bucket}
        if idx is None:
            return {"error": f"{raw!r} names a bucket, not an item. "
                             f"Try {letter}1.",
                    "items": [i["label"] for i in bucket["items"][:20]]}
        full = ledger.bucket_text(ref, bucket["key"], idx)
        if "error" in full:
            return {"error": f"no item {raw!r}",
                    "items": [i["label"] for i in bucket["items"][:20]]}
        full["label"] = f"{letter}{idx + 1}"
        full["bucket"] = {k: bucket[k] for k in
                          ("label", "title", "badge", "edit_class", "how", "restart")}
        return full

    return {"error": f"unknown tool {name}"}


def _summary(tool: str, out: dict) -> str:
    if tool == "current_session":
        return (f"{len(out.get('buckets', []))} editable buckets · "
                f"{out.get('editable_total', 0):,} of {out.get('est_total', 0):,} est tokens")
    if tool == "get_bucket":
        b = out.get("bucket") or {}
        return f"{b.get('label')} {b.get('title')} · {b.get('count')} items"
    if tool == "get_item":
        return f"{out.get('label')} {out.get('title', '')[:40]} · {out.get('tokens', 0):,} tok"
    if tool == "board_connect":
        return f"connected · {out.get('edges')} edges"
    if tool == "board_pipeline":
        return " -> ".join(n["label"] for n in out.get("pipeline", []))
    if tool == "deploy_remove":
        gone = ", ".join(x["label"] for x in out.get("removed") or []) or "nothing"
        bits = [f"removed {gone}"]
        if out.get("warned"):
            bits.append(f"{len(out['warned'])} still live")
        if out.get("skipped"):
            bits.append(f"skipped {len(out['skipped'])}")
        return " \u00b7 ".join(bits)
    if tool == "board_remove":
        gone = ", ".join(x["label"] for x in out.get("removed") or []) or "nothing"
        skip = out.get("skipped") or []
        return (f"removed {gone}"
                + (f" \u00b7 {out.get('edges_removed', 0)} edge(s)" if out.get("edges_removed") else "")
                + (f" \u00b7 skipped {len(skip)}" if skip else ""))
    if tool == "extract_run":
        return f"{out.get('node')} \u00b7 {out.get('count', 0)} match(es) \u00b7 {out.get('tokens', 0):,} tok"
    if tool == "extract_read":
        return f"{out.get('node')} \u00b7 {out.get('shown', 0)} of {out.get('held', 0)} records"
    if tool == "extract_summary":
        return f"{out.get('node')} \u00b7 summary set"
    if tool == "review_audit":
        return f"{out.get('node')} \u00b7 {out.get('verdict')} \u00b7 {out.get('entries', 0)} entries"
    if tool == "review_read":
        return f"{out.get('node')} \u00b7 {len(out.get('sent') or [])} sent \u00b7 {len(out.get('audit') or [])} audited"
    if tool == "deploy_stage":
        i = out.get("item") or {}
        return f"{i.get('label')} {i.get('kind')} · {i.get('title', '')[:34]}"
    if tool == "cell_run":
        return (f"{out.get('ms', 0)}ms · {len(out.get('images') or [])} figure(s)"
                if out.get("ok") else (out.get("error") or "")[:60])
    if tool == "deploy_stage_ops":
        i = out.get("item") or {}
        return f"{i.get('label')} · {out.get('ops')} ops"
    if tool == "deploy_list":
        return f"{len(out.get('items', []))} staged · transcript {'ok' if out.get('transcript_ok') else 'INVALID'}"
    if tool in ("board_add", "board_write"):
        n = out.get("node") or {}
        return f"{n.get('label')} {n.get('title', '')[:40]}"
    if tool == "pipeline_pending":
        return f"{len(out.get('pending', []))} queued"
    if tool == "pipeline_complete":
        r = out.get("run") or {}
        return f"{r.get('label')} -> {out.get('node')}" if out.get("ok") else (out.get("error") or "")
    if tool == "board_update":
        n = out.get("node") or {}
        return f"{n.get('label')} {n.get('title', '')[:34]}"
    if tool == "board_list":
        return f"{len(out.get('nodes', []))} nodes"
    if tool == "list_sessions":
        return f"{len(out.get('sessions', []))} sessions"
    return ""


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except ValueError:
            continue
        rid, method = req.get("id"), req.get("method")

        if method == "initialize":
            res = {"protocolVersion": "2024-11-05",
                   "capabilities": {"tools": {}},
                   "serverInfo": {"name": "ctx", "version": "0.6.0"}}
        elif method == "tools/list":
            res = {"tools": TOOLS}
        elif method == "tools/call":
            p = req.get("params") or {}
            tool, a = p.get("name", ""), (p.get("arguments") or {})
            out = call(tool, a)
            mcplog.append(tool, a, "error" not in out,
                          out.get("error") or _summary(tool, out))
            res = {"content": [{"type": "text",
                                "text": json.dumps(out, indent=2, default=str)}]}
        elif method and method.startswith("notifications/"):
            continue
        else:
            res = {}

        if rid is not None:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": res}) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
