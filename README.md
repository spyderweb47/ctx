# ctx

**See what is actually inside a running AI coding session's context — and change it.**

Python 3 standard library only. No install, no dependencies, no account. Runs on macOS, Linux and Windows.

```bash
python3 ctx.py serve     # canvas at http://127.0.0.1:7777
python3 ctx.py list      # sessions in the terminal
```

**Nothing to connect.** ctx reads the stores the CLIs already keep — `~/.claude` and `~/.codex` — so it finds your sessions on its own: no path to enter, no key, no config. Pick one at the picker and start. The built-in assistant uses the `claude` already on your `PATH`, with its existing login.

---

## The problem

A long coding session drifts. By hour three the model is answering from context you cannot see: a correction you made and it forgot, a tool result that is no longer true, an instruction written for a different task. You know something in there is wrong. You cannot tell what.

So you start a new session and lose everything.

ctx is the other option. It reads the session's own store, shows you the context as it really is, and lets you change it — then puts the session back under the same id.

---

## The finding this is built on

Claude Code records the system prompt on a conversation's first request and replays that record on every later request and resume. **So the record in the transcript is not a log of what was sent — it is the source of what gets sent next.**

Verified, not assumed. A codeword was injected into a stopped session's recorded prompt, the session was resumed under its own id, and it was asked for the codeword:

```
before the edit:  NONE
after  the edit:  ZEBRAFISH
```

That makes the system prompt a **transcript edit**, not a launch-flag one — and transcript edits persist. A launch flag has to be re-passed on every start and the next start silently drops it. A record simply stays.

Three runs pinned down the surrounding behaviour:

| launch | result |
|---|---|
| new session + `--append-system-prompt` | applied |
| `--resume` + `--append-system-prompt` | **ignored** |
| `--resume --system-prompt-snapshot off` + flag | applied |

---

## Three pages

### Explorer — what is in the window

A two-level Sankey over the real context. Level 1 fans out to the buckets; click one and it fans into its items.

It shows only what is **actually sent** and only what you can **actually change**:

- history discarded by a compaction is cut — it is on disk, it is not in the package
- superseded prompt snapshots are cut
- duplicate usage records are de-duplicated
- buckets with no lever are counted but not drawn

The five turn-by-turn kinds are one stream, so drilling any of them fans out the whole conversation in order with your chosen kind lit and the rest dimmed. Tool calls are welded to the results they produced, paired by `tool_use` id.

### Labs — work it out

An infinite canvas with a three-stage pipeline, enforced one way:

```
extraction  →  analysis  →  review  →  Deployment
```

- **extraction** gathers. One question — "which records mention this?" — answered as an ordered set of real, addressable items. It holds addresses, not copies, so it never goes stale.
- **analysis** is a notebook cell over what the extractions collected. Python, pandas, matplotlib, no limits. The namespace already knows the session: `ctx.matches()`, `ctx.texts()`, `ctx.match_df()`, `ctx.buckets`, `ctx.df()`.
- **review** holds a proposal still while it is audited. Nothing reaches Deployment except through one.

### The assistant — ✦ bottom right, or ⌘J

The meta-agent, without the second terminal.

The usual way to drive a tool like this is to open a *second* harness session, register an MCP server, and talk to that. It works, and it costs a whole session per project — ten projects means twenty terminals and remembering which one is pointed where.

ctx spawns that agent itself instead. Same CLI, same login, no API key, no window of its own, always pointed at the ctx session you have open. Drag the launcher to any edge; click it and it docks to the bottom and the panel grows out of it.

It has the ctx tools and **nothing else** — no Bash, no Read, no Write, no web. That is not only safety, it is what makes it cheap: with no filesystem to explore, the first thing it does is ask ctx. A first draft *with* file tools spent 21 turns and $0.16 reading ctx's own source to work out which session was active. Briefed and stripped down, the same question took **3 turns and $0.028**.

It can read, search, build on the canvas, audit, and stage a change. It can never apply one — that is the MCP server's guarantee, not a setting. Each ctx session keeps its own conversation, resumed by id.

### Deployment — the only door to the transcript

Stop the session, rewrite, validate, resume under the **same id**. Never a fork.

---

## Safety model

Nothing is written to a transcript unless three things hold:

1. **the session is not running** — a live process holds the conversation in RAM and would append over the edit from its own memory
2. **a snapshot exists** — every apply is undoable
3. **the result validates** — every `tool_use` has a matching `tool_result`, or the API rejects the whole conversation with a 400

A deploy that fails validation is rolled back, not left half-applied. Applying is always a human click: an agent can gather, analyse, audit and stage, and can never apply.

Rollback is **surgical**. It restores the exact lines the deployment rewrote and keeps every turn the session has had since — because sessions keep running after a deploy, and a wholesale file restore would silently delete that work.

---

## Honest numbers

