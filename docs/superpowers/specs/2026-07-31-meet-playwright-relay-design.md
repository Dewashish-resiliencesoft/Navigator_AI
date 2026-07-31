# Phase 3 slice: Meet join + Playwright relay + Teams notify + cursor

**Date:** 2026-07-31  
**Status:** approved in dialogue (Teams webhook A, Meet URL env A, full Playwright B, frame-relay+tunnel 1)  
**Out of scope:** STT/listening, reflection, Piper→Meet audio, v4l2loopback, full ResilioHub site graph

## Goal

When a live Meet demo starts: notify a Teams channel with the Meet link, drive the real product in Playwright (with a visible cursor animation), stream that viewport into Google Meet via Attendee by tunneling a local frame-relay page, then tear down cleanly.

## Decisions locked

| Topic | Choice |
|---|---|
| Teams | Incoming Webhook (`NAVIGATOR_TEAMS_WEBHOOK_URL`) |
| Meet URL | `NAVIGATOR_MEETING_URL` env |
| Product view | Full Playwright login + drive (not naked public URL) |
| Meet video bridge | CDP/screencast → local `/view` → cloudflared/ngrok → Attendee `voice_agent_settings.url` |
| Cursor | CSS overlay + animated moves before clicks/fills |
| CI | Live test skipped unless `NAVIGATOR_MEET_LIVE=1` |

## Architecture

```
start (live path)
  → notify_teams(meeting_url)
  → Playwright Chromium + install_cursor(page)
  → login(product_url, email, password) → dashboard
  → FrameRelay.start(page)          # CDP screencast → HTTP /view @ 1280x720
  → Tunnel.start(local_port)        # public HTTPS
  → AttendeeClient.join(
        meeting_url,
        bot_name="Navigator AI",
        voice_agent_settings={"url": f"{public}/view"},
     )
  → poll get(bot_id) until state == joined (or timeout)
  → drive short demo actions (cursor-visible)
  → leave(bot_id); stop tunnel; stop relay; close browser
```

Attendee loads **our relay page**, not ResilioHub directly. Login and product interaction stay in Playwright. Relay only mirrors pixels (and auto-requests mic so Attendee’s voice-agent container is happy).

## Env vars (local `.env` only — never commit secrets)

| Var | Purpose |
|---|---|
| `NAVIGATOR_ATTENDEE_BASE_URL` | Must be reachable Attendee host (not dead localhost), e.g. `https://app.attendee.dev/api/v1` |
| `NAVIGATOR_ATTENDEE_API_KEY` | Attendee token |
| `NAVIGATOR_MEETING_URL` | Google Meet link |
| `NAVIGATOR_PRODUCT_URL` | e.g. `https://resiliohub.com/dashboard/` (or login entry URL) |
| `NAVIGATOR_PRODUCT_LOGIN_EMAIL` | Product login |
| `NAVIGATOR_PRODUCT_LOGIN_PASSWORD` | Product login |
| `NAVIGATOR_TEAMS_WEBHOOK_URL` | Teams Incoming Webhook |
| `NAVIGATOR_TUNNEL_BIN` | Default `cloudflared` |
| `NAVIGATOR_MEET_LIVE` | `1` to enable live pytest |

Update `.env.example` with **empty placeholders only**.

**Security:** credentials shared in chat are treated as exposed; keep them out of git; rotate the product bot password when practical.

## Components

### 1. `navigator/meeting/attendee.py`

Implement:

- `join(meeting_url, bot_name=..., voice_agent_url: str | None = None) -> Bot`
  - `POST {base}/bots` with `Authorization: Token {api_key}`
  - Body includes `voice_agent_settings: {url}` when URL provided
- `get(bot_id) -> Bot` — `GET {base}/bots/{id}`; map API state string → `BotState`
- `leave(bot_id)` — `POST {base}/bots/{id}/leave`
- Keep `speak` / `audio_stream` / `send_video` as stubs (later)

Use `httpx` (already in `dev`) or stdlib `urllib` — prefer `httpx` if we add it to a meeting optional extra or reuse from API deps. Ponytail: `urllib.request` in base install to avoid new hard dep; or depend on `httpx` already pulled by fastapi in practice. Prefer **stdlib** for the client so core stays lean.

### 2. `navigator/meeting/teams.py`

- `notify_demo_link(*, webhook_url: str, meeting_url: str, message: str | None = None) -> None`
- POST JSON `{"text": "..."}` to Incoming Webhook
- Raise on non-2xx

### 3. `navigator/meeting/relay.py`

- Serve `GET /view`: HTML+JS canvas 1280×720; on load call `getUserMedia({audio:true})`
- Push JPEG/PNG frames from Playwright CDP screencast (or periodic screenshot) over WebSocket or multipart
- `FrameRelay.start(page, host="127.0.0.1", port=0) -> RelayHandle` with `.url` and `.stop()`
- ponytail: screenshot loop at ~5–10 fps if CDP screencast wiring is painful; ceiling noted in comment; upgrade = CDP screencast

### 4. `navigator/meeting/tunnel.py`

- `Tunnel.start(local_port, binary="cloudflared") -> TunnelHandle` with `.public_url` and `.stop()`
- Parse `https://*.trycloudflare.com` (or ngrok equivalent) from process stdout
- Fail fast if binary missing

### 5. `navigator/browser/cursor.py`

- `install_cursor(page)` — inject fixed overlay + CSS
- `cursor_click(page, x, y)` / integrate with existing tools when a `CallDeps.show_cursor` flag is set
- Animate move (steps), then click ripple, then real Playwright action

### 6. `navigator/browser/product_login.py` (or under `meeting/`)

- `login_resiliohub(page, *, base_url, email, password) -> None`
- Selectors as constants or env overrides; wait for post-login signal
- Screenshot on failure to `tmp_path` / archive

### 7. Orchestration

- Prefer a dedicated entry: `navigator/meeting/live_demo.py` or extend `joining` + a pytest-driven script
- `joining` node: if `deps.meeting_url` set, run notify + attendee join (relay must already be up — runner owns relay lifecycle)
- DemoRunner / live test owns: browser → login → relay → tunnel → join → actions → cleanup

### 8. Settings

Extend `navigator/settings.py` + `.env.example` with the new keys (empty defaults).

## Testing

| File | Mode |
|---|---|
| `tests/test_attendee.py` | Mock HTTP for join/get/leave |
| `tests/test_teams.py` | Mock webhook |
| `tests/test_cursor.py` | Playwright page has overlay |
| `tests/test_relay.py` | `/view` returns 200; optional frame push |
| `tests/test_meet_demo.py` | Live; skip unless `NAVIGATOR_MEET_LIVE=1` |

Docs: new settings only — if OpenAPI gains nothing, docs check stays green. If DemoRunner/API starts using `meeting_url`, update docs via `python -m navigator.docs build`.

## Success criteria

1. Unit tests green without live Meet.
2. With live env + `MEET_LIVE=1`: Teams message arrives; Attendee bot joins Meet; Meet participants see relay stream of logged-in Playwright session with cursor motion.
3. No secrets in git; `.env.example` has placeholders only.
4. Existing 154 tests still pass; live test skipped in default CI.

## Non-goals

- Inventing ResilioHub site-graph flows for full product demo script
- Self-hosted Attendee / Docker / v4l2
- STT over meeting audio
