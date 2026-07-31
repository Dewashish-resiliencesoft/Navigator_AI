# Navigator AI

An agent that joins Google Meet / Zoom calls and gives live, interactive demos of a
web product. It drives the real site in a browser, narrates what it does out loud,
types prospect-supplied data into the product during the call, and learns
corrective rules from its own verification failures.

The WhatsApp CRM dashboard is the first product it demos, not the only one — the
only product-specific artifact in the codebase is a site graph YAML.

**Status: Phases 1 and 5 done.** A scripted (no-LLM) demo loop runs end to end, and
the wrapper API serves many products from one deployment. LLM planning, STT,
meeting integration, and reflection are stubs with `TODO(phase N)` markers.

## Setup

```bash
python3 -m virtualenv .venv          # or: python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m playwright install chromium
cp .env.example .env

# Optional: real speech instead of printed narration
.venv/bin/pip install piper-tts
.venv/bin/python -m piper.download_voices en_US-lessac-medium --data-dir voices
```

## Run the demo

```bash
.venv/bin/python -m navigator.demo                    # headful, speaks aloud
.venv/bin/python -m navigator.demo --headless --mute  # what CI does
```

It opens the inbox, types a message, sends it, verifies each step's postcondition
against the DOM, narrates the outcome, and prints the action log. Exit code is
non-zero if any step failed.

```bash
.venv/bin/python -m pytest -q
```

## The wrapper API

One deployment, many products. A customer registers, uploads a site graph for
their own web app, and asks for a demo — no code changes on our side, because the
site graph is the only interface between Navigator and any product.

```bash
.venv/bin/uvicorn navigator.api.app:app --port 8000 --workers 1
```

`--workers 1` is required for now: live demo state is in-process. See the TODO on
`DemoRunner`.

```bash
# 1. register — the api_key in the response is shown exactly once
curl -sX POST localhost:8000/v1/products -H 'Content-Type: application/json' \
     -d '{"name":"Acme Inbox"}'

# 2. upload a site graph (validated before it is stored; a bad push cannot
#    break a running demo)
curl -sX PUT localhost:8000/v1/products/site-graph \
     -H "Authorization: Token $KEY" -H 'Content-Type: application/json' \
     -d "{\"yaml\": $(jq -Rs . < my_product.yaml)}"

# 3. run a demo, then poll it
curl -sX POST localhost:8000/v1/demos -H "Authorization: Token $KEY" \
     -H 'Content-Type: application/json' \
     -d '{"page_id":"inbox","flow_id":"send_message"}'
curl -s localhost:8000/v1/demos/$DEMO_ID -H "Authorization: Token $KEY"
curl -s localhost:8000/v1/demos/$DEMO_ID/actions -H "Authorization: Token $KEY"
```

Full route list at `/docs`. Other endpoints: `GET /v1/products/flows` (what the
active graph offers), `/v1/products/site-graph/revisions` and
`/activate` (rollback), `/v1/products/failures` (which flows are rotting),
`/v1/products/corrections/pending` (empty until Phase 4).

**Tenant isolation.** `product_id` always comes from the API key, never from a
path, so a route cannot read across tenants by accident. Each demo gets its own
browser *context* — separate cookie jar, storage, and session, not merely a
separate page. ActionLog rows and archive directories are namespaced by
`product_id`; so are Chroma collection names, because a correction learned
demoing one product is wrong and possibly confidential for another.

**Per-product narration.** The intro and step narration are rendered from a
`persona` block in the site graph and from selector aliases read as English
(`send_button` → "send button"). No product name appears anywhere in the code.
Authoring readable aliases is what makes a new product sound right.

## How it works

A LangGraph state machine, explicit rather than an agent loop:

```
joining -> introducing -> speaking
           listening -> planning -> speaking <- verifying <- executing
```

Everything that talks routes through SPEAKING. The EXECUTING -> VERIFYING ->
SPEAKING cycle repeats once per tool call, so the agent never builds on an action
it hasn't verified.

The agent has exactly four tools — `click_element`, `fill_field`, `navigate`,
`wait_for` — and no free-form DOM access. Every call declares a **postcondition**
at call time (a selector/state assertion), and VERIFYING checks it against real
DOM state with no LLM involved. Both the expectation and what actually happened
land in the ActionLog, which is what makes a call debuggable afterwards and what
reflection later learns from.

### The site graph

`navigator/config/sites/*.yaml` maps pages -> selector aliases -> flows ->
postconditions. It is hand-authored on purpose: the agent never infers selectors,
and callers pass *aliases*, never CSS, so a DOM change is a config edit. A bad
site graph fails at load with a message naming the page, flow, and alias — never
mid-call.

To demo a different product, write a site graph for it. No Python changes.

## Layout

| Path | What's in it |
|---|---|
| `navigator/schemas.py` | Every model that crosses a module boundary |
| `navigator/config/` | Site graph loader, validator, and site YAMLs |
| `navigator/browser/` | Playwright session, the four tools, postcondition checking |
| `navigator/agent/` | State machine, one file per state |
| `navigator/api/` | Wrapper API: product registry, demo runner, FastAPI app |
| `navigator/logs/` | ActionLog (SQLite) |
| `navigator/voice/` | Piper TTS; STT is a Phase 2 stub |
| `navigator/memory/` | Chroma collections; Phase 2 stubs |
| `navigator/meeting/` | Attendee API client; Phase 3 stub |

`browser/` never imports `agent/`. Nothing outside `browser/` and `config/` ever
sees a CSS selector — that rule is what makes the system product-agnostic.

There is exactly one site graph validator (`config.site_graph.parse_site_graph`).
The file loader, API uploads, and the future SDK push all go through it, so a
customer gets the same error message however the graph reached us.

## Costs

Free on the free tiers: Groq (Llama 3.3 70B + Whisper v3 Turbo), Gemini 2.5 Flash
for reflection and vision, Piper, Silero VAD, Chroma, Playwright, self-hosted
Attendee. OpenAI (`gpt-4o-mini` / `gpt-4o`) is a paid alternate behind the same
`LLMProvider` protocol — set `NAVIGATOR_REFLECT_PROVIDER=openai`.

Groq's free tier caps at 1,000 requests/day for the 70B model and is per
organization, not per key. That's the first ceiling a multi-tenant deployment
hits.

## Licensing note

Piper is GPL-3.0 (the MIT `rhasspy/piper` is archived; maintained work is
`OHF-Voice/piper1-gpl`). It runs as a subprocess, not a library import. Swap
`voice/tts.py`'s `Speaker` implementation if that doesn't suit your distribution.
