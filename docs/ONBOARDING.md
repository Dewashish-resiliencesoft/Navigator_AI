# Developer onboarding

Read [`PRODUCT_MODEL.md`](PRODUCT_MODEL.md) for *what* Navigator is. This doc is
only *how to get it running*.

For Google Meet (create link → auto-join → screenshare), see
[`GOOGLE_MEET_SETUP.md`](GOOGLE_MEET_SETUP.md).

For clear start/stop commands (daily use), see [`START_APP.md`](START_APP.md).

## What the stack is

Navigator is an AI agent that joins a video meeting and gives a live, interactive
demo of a web product. Four moving parts:

| Layer | Tech | What it does |
|---|---|---|
| **API + dashboard** | FastAPI + uvicorn, port **8000** | Serves `/v1/*` (wrapper API), `/client` (Client dashboard), `/docs`. Single process — live demo state is in-memory. |
| **Dashboard UI** | React + Vite + Tailwind, `navigator/client/web` | Built to `dist/`, served *by FastAPI*. Only run Vite's dev server if you are editing the UI. |
| **Browser driver** | Playwright (Chromium) | Actually clicks through the Client's product. Driven by a site graph, never hardcoded selectors. |
| **Meeting bot** | Self-hosted **Attendee** (Django), port **8002** | Joins the call, plays Navigator's TTS audio in, shares a webpage as video, streams mixed call audio back over websocket. |

Agent brain is LangGraph; voice is Piper or Fish TTS out and Groq
`whisper-large-v3-turbo` STT in; memory is Chroma.

Attendee is a **separate repo** at `~/projects/attendee`, self-hosted so there is
no vendor bill. Never push it. See "Self-hosting Attendee" in the root README for
how it was set up.

### Port map

| Port | Owner |
|---|---|
| 8000 | Navigator API + dashboard |
| 8001 | Attendee webpage-streamer |
| 8002 | Attendee app (API) |
| 9000 / 9091 | MinIO (S3 stand-in) API / console |

## Before running

1. **Python deps** — `.venv` already exists. If starting fresh:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install -e '.[api,voice,memory,llm,dev]'
   .venv/bin/playwright install chromium
   ```

2. **`.env`** — copy `.env.example` and fill it. Gitignored; never commit it.
   Minimum for a live meeting demo: `NAVIGATOR_GROQ_API_KEY` (STT),
   a TTS provider, `NAVIGATOR_ATTENDEE_BASE_URL` + `NAVIGATOR_ATTENDEE_API_KEY`,
   and `NAVIGATOR_CREDENTIAL_KEY`.

3. **Docker group** — a fresh `usermod -aG docker` needs a re-login. Until then
   prefix docker commands with `sg docker -c "..."`.

4. **Attendee stack up** — required for live demos. Navigator refuses to start
   one if `:8002` does not answer.
   ```bash
   cd ~/projects/attendee
   docker compose -f dev.docker-compose.yaml -f local.docker-compose.yaml \
       --profile webpage-streamer up -d
   ```
   Both `-f` flags are mandatory: there is no plain `docker-compose.yaml` here,
   so compose's automatic override lookup never fires and `local.` would be
   silently ignored. `--profile webpage-streamer` is mandatory too, or the
   streamer service is silently omitted and screenshare has nothing to talk to.

   Check all 8 containers: `sg docker -c "docker compose -f dev.docker-compose.yaml -f local.docker-compose.yaml ps"`

## Running

```bash
.venv/bin/uvicorn navigator.app.main:app --port 8000 --workers 1
```

Open `http://127.0.0.1:8000/client`. `--workers 1` is required — see the TODO on
`DemoRunner`.

Editing the dashboard UI (backend must already be up, it proxies to `:8000`):

```bash
cd navigator/client/web && npm run dev
```

Tests: `.venv/bin/python -m pytest -q`

## Gotchas that will cost you an hour

- **`*.md` is gitignored repo-wide** (`.gitignore:46`). New docs need an explicit
  `!` negation to be committable. That is why this file has one.
- **Attendee's `SITE_DOMAIN` is not settable via `.env`** —
  `attendee/settings/development.py:6` hardcodes `localhost:8000` after importing
  base. Confirmation links in its logs say `:8000`; open them on `:8002`.
- **The bot does not admit itself.** Auto-join works by making the *room* open
  (`accessType: "OPEN"` for Meet, `waiting_room: False` for Zoom). There is no
  admit-guest code in this repo, by design.
- **Test demos never bill.** `origin: "dashboard_test"` is excluded from
  `ActionLog.product_metrics()`. Self-hosted Attendee also has
  `CHARGE_CREDITS_FOR_BOTS=false`, so bots never meter either way.
- **Regenerate API docs after changing routes:** `python -m navigator.docs build`.
  Skipping it fails `tests/test_docs.py`.
