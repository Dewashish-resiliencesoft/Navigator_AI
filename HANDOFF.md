# Navigator AI — handoff

State as of 2026-07-31. Everything below was verified by running it, not by reading
notes: `pytest -q` → 139 passed, `python -m navigator.docs check` → up to date,
`cd sdk && npm test` → 12 passed / 0 failed, production docs → HTTP 200.

## What this is

An agent that joins Google Meet / Zoom calls and gives a live, interactive demo of a
web product. It drives the real site in a browser, narrates out loud, types
prospect-supplied data into the product mid-call, and learns corrective rules from its
own verification failures.

**The one architectural rule:** the site graph YAML is the only product-specific
artifact. Everything else — four tools, six postcondition kinds, the state machine,
the action log — is written against `SiteGraph` as an interface. Nothing outside
`browser/` and `config/` ever sees a CSS selector; callers pass *aliases*. That rule
is what makes the system product-agnostic, and it is worth protecting.

Corollary: the agent never infers selectors and never invents postconditions. A model
that invents both the action and the expectation cannot fail visibly, which defeats
the entire design. Site graphs are hand-authored or human-approved. That constraint is
the product's main quality claim, not a limitation.

## Tech stack — fixed, do not substitute

Python 3.11+ (running 3.12.3) · Playwright (Python, sync API) · LangGraph 1.2.10
(explicit state machine, not an agent loop) · Groq (Llama 3.3 70B + Whisper v3 Turbo)
· Gemini 2.5 Flash free / OpenAI paid behind one `LLMProvider` protocol · Piper local
TTS (subprocess) · ChromaDB · Attendee self-hosted meeting bot · v4l2loopback + ffmpeg
· Docker Compose · FastAPI · SQLite (stdlib `sqlite3`).

Environment notes: **no Docker on this machine**, no `python3-venv` (venv was created
with the `virtualenv` module). Node v20.20.2 local, Node 24 on CI. Fern CLI 5.89.0 via
`npx fern-api`.

## Conventions the user requires

- Type hints everywhere.
- Pydantic models for all structured data crossing a module boundary.
- All API keys and config via env vars.
- Every LangGraph state independently testable — each node is a plain function taking
  `CallState` and returning a partial, so tests pass a dict and need no graph.
- "Favor explicit, inspectable state over implicit agent memory — this system needs to
  be debuggable call-by-call."
- Every change must update **both** the HTML docs and Fern. This is enforced
  mechanically; see Phase 8.

Two active response modes, set by session hooks: **CAVEMAN ultra** (terse prose; drop
articles and filler; code, commits, and security text written normally) and
**PONYTAIL ultra** (YAGNI — build the minimum that works, no unrequested
abstractions; mark intentional simplifications with a `ponytail:` comment).

## Phase status

| Phase | Scope | Status |
|---|---|---|
| 1 | Scripted demo loop, site graph, tools, postconditions, ActionLog | ✅ done |
| 2 | Groq LLM planning, Silero VAD + Whisper STT, Chroma retrieval | ❌ stubs |
| 3 | Attendee integration: join Zoom/Meet, speak, stream video | ❌ stubs |
| 4 | Reflection pipeline, pending-review table, vision verify fallback | ❌ stubs |
| 5 | Wrapper API — FastAPI, product registry, multi-tenant isolation | ✅ done |
| 6 | Recorder — click through an app, get a draft site graph | ❌ not started |
| 7 | SDK — annotate / declare / verify in the product's own repo + CI | ✅ done |
| 8 | Docs pipeline — self-contained HTML + Fern, regenerated from code | ✅ done |

**Done: 1, 5, 7, 8. Left: 2, 3, 4, 6.**

Phases 2–4 are what make it conversational. Phases 5–8 are what make it sellable, and
they are independent of 2–4 — which is why they were built first.

## What works end to end today

```bash
.venv/bin/python -m navigator.demo                    # headful, speaks aloud
.venv/bin/python -m navigator.demo --headless --mute   # what CI does
```

Opens the inbox fixture, types a message, sends it, verifies each step's postcondition
against real DOM, narrates the outcome, prints the action log. Non-zero exit if any
step failed. No LLM anywhere in this path — the plan is replayed from the site graph.

```bash
.venv/bin/uvicorn navigator.api.app:app --port 8000 --workers 1
```

15 routes. `--workers 1` is required: live demo state is an in-process dict on
`DemoRunner` (`navigator/api/runner.py`, `TODO(phase 5+)`).

## Layout

