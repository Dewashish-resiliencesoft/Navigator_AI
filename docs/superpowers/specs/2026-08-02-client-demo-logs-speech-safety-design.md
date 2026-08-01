# Client demo logs + speech safety

**Date:** 2026-08-02  
**Status:** approved for planning  
**Audience:** Navigator client (tenant) dashboard only — not end-user / prospect UI  
**Approach:** poll + SQLite run metadata (no SSE/WebSocket for v1)

## Goals

1. **Logs for clients** — realtime-enough view of demo runs with timestamps, full technical ActionLog detail on expand, plus host OS/device and meeting-side context.
2. **Speech safety** — end users in the live meeting never hear Playwright/timeout jargon, secrets, credentials, or exact technical errors; generic soft fallbacks only. Prospects cannot coax the agent into speaking secrets or raw errors.

## Non-goals

- Push streaming (SSE/WS) — poll is enough for loopback client console.
- Inferring prospect phone/OS from Meet/Zoom (we do not have that; label honestly).
- Changing public `/v1` unauthenticated surfaces to expose full ActionLog.
- Retention beyond 7 days in v1.

## Decisions (locked)

| Topic | Choice |
|---|---|
| Device / OS | **Both:** host env (OS, arch, hostname) + meeting platform/status |
| Expand detail | **Full ops** (tool, selector, page, pass/fail, raw error) — dashboard only |
| UI placement | **Both:** Live Demo live tail + new **Logs** sidebar tab for history |
| Retention | **7 days** persisted |
| Transport | **HTTP poll** (~1–2s live tail, ~3–5s Logs list) |

## Architecture

```
demo start ──► demo_runs row (meta: platform, host, status, times)
                    │
agent tools ──► action_log (existing) keyed by session_id
                    │
client UI ──poll──► GET /client/api/runs?days=7
                    GET /client/api/runs/{session_id}/events
                    │
TTS path ──► speaking._prospect_safe (+ verifying soft fail)
             listening/planning refuse secret-exfil prompts
```

### Data: `demo_runs`

Store in the **same SQLite DB** as ActionLog (one place to prune).

Suggested columns:

- `session_id` (PK / unique)
- `demo_id` (runner id if distinct)
- `product_id`
- `platform` (`zoom` | `meet` | `static` | …)
- `status` (`starting` | `running` | `finished` | `failed`)
- `host_os`, `host_release`, `host_machine`, `host_name` (sanitized; no secrets)
- `browser` (e.g. Chromium version if known; else empty)
- `meeting_label` (redacted: meet code / platform name — **no** join tokens, ZAK, API keys)
- `started_at`, `ended_at` (ISO UTC)
- optional: `fail_count` denormalized or computed from action_log on read

**Prune:** on list/write, delete `demo_runs` (and optionally orphan `action_log` rows for those sessions) older than **7 days**.

Capture host fields via stdlib `platform` / `socket.gethostname()` at demo start. Update status as runner progresses.

### API (client dashboard auth only)

- `GET /client/api/runs?days=7` → list run cards (meta + fail count).
- `GET /client/api/runs/{session_id}` → one run meta.
- `GET /client/api/runs/{session_id}/events` → full ActionLog entries for that session, scoped by `product_id`.

Rules:

- Always filter by authed `product_id` (tenant isolation).
- 404 if missing or wrong product.
- Empty 7d window → `[]`, not error.
- Do **not** put full technical events on speech/narration paths.

Mirror under `/v1/...` only if already using the same AuthedProduct pattern; prefer `/client/api` for dashboard.

### UI

**New sidebar tab: Logs**

- List runs (7d): status chip, start time, platform, short host OS line, fail count.
- Expand → timeline: timestamp | tool | page | ok/fail | detail (selector + stored error text).
- Poll list while tab open (~3–5s).

**Live Demo panel**

- While a run is active: compact “Live log” tail (last ~20 events, full detail).
- Link/control: “Open in Logs” for full history.
- Poll ~1–2s while running.

Visual language: match existing client console (tabs, motion). Cards only where interaction needs a container. Copy speaks to **companies using Navigator**, not prospects.

### Speech safety

**TTS gate (`speaking`)** — before every `speaker.say`:

- Scrub / replace if line matches technical patterns (Page.click, Timeout, action failed, locator, selectors dumps, stack traces).
- Scrub credential-like patterns (`password`, `api_key`, `token`, `Bearer`, `secret`, `KEY=`, long hex/base64 blobs).
- On hit → rotate short generic soft lines (glitch on our side, not yours, we’re fixing it).

**`verifying`** — failures already narrate soft only; keep that. Technical detail stays in ActionLog only.

**Jailbreak / exfil resistance** — in listening/planning (or a tiny shared helper):

- If prospect asks for passwords, API keys, secrets, “exact error”, stack traces, logs, env vars → fixed refuse + redirect to continue the product demo.
- Never echo `ToolResult.detail`, ActionLog, env, or credentials into `narration`.

**Boundary:** full tech = client Logs only; meeting speech = soft/generic only.

## Testing

- Run create / list / prune (7d): old rows gone; product-scoped.
- Cross-tenant: product A cannot read product B runs/events.
- Speech scrub unit tests: Playwright string + fake `password=…` → soft line.
- Refuse-secret path: fixed refuse, no echo of secret-like content.
- Frontend: manual smoke — Logs tab + Live Demo tail during a demo (automated UI only if cheap fixtures exist).

## Out of scope / later

- SSE push
- Prospect device fingerprinting
- Retention > 7 days or export/download
- Admin cross-tenant log viewer

## Implementation order (for plan)

1. `demo_runs` store + prune + wire demo start/end status  
2. Client API routes + Fern rebuild  
3. Logs panel + Live Demo tail (poll)  
4. Speech scrub hardening + refuse-secret prompts + tests  
5. `graphify update .` after navigator code edits  