Turn totals come from the provider's own `usage` record and are exact. Per-segment numbers are estimates, and ctx says so.

Characters-per-token is not one number. Measured across 1,836 clean request-to-request deltas — pairs where the content added between two API calls was ≥92% a single kind:

| kind | chars/token | IQR |
|---|---|---|
| tool inputs, results, schemas | **1.89** | 1.64–2.17 |
| prose and markdown | **2.49** | 2.33–2.72 |
| thinking signatures | **3.21** | 3.06–3.33 |

Those get the *shape* right. They still do not reproduce a tokenizer, so the **total** is taken from the provider's own input count and every bucket is scaled to it. The calibration factor is shown in the status bar with the raw miss in its tooltip — a factor far from 1.0 means the shares are shakier too.

Two things are not sized by characters at all:

**Images** are priced by area, roughly `(w × h) / 750`. Measuring their base64 instead overcounts by ~50×: one real session scored 6.03M tokens where the images actually cost 124k.

**Thinking** is never stored. Measured across one store, 65 of 5,745 thinking blocks have text — 1.1%, and only from two specific CLI builds. What is kept is the signature, which *is* resident, so the bucket is sized by signature length and labelled for what it holds.

---

## Voice-first labels

Every item has a short address: `B` is a bucket, `B1` an item inside it, `N4` a canvas node, `D13` a deployment.

Deliberately **not** words like "delta" or "echo". A dictated word blurs into the sentence around it and a transcript cannot tell the label from the content. `B1` always reads as an identifier, whatever sentence it lands in.

Labels are assigned on first sight and persisted. They never reshuffle — if they did, you would say "B1" and be looking at something else.

---

## Connect an agent (MCP)

```bash
claude mcp add ctx -s user -- python3 /absolute/path/to/mcp_server.py
```

Point it at a **different** session from the one you are analysing — an agent that analyses the session it lives in adds its own tool schemas to the context it is measuring.

*(This is optional. The built-in assistant spawns its own agent and needs no registration — this is for driving ctx from a real terminal.)*

| tool | | what it does |
|---|---|---|
| `current_session` | read | the session and its editable buckets |
| `get_bucket` / `get_item` | read | one bucket, or the full text of one item |
| `extract_run` / `extract_summary` / `extract_read` | write | fill and read an extraction node |
| `board_add` / `board_write` / `board_connect` / `board_remove` | write | build the canvas |
| `review_read` / `review_audit` | write | audit a proposal |
| `deploy_stage` / `deploy_stage_ops` | write | stage a change — never applies it |
| `deploy_list` / `deploy_remove` | | inspect and tidy the queue |

Every call is logged and shown live in the **Activity** tab.

---

## Adapters

| | Claude Code | Codex |
|---|---|---|
| store | `~/.claude/` | `~/.codex/` |
| index | `sessions/<pid>.json` | `session_index.jsonl` |
| transcript | `projects/<slug>/<id>.jsonl` | `sessions/**/rollout-*.jsonl` |

Both are *home store + append-only JSONL + index*. Adding another CLI means one file in `ctxlib/adapters/`:

```python
NAME = "my-cli"
def discover() -> list[SessionRef]: ...
def parse(path) -> tuple[list[Segment], dict]: ...
```

Register it in `ctxlib/adapters/__init__.py`. Nothing else changes — the ledger, canvas and MCP server are all adapter-agnostic.

---

## Layout

```
ctx.py                    CLI entry
ctxlib/base.py            Segment/Bucket model, edit classes, measured densities
ctxlib/ledger.py          segments → buckets, token accounting, calibration
ctxlib/extract.py         search the context, keep addresses not copies
ctxlib/deploy.py          stage, snapshot, validate, apply, surgical rollback
ctxlib/board.py           canvas nodes, edges, one-way flow
ctxlib/notebook.py        Python cells with the session in scope
ctxlib/server.py          stdlib HTTP + JSON API
ctxlib/adapters/          claude_code.py, codex.py
web/                      the client
mcp_server.py             stdio MCP
selftest.py               routes, wiring, and the client actually loading
```

## Self-test

```bash
python3 selftest.py        # against a running server
```

Checks that every route answers, every `onclick` has a function, every `$('#id')` exists — and that the client **executes** against a stub DOM. That last one was added after a top-level `const` that referenced a helper declared further down: it parsed perfectly, threw a `ReferenceError` the moment a browser ran it, and killed every button in the app while every other check passed.

---

## Status

Working and used daily, but young. Known open questions:

- tool schemas share the same recorded snapshot as the system prompt — whether editing them takes effect is **untested**, and it is 18–25% of a typical window
- `--system-prompt-snapshot off` ignores the record, and the CLI documents the record as replayed *"until the conversation is compacted"* — neither limit is verified
- estimates are calibrated per session; a session with no provider count falls back to raw and says so

Issues and pull requests welcome.

## Licence

MIT