| Path | What's in it | State |
|---|---|---|
| `navigator/schemas.py` | Every model crossing a module boundary | done |
| `navigator/config/` | Site graph loader, validator, site YAMLs | done |
| `navigator/browser/` | Playwright session, four tools, postcondition checks | done (vision fallback stubbed) |
| `navigator/agent/` | State machine, one file per node | mixed — see below |
| `navigator/api/` | Product registry, demo runner, FastAPI app | done |
| `navigator/logs/` | ActionLog (SQLite) | done |
| `navigator/voice/tts.py` | Piper TTS | done |
| `navigator/voice/stt.py` | Silero VAD + Groq Whisper | **stub, phase 2** |
| `navigator/memory/` | Chroma collections + retrieval | **stubs, phase 2** |
| `navigator/meeting/attendee.py` | Attendee API client | **stub, phase 3** |
| `navigator/agent/providers.py` | `LLMProvider`: Gemini + OpenAI | **stubs, phase 4** |
| `navigator/docs/` | Docs generator: HTML + Fern, from live code | done |
| `sdk/` | `@navigator/sdk`: authoring DSL, compiler, `navigator` CLI | done |
| `fern/` | Generated Fern project — **do not hand-edit** | generated |
| `docs/index.html` | Generated integration guide — **do not hand-edit** | generated |

Module rule: `browser/` never imports `agent/`. Both import `schemas.py` and
`config/`. `logs/` imports only `schemas.py`.

There is exactly **one** site graph validator: `config.site_graph.parse_site_graph`.
File loads, API uploads, and SDK pushes all go through it, so a customer gets the same
error message however the graph arrived. Do not add a second one — two validators
would drift, and the drift surfaces as "works locally, rejected on push".

## The state machine

```
joining -> introducing -> speaking
           listening -> planning -> speaking <- verifying <- executing
```

Nine nodes. Everything that talks routes through SPEAKING. The
EXECUTING → VERIFYING → SPEAKING cycle repeats once per tool call, so the agent never
builds on an action it hasn't verified.

Real: `introducing`, `executing`, `verifying`, `speaking`, `ending`.
Stubbed: `joining` (phase 3), `listening` (phase 2), `planning` (phase 2 — currently
replays a named flow from the site graph), `reflecting` (phase 4).

Four tools, no free-form DOM access: `click_element`, `fill_field`, `navigate`,
`wait_for`. Every call declares a **postcondition** at call time; VERIFYING checks it
against real DOM with no LLM involved. Both the expectation and what actually happened
land in the ActionLog — that pair is what makes a call debuggable afterwards and what
reflection will learn from.

Six postcondition kinds (`CheckKind`): `visible`, `hidden`, `text_contains`,
`value_equals`, `url_matches`, `element_count`.

## Multi-tenancy (phase 5, done)

- `product_id` always comes from the API key, **never** from a URL path, so no route
  can read across tenants by accident.
- Each demo gets its own browser **context** — separate cookie jar, storage, session.
  Not merely a separate page. Cookie isolation between tenants is mandatory.
- ActionLog rows and archive dirs are namespaced by `product_id`. So are Chroma
  collection names (`{product_id}_corrections`, `{product_id}_product_knowledge`) —
  a correction learned demoing product A is wrong and possibly confidential for B.
- Narration is rendered from a `persona` block in the site graph plus selector aliases
  read as English (`send_button` → "send button"). No product name appears anywhere in
  the code. Authoring readable aliases is what makes a new product sound right.

## The SDK (phase 7, done)

Three levels, each useful alone. Don't make level 3 a prerequisite for any value.

1. **Annotate.** `<button data-nav="send_button">`. An alias used in a flow but never
   declared compiles to `[data-nav="send_button"]`, so the customer writes no CSS and
   the selector survives restyles and CSS-module hashes.
2. **Declare.** `navigator.config.ts` exports flows built from a typed DSL (`flow`,
   `navigate`, `fillAndCheck`, `click`, `waitFor`, `expectVisible`, `expectText`).
3. **Gate CI.** `npx navigator verify` pushes, runs every flow against the customer's
   own dev server, exits non-zero on any failed postcondition.

```bash
cd sdk && npm install && npm run build && npm test
export NAVIGATOR_API_KEY=nav_... NAVIGATOR_BASE_URL=http://localhost:8000
npx navigator compile   # print YAML, change nothing
npx navigator push      # upload as a new revision
npx navigator verify    # run every flow; exit 1 on failure
```

Level 3 is why the SDK ships: **the demo breaks in CI, on your schedule, instead of in
front of a prospect.** A site graph is a test suite that doubles as a sales script.

`compile.ts` emits YAML and stops. The SDK deliberately does **not** validate.

## The docs pipeline (phase 8, done)

```bash
.venv/bin/python -m navigator.docs build   # regenerate every artifact
.venv/bin/python -m navigator.docs check   # exit 1 if anything committed is stale
npx fern-api check                         # validate the Fern project (no auth)
```

One generator (`navigator/docs/build.py`), six committed outputs, all derived from live
code: FastAPI's own `app.openapi()`, the `ToolCall` discriminated union, the
`CheckKind` literals, Pydantic `model_fields`.

| Output | For |
|---|---|
| `docs/index.html` | One self-contained file. No CDN, no build step; opens over `file://` |
| `fern/pages/integration.mdx` | Narrative half of the hosted Fern docs |
| `fern/openapi/openapi.yml` | The server's exact schema |
| `fern/docs.yml`, `fern.config.json`, `generators.yml` | Fern project config |

**The enforcement is a red test, not a git hook.**
`tests/test_docs.py::test_committed_docs_are_current` regenerates and diffs against
what's committed. Add an endpoint, rename a field, add a postcondition kind — the test
goes red and names the fix command. Not bypassable with `--no-verify`. That test is the
entire mechanism behind "the docs can't drift". **If you change the API, run
`python -m navigator.docs build` and commit the result.**

Fern reads the spec from the committed `fern/openapi/openapi.yml` (via `api.specs` in
`generators.yml`), deliberately **not** from a live URL — a URL only resolves once
something is deployed and would make every docs build depend on that deployment.
Related: the `api:` nav entry in `docs.yml` must carry **no** `openapi:` key, or
`fern check` fails with `does not match any allowed schema`.

Two constants at the top of `build.py`; everything in `fern/` is generated from them:

| Constant | Value | Must be |
|---|---|---|
| `ORGANIZATION` | `resiliencesoft` | The Fern org slug, exactly — a mismatch 403s at publish |
| `DOCS_INSTANCE` | `navigator.docs.buildwithfern.com` | Globally unique across Fern, not just within the org |

`DOCS_INSTANCE` is a separate constant rather than `f"{ORGANIZATION}.docs..."` on
purpose: the `resiliencesoft` org already hosts a second docs site, so deriving the
subdomain from the org slug would collide.

Accent color is `#0a5c31` (8.11:1 on white). Fern silently adjusts accents below WCAG
AAA 7:1, so `html.py` and `build.py` must carry the same value or HTML and Fern drift.

**Live now: https://navigator.docs.buildwithfern.com** (200; redirects to
`/getting-started/integration-guide`). Published by run `30630757995`,
`deploymentId=019fb826-91cc-700d-be51-9c4c252ac1e2`, 15/15 endpoints registered.

### CI

| Workflow | Trigger | Does |
|---|---|---|
| `ci.yml` | PR + push to main | `docs check`, pytest, SDK build/test, `fern check` |
| `preview-docs.yml` | PR touching docs/API/schemas | Posts a preview URL as a PR comment |
| `publish-docs.yml` | Push to main + manual | `docs check`, then `fern generate --docs` |

Both `publish-docs` and `ci` run `docs check` **before** touching Fern. Without that
ordering, a PR changing a route without rebuilding would publish a spec describing an
API that no longer exists — exactly the drift the pipeline prevents.

`preview-docs` uses `pull_request`, not `pull_request_target`, and gates on
`head.repo.full_name == github.repository`, so fork PRs skip rather than receive
`FERN_TOKEN`.

`sdk` job runs `node --test` bare. Do **not** write `node --test test/` — local Node 20
accepts a directory, CI's Node 24 does not (`Cannot find module .../sdk/test`). The
flag also does not expand globs.

### Fern gotcha worth knowing

The Fern **dashboard's** "Set FERN_TOKEN" button is broken — Venus API 500s
(`Failed to generate token via Venus API`, trace IDs `8f08c880-6698-4b8d-8323-08c9fbded5a6`
and `e7c775ae-fb65-43b8-805e-589c5f772c42`). The **CLI** path works fine. Mint tokens
with `npx fern-api token`, never the dashboard button. A dashboard-produced token was
the sole cause of a CI `403 "User does not belong to organization"` that looked like a
membership problem and wasn't.

Benign, non-blocking noise in publish output: `ENOENT ... ai_examples_override.yml`,
`Failed to obtain JWT from Venus: 401 ... AI example enhancement will be skipped`,
`Failed to enhance example after 4 attempts: Lambda returned 502
{"error":"OpenAIResponseParseError"}`, and `Uploading 0 files` (no images). Docs
publish regardless.

## Tests

139 Python tests + 12 SDK tests, all passing.

`tests/`: `conftest.py`, `fixtures/`, `test_action_log.py`, `test_api.py`,
`test_docs.py`, `test_graph.py`, `test_registry.py`, `test_site_graph.py`,
`test_tools.py`, `test_verify.py`. All run headless.

```bash
.venv/bin/python -m pytest -q
```

## What's left, with the specifics each phase needs

### Phase 2 — LLM planning, STT, retrieval

Replace `agent/nodes/planning.py`'s site-graph replay with a Groq
`llama-3.3-70b-versatile` call that picks among the named flows in `PageSpec.flows`.
The flow library already exists and is already validated — the planner picks, it does
not invent.

- `voice/stt.py`: `silero_vad.load_silero_vad()`, score each frame, emit end-of-speech;
  then `Groq audio.transcriptions.create` with Whisper v3 Turbo.
- `agent/nodes/listening.py`: Silero VAD over the Attendee audio stream (needs phase 3
  for a real stream, but is testable against a wav).
- `memory/collections.py`: `chromadb.PersistentClient(path=...)`,
  `get_or_create_collection`. Names must stay namespaced by `product_id`.
- `memory/retrieval.py`: query `corrections` and `product_knowledge`.

**Ceiling to know before selling seats:** Groq's free tier caps at 1,000 requests/day
for the 70B model, **per organization, not per key**. That's the first wall a
multi-tenant deployment hits.

### Phase 3 — Attendee

`meeting/attendee.py`: `POST /bots {meeting_url, bot_name}` → Bot; `GET /bots/<id>`;
`POST /bots/<id>/leave`; send Piper's wav via the output-audio endpoint; websocket
audio in, yielding PCM frames; v4l2loopback device fed by ffmpeg capturing the browser
so the prospect sees the demo. Then `agent/nodes/joining.py` calls
`deps.attendee.join(...)` and polls until the bot is in the call.

### Phase 4 — reflection + vision

- `agent/nodes/reflecting.py`: for each entry in `state["failures"]`, ask the
  configured `LLMProvider` for a corrective rule; gate with a single yes/no from
  `llama-3.1-8b-instant` (8b not 70b on purpose — it's a cheap filter).
- `agent/providers.py`: implement Gemini (`google.genai` `models.generate_content`,
  and `types.Part.from_bytes(png, "image/png")` for vision) and OpenAI
  (`chat.completions.create`, base64 data URI image part).
- `browser/verify.py`: screenshot the element and ask the LLM only when
  `VerifyResult.ambiguous` is True. Vision is a fallback, never the primary check.
- `api/app.py`: `/v1/products/corrections/pending` currently returns empty — read the
  pending-review table filtered by `product_id`. Corrections are human-approved before
  they reach the live collection.

Per-product Chroma collections land here.

### Phase 6 — recorder

`navigator record --url <app>` opens a browser, a human clicks through the demo once,
and the Playwright trace becomes a draft site graph: `data-testid`/`id` selectors
preferred over brittle CSS paths, one postcondition guessed per action from what
changed in the DOM. The human then reviews and names things. It does **not** remove the
human — a guessed postcondition is a suggestion, and deliberateness is the whole point
of postconditions.

### Also open

- `api/runner.py`: `_demos` is a per-process dict, so `--workers 1` is mandatory. Needs
  shared state (Redis or Postgres) before a real deployment.
- Docker Compose was never written — Docker isn't installed on this machine.

## Onboarding paths (all produce the same validated SiteGraph)

| Customer situation | Path | Costs them |
|---|---|---|
| Won't touch their code | Recorder (phase 6) | Click through once, review the draft |
| Will ship an attribute per element | SDK level 1 | One attribute per demo element |
| Wants the demo to never silently rot | SDK levels 2–3 | Config file + a CI job |

## Costs

Free on free tiers: Groq, Gemini 2.5 Flash, Piper, Silero VAD, Chroma, Playwright,
self-hosted Attendee. OpenAI (`gpt-4o-mini` / `gpt-4o`) is the paid alternate behind
the same protocol — `NAVIGATOR_REFLECT_PROVIDER=openai`.

## Licensing

Piper is GPL-3.0 (`OHF-Voice/piper1-gpl`; the MIT `rhasspy/piper` is archived). It runs
as a **subprocess, not a library import**. Swap `voice/tts.py`'s `Speaker` if that
doesn't suit your distribution.

## Git

```
261a427 Point Fern at the real org, and fix npm test on Node 24
ecd00bc Publish docs from GitHub, gated on the docs not being stale
e77f491 Add Phase 7 SDK and Phase 8 docs pipeline
74c2ea2 Add comprehensive test suite for API, graph, registry, site graph, tools, and verification
```

All pushed to `origin/main`. Repo `Dewashish-resiliencesoft/Navigator_AI`, private.

## One security action outstanding

The `FERN_TOKEN` currently in the repo secret was pasted into a chat transcript, so
treat it as exposed. It is a non-expiring, org-scoped key. Rotate it:

```bash
npx fern-api token | grep -oE 'fern_[A-Za-z0-9_-]+' | gh secret set FERN_TOKEN
```

Then revoke the old token in the Fern dashboard. The pipe keeps the value out of shell
history and scrollback.
