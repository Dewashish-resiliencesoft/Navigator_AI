# Navigator AI: Developer Onboarding

This is the deep code-oriented guide for engineers joining the Navigator AI
repository. It explains the repository layout, runtime relationships, backend
and frontend control flow, dashboard buttons, state management, meeting/audio
infrastructure, tests, and common debugging paths.

This guide describes the current implementation. For product and security rules,
`docs/PRODUCT_MODEL.md` is authoritative. For a short setup checklist, see
`docs/ONBOARDING.md`. For the public API schema, run the server and open
`/docs`, or inspect the generated files under `fern/`.

## 1. Product Mental Model

Navigator is a multi-tenant API and dashboard for demonstrating a Client's real
web product to an End User. The AI does not invent arbitrary browser actions. It
uses a validated site graph containing named pages, selector aliases, flows, and
postconditions.

The basic call is:

```text
Client site graph
        |
        v
FastAPI request -> Registry -> DemoRunner -> meeting + browser
                                      |
                                      v
                         agent graph / playback engine
                                      |
                         Playwright actions + narration
                                      |
                         verification + ActionLog
```

The repository has three distinct actors:

| Actor | Meaning | Authentication | Surface |
|---|---|---|---|
| Platform | Resiliencesoft operating Navigator | Internal deployment access | All server code |
| Client | Company buying Navigator | Dashboard JWT or server-side `nav_` key | Dashboard, graphs, flows, settings |
| End User | Visitor on the Client's landing page | Short-lived `sess_` token | Public demo only |

Never use "end user" to mean the Client. This distinction affects auth, billing,
site-graph revisions, and which UI a person is allowed to see.

## 2. Non-Negotiable Rules

Read `docs/PRODUCT_MODEL.md` before changing product behavior.

- `product_id` comes from the authenticated credential, never from a request
  path or request body.
- `origin` is assigned at the auth boundary and is immutable:
  - `dashboard_test` for dashboard and headless verification.
  - `public_embed` for an End User public demo.
- Public live demos run the published site-graph revision only.
- Dashboard tests may run the latest draft revision.
- Dashboard tests do not count toward billable usage.
- A `nav_` API key must remain server-side.
- Public browser code receives only a single-use `sess_` token.
- The dashboard is a Client surface and must stay behind dashboard auth.
- The public embed must expose the button label `Start a demo.`
- A demo gets its own browser context. Do not replace it with a shared page.
- The agent receives aliases and typed graph objects, not arbitrary CSS selectors.
- Every browser action has a postcondition and is verified against the DOM.
- Live demo state is in-process unless a Redis-backed `DemoStateStore` is
  explicitly configured. Use one Uvicorn worker by default.

## 3. First-Time Setup

### Prerequisites

- Python 3.11 or newer.
- Node.js for the dashboard and SDK.
- Docker Compose for self-hosted Attendee.
- Playwright Chromium.
- `cloudflared` for live public audio and screenshare tunnels.
- Groq credentials for STT and text-model paths when enabled.
- Gemini credentials for Gemini Live and vision paths when enabled.

### Python environment

```bash
cd Navigator_AI
python3 -m venv .venv
.venv/bin/pip install -U pip wheel
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m playwright install chromium
```

If Chromium reports missing system libraries:

```bash
.venv/bin/python -m playwright install-deps chromium
```

### Environment

```bash
cp .env.example .env
```

Important settings are loaded by `navigator/core/settings.py` into the singleton
`settings`. Do not read environment variables directly in feature code when a
typed setting already exists.

Common variables:

| Variable | Used by | Purpose |
|---|---|---|
| `NAVIGATOR_ATTENDEE_BASE_URL` | `meeting/attendee.py` | Attendee API base URL |
| `NAVIGATOR_ATTENDEE_API_KEY` | `meeting/attendee.py` | Attendee auth |
| `NAVIGATOR_GROQ_API_KEY` | `voice/stt.py`, agent providers | STT/text provider |
| `NAVIGATOR_GEMINI_API_KEY` | `voice/live_agent.py` | Gemini Live/vision |
| `NAVIGATOR_CREDENTIAL_KEY` | `app/credential_vault.py` | Encrypt Client login data |
| `NAVIGATOR_TUNNEL_BIN` | `meeting/tunnel.py` | `cloudflared` executable |
| `NAVIGATOR_MEETING_PLATFORM` | meeting provider selection | `google_meet`, `zoom`, or configured provider |
| `NAVIGATOR_MEETING_URL` | CLI/static meeting fallback | Static meeting URL only |
| `NAVIGATOR_DB_PATH` | registry/log stores | SQLite application database |
| `NAVIGATOR_REDIS_URL` | `app/state.py` | Optional multi-worker state coordination |

Never commit `.env`, API keys, credentials, service-account files, or local
database artifacts containing tenant data.

### Run the local dashboard

Build the React dashboard first:

```bash
cd navigator/client/web
npm install
npm run build
cd ../../..
```

Start Navigator with one worker:

```bash
.venv/bin/uvicorn navigator.app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

Open `http://127.0.0.1:8000/client`.

The server lifespan in `navigator/app/main.py` calls
`meeting/attendee_stack.py`, which can check or start local Attendee. If it is
not configured, dashboard health and readiness checks explain what is missing.

### Run the non-meeting scripted demo

This path is useful when debugging the graph, Playwright tools, narration, or
verification without Attendee, tunnels, or a real meeting:

```bash
.venv/bin/python -m navigator.demo
.venv/bin/python -m navigator.demo --headless --mute
```

### Run tests

```bash
.venv/bin/pytest -q
```

Frontend checks:

```bash
cd navigator/client/web
npm run typecheck
npm run build
```

## 4. Repository Map

Navigator is organized by feature, not as a generic controller/model/view dump.

| Path | Responsibility | Start here when... |
|---|---|---|
| `navigator/core/` | Typed settings and shared schemas | You need configuration or cross-module data |
| `navigator/app/` | FastAPI wiring, registry, runner, state | You are changing an API or demo lifecycle |
| `navigator/auth/` | JWT, refresh cookies, session auth | You are changing login or auth boundaries |
| `navigator/client/` | Dashboard backend helpers and React app | You are changing Client-facing UI |
| `navigator/agent/` | LangGraph state machine and planning | You are changing reasoning or action sequencing |
| `navigator/automation/` | Browser tools, recorder, exploration | You are changing Playwright behavior or graph authoring |
| `navigator/meeting/` | Attendee, meetings, audio, screenshare, tunnels | You are changing live calls |
| `navigator/voice/` | Gemini Live, STT, TTS, language handling | You are changing voice input/output |
| `navigator/knowledge/` | Site graphs, product brief, bio, knowledge | You are changing Client product context |
| `navigator/logs/` | SQLite ActionLog, runs, decisions, metrics | You are changing observability or billing metrics |
| `navigator/docs/` | Generated HTML/OpenAPI documentation | You changed API/schema output |
| `sdk/` | TypeScript authoring DSL and CLI | You are changing Client-side graph authoring |
| `scripts/` | Deployment, Attendee, Zoom helpers | You are changing operational setup |
| `docker/` | Compose overrides and local services | You are changing local infrastructure |
| `fern/` | Generated hosted API docs project | Never hand-edit; regenerate instead |
| `tests/` | Unit, API, integration, and contract tests | You need the expected behavior |
| `docs/` | Human-authored product and developer docs | You need policy or onboarding context |

### Backend entry points

| File | Important symbols | What it owns |
|---|---|---|
| `navigator/app/main.py` | `app`, route functions, `_run_live_demo` | HTTP boundary and dependency injection |
| `navigator/app/runner.py` | `DemoHandle`, `DemoRunner` | Threaded demo lifecycle and observable state |
| `navigator/app/registry.py` | `Registry`, `Product`, `SiteGraphRevision` | Tenant, revision, and publish state |
| `navigator/app/credential_vault.py` | `CredentialVault` | Encrypted product login credentials |
| `navigator/app/state.py` | `DemoStateStore` | Optional cross-worker state/stop messages |
| `navigator/core/settings.py` | `Settings`, `settings` | Environment-backed configuration |
| `navigator/logs/store.py` | `ActionLog` | Action rows, demo runs, metrics |

### Frontend entry points

| File | Important symbols | What it owns |
|---|---|---|
| `client/web/src/main.tsx` | React bootstrap | Mounts `App` |
| `client/web/src/App.tsx` | `App`, `PANELS`, `Toast` | Auth shell, header, panel routing, global live-demo banner |
| `client/web/src/components/Sidebar.tsx` | `TABS`, `Sidebar`, `MobileTabs` | Navigation buttons and setup links |
| `client/web/src/components/ui.tsx` | `Button`, `Card`, `Input`, `StatusPill` | Shared visual primitives |
| `client/web/src/lib/api.ts` | `api`, `request`, response types | Browser-to-FastAPI contract |
| `client/web/src/lib/demoSession.ts` | `useDemoSession` | Shared active demo state and polling |
| `client/web/src/lib/exploreSession.ts` | `useExploreSession` | Explore WebSocket/status state |
| `client/web/src/lib/productData.ts` | `useProductData` | Shared product playlist and invalidation epoch |
| `client/web/src/store.ts` | `useUi`, `errText` | Selected tab, toast, Logs handoff |
| `client/web/src/panels/*.tsx` | panel components | Feature-specific UI and event handlers |

## 5. Backend Architecture

### FastAPI dependency flow

`navigator/app/main.py` creates module-level services and exposes them through
small dependency functions such as `get_registry()`, `get_runner()`, and
`get_log()`. Tests replace these dependencies with fakes through
`app.dependency_overrides`.

Most route functions follow this pattern:

```text
HTTP request
    -> auth dependency
    -> product resolution from credential
    -> registry/log/runner dependency
    -> validate request and graph
    -> mutate or start work
    -> Pydantic response model
```

The API does not put long-running demo execution on the request thread. It
creates a `DemoHandle`, starts a daemon worker, returns `202`, and lets the UI
poll the handle.

### Authenticated surfaces

#### `nav_` API key

Used by server-side Clients and the SDK. It authenticates `/v1/*` routes. The
registry hashes and resolves the key to a `product_id`.

#### `sess_` embed token

Created by `POST /v1/session-tokens` using a `nav_` key. The public embed uses
the resulting one-use token to start exactly one public demo. Optional intake
prefill is stored with the token.

#### Dashboard JWT

Created by signup/login, refreshed through a rotating cookie, and sent as a
Bearer token by the dashboard. `/client/api/*` routes resolve the Client from
the JWT. The browser never receives a `nav_` key.

### Route groups

#### Auth routes

Implemented in `navigator/auth/routes.py` and mounted by `main.py`:

| Route | Behavior |
|---|---|
| `POST /v1/auth/signup` | Creates a Client and dashboard user |
| `POST /v1/auth/login` | Returns an access token |
| `POST /v1/auth/refresh` | Rotates the refresh cookie and returns a new access token |
| `POST /v1/auth/logout` | Ends the refresh session |

#### Product and graph routes

These are mostly `/v1/*` API-key routes and mirrored dashboard routes:

| Concern | API examples | Main implementation |
|---|---|---|
| Product identity | `/v1/products/me` | `Registry.authenticate` |
| Site graph upload | `PUT /v1/products/site-graph` | `Registry.put_site_graph` |
| Revisions | `GET /v1/products/site-graph/revisions` | `Registry` revision methods |
| Publish/activate | `POST /v1/products/site-graph/activate` | `Registry.activate` |
| Flows | `/v1/products/flows`, dashboard flow routes | graph/playlist helpers |
| Knowledge | `/v1/products/knowledge`, dashboard knowledge route | `knowledge/` modules |

#### Demo routes

| Route | Origin | Execution |
|---|---|---|
| `POST /v1/demos` | `dashboard_test` | Headless graph verification; no meeting |
| `POST /v1/session-tokens` | N/A | Mints an End User `sess_` token |
| `POST /v1/demos/start` | `public_embed` | Live meeting demo from published graph |
| `GET /v1/demos` | credential-scoped | List demo handles |
| `GET /v1/demos/{id}` | credential-scoped | Poll a demo handle |
| `POST /v1/demos/{id}/end` | credential-scoped | Stop demo and leave bot |
| `GET /v1/demos/{id}/actions` | credential-scoped | Read ActionLog entries |

Dashboard equivalents are implemented around `main.py` demo routes under
`/client/api/demos`. They use dashboard JWT auth and always create
`dashboard_test` demos.

#### Dashboard operations

The dashboard routes cover:

- Product domain and readiness.
- Product login vault.
- Agent settings and provider keys.
- Autonomy and tier settings.
- Site graph editing and publish.
- Demo script editing.
- Flow ordering and deletion.
- Manual recorder lifecycle.
- Autonomous exploration and its ticketed read-only WebSocket.
- Metrics, run history, events, and decision traces.
- Corrections and knowledge.

When adding a dashboard endpoint, update all three surfaces:

1. FastAPI route and Pydantic response model.
2. `client/web/src/lib/api.ts` request function and TypeScript type.
3. The panel that calls it, plus a test.

Then regenerate docs:

```bash
.venv/bin/python -m navigator.docs build
.venv/bin/python -m navigator.docs check
```

## 6. Demo Lifecycle

### Dashboard live test

```text
LiveDemo.tsx
  -> useDemoSession.start
  -> api.startDemo
  -> POST /client/api/demos/start
  -> DashboardAuthedProduct
  -> _run_live_demo(origin="dashboard_test")
  -> Registry.latest_revision()
  -> readiness checks
  -> MeetingProvider.create_meeting()
  -> DemoRunner.start_live()
  -> DemoRunner._run_live()
  -> run_live_meet_demo()
  -> dashboard polls GET /client/api/demos/{id}
```

The dashboard test is allowed to run a draft. For static closed-access Meet,
the server switches to admit-flow: the Client opens the meeting and admits
Navigator.

### Public embed demo

```text
Client server
  -> POST /v1/session-tokens with nav_ key
  -> embed receives sess_ token
  -> visitor clicks Start a demo
  -> navigator.js POST /v1/demos/start with sess_ token
  -> SessionTokenStore.consume_token()
  -> _run_live_demo(origin="public_embed")
  -> Registry.published_revision()
  -> open-access meeting creation
  -> DemoRunner.start_live()
  -> meeting + browser + agent
```

Public live demos reject closed-access meetings because an End User cannot admit
the bot from a waiting room.

### Headless verification

```text
SDK navigator verify
  -> compile site graph
  -> PUT /v1/products/site-graph
  -> POST /v1/demos
  -> Registry.latest_revision()
  -> DemoRunner.start()
  -> DemoRunner._run()
  -> fresh Chromium context
  -> build_graph(deps).invoke(initial_state())
  -> actions and postconditions
  -> wait for finished handle
  -> exit nonzero when failures exist
```

This path deliberately does not involve Attendee, cloudflared, meetings, or
public billing.

### `DemoRunner` internals

`DemoHandle` is the observable record shared by the worker and API:

- Identity: `demo_id`, `session_id`, `product_id`, `revision`, `origin`.
- State: `starting`, `running`, `finished`, or `failed`.
- Progress: `page_id`, `actions`, `failures`, `said`, `error`.
- Meeting: `meeting_url`, `platform`, `bot_id`, `bot_in_meeting`.
- Live leave state: `leave_grace_remaining`.
- Private control: `_thread`, `_stop`.

`DemoRunner.stop()` sets `_stop`, leaves the Attendee bot if present, marks the
UI finished immediately, and persists status. The worker may still be cleaning
up browser/tunnel resources in the background.

`DemoRunner._run_live()` wires callbacks:

| Callback | Effect |
|---|---|
| `on_bot_joined` | Stores Attendee bot ID on the handle |
| `on_meeting_ready` | Sets `bot_in_meeting` and appends join-link transcript line |
| `on_leave_grace` | Updates the countdown exposed by the API |

The state sync loop persists handles periodically. Without Redis, this is still
local-process state and requires one worker.

## 7. Live Meeting, Audio, and Voice

### `run_live_meet_demo`

The main live implementation is `navigator/meeting/live_demo.py`.

Its high-level order is:

1. Validate live settings and site graph.
2. Resolve Client provider keys and persona.
3. Start the frame relay.
4. Start `AudioBridge`.
5. Start the audio cloudflared tunnel and create a public `wss://` URL.
6. Create Attendee bot with audio websocket settings.
7. Configure Zoom ZAK/Web SDK when the meeting is Zoom.
8. Wait for the Attendee state to become `joined`.
9. Start the screenshare tunnel after bot join.
10. Wait for the audio websocket. Current default maximum is 120 seconds because
    Zoom can delay the stream until the meeting host grants recording permission.
11. Wait for a human participant.
12. Start the 25-second leave-grace watcher.
13. Start Gemini Live and configure the speaker ownership.
14. Run intake and collect prospect context.
15. Launch a fresh Playwright browser context.
16. Login to the Client product when configured.
17. Arm screenshare and wait for the relay to become visible.
18. Select timeline, strict playlist, or LangGraph execution.
19. Continue through optional post-demo Q&A.
20. Leave the bot and tear down browser, tunnels, relay, audio, and LiveAgent.

### Attendee client

`navigator/meeting/attendee.py` is a small HTTP client. It owns no browser or
meeting business logic. It provides:

- `join`, `get`, `leave`, and `leave_if_active`.
- `send_chat`.
- `participant_events`.
- `wait_for_human_join`.
- `human_has_left`.
- `register_audio_hub` and `audio_stream`.
- Screenshare and voice-agent PATCH helpers.

Keep provider API details in this client. Do not spread raw Attendee HTTP calls
through agent nodes or React code.

`navigator/meeting/attendee_ws_patch.py` is an operational compatibility patch
for self-hosted Attendee. Attendee's mixed-audio WebSocket client must start
before the first mixed PCM chunk exists. `attendee_stack.py` applies the patch
to the local Attendee checkout and restarts `attendee-worker-local` when the
source changes. This prevents a circular startup dependency where Navigator
needs the socket to send bot audio, while Attendee waits for inbound audio
before opening the socket.

### AudioBridge

`navigator/meeting/audio_bridge.py` is a local WebSocket server:

```text
Attendee mixed PCM
  -> cloudflared public wss URL
  -> AudioBridge handler
  -> inbound Queue[bytes]
  -> STT or Gemini Live

Gemini Live PCM
  -> AudioBridge.push_outbound_pcm
  -> sender thread
  -> Attendee websocket
  -> meeting output
```

Important behavior:

- `start()` creates the listener and sender threads.
- `serve_forever()` is required; a listening socket without accepting work
  causes silent audio.
- `chunks_received` and `chunks_sent` are operational counters.
- `audio_s_sent` counts real playback duration, not queued duration.
- `flush_bot_output()` clears local pending audio and sends Attendee a clear
  command for barge-in.

When debugging silence, inspect these messages in logs:

```text
[live] audio websocket ready: ...
[audio] Attendee websocket connected ...
[audio] pcm chunks received=1 ...
[live] audio WS up ...
```

If Attendee reports a recording-permission denial, the stream may not connect
until the host grants permission. A 12-second wait was insufficient for that
case; the current default is 120 seconds. Self-hosted Attendee also receives
the eager-connect patch described above.

### Gemini Live ownership

`navigator/voice/live_agent.py` owns the realtime Gemini session:

- `_pump_in`: AudioBridge input to Gemini.
- `_pump_out`: Gemini responses to outbound PCM.
- `_pump_cmd`: director commands such as `say`, `nudge`, and context updates.
- Server-message handling: audio, transcripts, turn completion, interruption.
- `set_listen_only`: prevents the agent from answering during intake listen.
- `set_director_only`: suppresses autonomous answering during scripted playback.

`live_demo.py` coordinates the director and LiveAgent. The agent graph decides
what the product demonstration should do; Gemini Live is the conversational
audio transport and voice.

### STT and TTS

`navigator/voice/stt.py` wraps VAD and Groq Whisper. Live input normally arrives
through Gemini Live; fallback intake can consume `AudioBridge.inbound` through
the segmenter.

`navigator/voice/tts.py`, `meeting/meet_speaker.py`, and live-demo speaker
wrappers coordinate local output, Attendee audio, and chat fallback. Every
narration path should have one clear owner. Do not make both LangGraph speaking
and Gemini Live independently speak the same line.

## 8. Agent State Machine

The graph is built in `navigator/agent/graph.py` with LangGraph, but the nodes
are plain testable functions.

```text
joining -> introducing -> speaking
                             |
                             v
listening -> planning -> speaking <-> executing -> verifying
     ^                         |                         |
     |                         +-------------------------+
     |                                                   v
     +------------------------------------------ reflecting -> turn_done
                                                          |
                                             more turns -> listening
                                             finished    -> ending
```

### Node responsibilities

| Node | File | Responsibility |
|---|---|---|
| `joining` | `agent/nodes/joining.py` | Meeting/no-op join phase |
| `introducing` | `agent/nodes/introducing.py` | Persona and opening narration |
| `listening` | `agent/nodes/listening.py` | Input, STT, silence, corrections |
| `planning` | `agent/nodes/planning.py` | Choose named flow, answer, handoff, or correction |
| `speaking` | `agent/nodes/speaking.py` | The single narration/TTS owner |
| `executing` | `agent/nodes/executing.py` | Dispatch one typed browser tool |
| `verifying` | `agent/nodes/verifying.py` | Check postcondition and write action result |
| `reflecting` | `agent/nodes/reflecting.py` | Convert failures into pending correction rules |
| `ending` | `agent/nodes/ending.py` | Archive and terminal cleanup |

The central invariant is `executing -> verifying`. The next action must not be
planned from an unverified browser state.

The four allowed browser tools are:

- `click_element`.
- `fill_field`.
- `navigate`.
- `wait_for`.

Postconditions include visibility, hidden state, text, value, URL, and element
count. The model can choose a named flow, but it cannot invent a selector or
arbitrary browser command.

### Playback engines

Live demos can use more than one execution engine:

- Timeline playback when complete playlist metadata is available.
- Strict playlist playback when a playlist exists but timeline metadata is not
  complete.
- LangGraph execution for normal conversational or non-playlist runs.
- Gemini Live for realtime conversational audio ownership.

Selection is exposed by `live_demo.select_engine()`. Playback helpers live in
`navigator/agent/recorded_playback.py`.

## 9. Site Graph, Flows, and Publishing

### Site graph

`navigator/knowledge/site_graph.py` defines the typed graph. A graph contains
conceptually:

```yaml
site: client-product
base_url: https://product.example
pages:
  inbox:
    url: /inbox
    selectors:
      send_button: "[data-nav='send_button']"
    flows:
      send_test_message:
        steps: ...
demo_playlist: ...
persona: ...
```

The exact schema is validated by `parse_site_graph()`. Do not create a second
validator in a route, frontend, or SDK.

### Registry revisions

`navigator/app/registry.py` stores graph YAML as revisions:

```text
upload/edit -> draft revision
                 |
                 +--> dashboard test can run it
                 |
              Publish/activate
                 |
                 v
          public live demos use it
```

`latest_revision()` is for dashboard/test workflows. `published_revision()` is
for public live demos. `activate()` is the only path that makes a revision live.

### Manual recorder

The recorder in `navigator/automation/record.py` captures a human browser path
and converts it to a draft graph. It is a graph authoring tool, not a live demo
runner. Recorder output must be reviewed before publishing.

### Autonomous exploration

The explore runner in `navigator/automation/explore/` walks a product within a
budget and safety guardrails. It also produces a draft. The dashboard uses
`useExploreSession` and a ticketed read-only WebSocket for progress, questions,
frames, and safety decisions.

Exploration must not auto-publish. The Client reviews the generated flow in the
dashboard, approves mutating steps, and publishes deliberately.

## 10. Frontend Architecture

### Application shell

`client/web/src/App.tsx` is the dashboard shell, not a router library. It maps a
string tab ID to a panel component:

| Tab ID | Component |
|---|---|
| `overview` | `Overview` |
| `demo` | `LiveDemo` |
| `logs` | `Logs` |
| `flows` | `Flows` |
| `execution` | `Execution` |
| `graph` | `SiteGraph` from `Editors.tsx` |
| `knowledge` | `Knowledge` from `Editors.tsx` |
| `bio` | `Bio` from `Editors.tsx` |
| `settings` | `Settings` |
| `monitor` | `ResourceMonitor` |

The shell does the following:

1. Calls `api.checkAuth()`.
2. Renders `AuthScreen` when unauthenticated.
3. Loads preferences and may open `OnboardingWizard`.
4. Hydrates `useDemoSession` and `useExploreSession`.
5. Polls active demo state globally.
6. Renders `Sidebar`, header, theme button, active panel, toast, explore float,
   and onboarding modal.
7. Shows a persistent active-demo banner on every panel.

The dashboard does not use client-side route URLs for panel navigation. A button
usually calls `useUi().setTab("panel-id")`.

### Shared UI state

#### `useUi` in `store.ts`

Contains:

- `tab`: active panel ID.
- `toast`: success/error message.
- `logsSessionId`: which run Logs should expand.
- `ok`, `err`, and `clear` helpers.

`errText()` converts API and Pydantic validation errors into readable text.

#### `useDemoSession` in `demoSession.ts`

This is the canonical active-demo state. It survives panel unmounts so changing
tabs does not lose a running session.

It owns:

- `demo`.
- `starting` and `ending` flags.
- `hydrate()` after login/reload.
- `refreshActive()` polling.
- `start()` and `end()` mutations.

Statuses `starting` and `running` count as live. A missing demo during polling
is converted to a local finished state so the UI does not remain stuck.

#### `useProductData` in `productData.ts`

Stores shared flow/playlist data and an `epoch`. Panels call `invalidate()`
after saving graph, bio, knowledge, login, or settings so other panels refetch.

#### `useExploreSession` in `exploreSession.ts`

Stores the autonomous exploration session, scope filters, progress, questions,
screenshots, safety decisions, and elapsed time. Its WebSocket is read-only;
answers go through authenticated HTTP endpoints.

### Request flow in `api.ts`

Most API calls use one of these helpers:

```ts
const get = <T>(path: string) => request<T>(path);
const send = <T>(path: string, method: string, body?: unknown) => ...;
```

`request()`:

1. Adds the Bearer access token for protected routes.
2. Sends JSON and parses the response.
3. Throws `ApiError` for non-2xx responses.
4. On `401`, refreshes the access token once and retries.
5. Leaves public auth routes unmodified.

When adding an endpoint, define its response type near the top of `api.ts` and
add one named method to the `api` object. Do not duplicate `fetch` logic inside
panels.

## 11. Dashboard Buttons and What They Do

This section is the button-level map for a new frontend developer. The handler
is the place to start debugging; the API method shows the network boundary.

### Global shell buttons

| Visible control | File/handler | Effect |
|---|---|---|
| Sidebar tab buttons | `Sidebar.tsx`, `setTab(id)` | Switches the active panel |
| Resource Monitor & Health Check | `Sidebar.tsx`, `setTab("monitor")` | Opens monitor panel |
| Show setup guide | `Sidebar.tsx`, `showOnboardingCard` | Restores hidden setup card and opens wizard |
| Continue setup | `GetStartedCard` -> `openOnboarding` | Opens onboarding at the incomplete step |
| Log out | `App.tsx`, `signOut` | Ends active demo if necessary, logs out, resets product state |
| Sun/moon icon | `App.tsx`, `toggle` | Changes theme and persists `nav-theme` in localStorage |
| View Live demo | global banner, `setTab("demo")` | Switches to the Live demo panel |
| End demo | global banner, `endLive` | Calls `useDemoSession.end()` |
| Toast close | `Toast`, `clear` | Dismisses current notice |

### Authentication and onboarding

| Visible control | Handler | Effect |
|---|---|---|
| Log in | `AuthScreen`, `api.login` | Gets JWT and enters dashboard |
| Sign up | `AuthScreen`, `api.signup` | Creates account, enters dashboard, opens onboarding |
| Auth theme toggle | `AuthScreen` | Changes theme before login |
| Back | `OnboardingWizard` | Goes to prior wizard item |
| Skip | `OnboardingWizard` | Skips optional current item |
| Continue | `OnboardingWizard` | Saves current item and advances |
| Finish | `OnboardingWizard` | Completes setup and triggers success/confetti |
| Skip setup | `OnboardingWizard` | Closes setup without completing all items |

Onboarding saves product data through domain, bio, login, and knowledge API
methods. It is not a separate backend workflow; it is a guided sequence of
normal dashboard mutations.

### Overview

The Overview panel polls metrics, run history, and publish checklist.

| Control | Effect |
|---|---|
| Demo sessions KPI | Opens Logs |
| Actions KPI | Opens Logs |
| Step failures KPI | Opens Logs |
| Recent run row | Selects session and opens Logs |
| Pass rate, charts, checklist | Display-only |

### Live demo

`LiveDemo.tsx` combines product configuration with a test-demo launcher.

| Control | Handler/API | Effect |
|---|---|---|
| Product domain Save | `saveDomain` / `putProductDomain` | Stores base URL and invalidates product data |
| Autonomy option | `saveAutonomy` / `putAutonomyMode` | Stores guided/adaptive/explorer mode and refreshes readiness |
| Product login Save | `saveLogin` / `putProductLogin` | Stores encrypted login configuration |
| Change password | local state | Replaces masked password display with input |
| Show login during demo | `saveIncludeLogin` / `putProductLogin` | Includes login flow in default demo |
| Platform selector | local state | Chooses meeting provider for next test |
| Topic input | local state | Supplies meeting topic |
| Intake fields | local state | Prefills name, company, business type, and intent |
| Run a test demo | `start` / `POST /client/api/demos/start` | Starts `dashboard_test` live demo |
| Copy link | `copy` / clipboard API | Copies current meeting URL |
| End | `end` / `POST /client/api/demos/{id}/end` | Stops demo and leaves meeting |
| Auto-ending countdown | `demo.leave_grace_remaining` | Display-only server state; human leave triggers it |
| Open full log | `openLogs` | Stores session ID and switches to Logs |

Start is blocked when readiness has blocking failures, the domain is a
placeholder, or another demo is active. The meeting link can appear before the
bot is fully inside because static/admit workflows require the Client to open
and admit it.

### Logs

`Logs.tsx` shows persisted demo runs and expandable action/decision detail.

| Control | Effect |
|---|---|
| Run row | Expands/collapses the run |
| Action log tab | Shows ActionLog entries |
| Agent decisions tab | Shows decision traces |
| End on live run | Stops the active demo |
| Meeting label link | Opens the associated meeting URL |
| Session expansion | Polls events while active |

The panel refreshes the run list every few seconds. It treats missing event data
as an empty log for a run that has not produced events yet.

### Flows

Manual flow list controls are split into local edits and persisted actions.

| Control | Persistence | Effect |
|---|---|---|
| Add row | Local until save | Adds an editable flow row |
| Up/down row controls | Local until save | Changes playlist order |
| Edit flow fields | Local until save | Changes name/page/flow IDs |
| Delete flow | Immediate API | Confirms then calls `deleteFlow` |
| Save order | API | Calls `putFlows` with current playlist |
| Clear all | API after confirmation | Calls `clearAllFlows` |
| Start setup | API | Starts recorder setup |
| Start capturing this flow | API | Starts recorder capture |
| Stop recorder | API | Stops capture and merges result |
| Start exploring | API/WebSocket | Starts bounded autonomous exploration |
| Stop exploring | API | Requests explorer stop |
| Answer & resume | API | Sends answer to an exploration question |
| Skip this field | API | Skips an unknown business-specific field |
| Allow | API | Approves a flagged exploration control |
| Dismiss | API | Rejects a flagged exploration control |
| Clear completed result | Local/session state | Removes finished explore output |

The flow list is the Client's demo playlist. Recorder and explorer output are
drafts until reviewed and published.

### Execution

`Execution.tsx` handles exploration scope and pending mutating steps.

| Control | Effect |
|---|---|
| Include path add/remove | Saves local explorer scope filter |
| Exclude path add/remove | Saves local explorer scope filter |
| Exclude label add/remove | Saves local explorer scope filter |
| Approve for live demo | Updates pending approval in draft YAML |
| Drop step | Removes step and related metadata from draft YAML |

Approval is not publication. A Client must still publish the resulting graph.

### Site graph and demo script

The graph editor is in `Editors.tsx`; the demo script editor is
`components/DemoScriptPanel.tsx`.

| Control | API/effect |
|---|---|
| YAML editor | Local editor state |
| Fullscreen | Local layout state |
| Save draft | `putSiteGraph` |
| Publish | `publishSiteGraph` after confirmation |
| Clear all graph | `clearSiteGraph` after confirmation |
| Save script | `patchDemoScript` |
| Regenerate | `regenerateDemoScript` |
| Retry script load | Repeats `getDemoScript` |
| Expand flow section | Local display state |

Saving creates a draft revision. Publish activates a revision for public live
traffic.

### Knowledge and company bio

| Panel | Control | API/effect |
|---|---|---|
| Knowledge | Save | `putKnowledge`, then product invalidation |
| Company bio | Reset defaults | Local replacement only until Save |
| Company bio | Add field | Adds local field |
| Company bio | Delete field | Removes local field |
| Company bio | Save | `putBio` |

Knowledge is indexed by the backend after save. Bio fields become persona and
product context used by live demos.

### Settings

| Control | Handler/API | Effect |
|---|---|---|
| Agent name/tone/voice/language fields | `putAgentSettings` | Changes agent persona/audio settings |
| Save | `save` | Persists agent settings |
| Screenshare login toggle | `putProductLogin` | Enables/disables configured login in share flow |
| Gemini/Groq key fields | `putAgentProviderKeys` | Updates provider keys without exposing stored values |
| Save keys | API | Persists supplied nonblank keys |

### Resource Monitor

The monitor is diagnostic and currently has no mutating buttons. It polls
`GET /client/api/system/health` for CPU, memory, GPU, network, token usage,
service health, and process status.

## 12. Data and Persistence

### SQLite databases

The registry and application stores are SQLite-backed. Depending on settings,
files commonly include:

- Product and site graph registry.
- Dashboard auth and refresh sessions.
- Credential vault metadata.
- ActionLog rows.
- Demo-run metadata.
- Decision traces.
- Pending corrections.
- Token usage.

Use the store classes instead of issuing ad hoc SQL from routes or UI code.

### ActionLog

`navigator/logs/store.py` records:

- Session and demo identity.
- Product and origin.
- Tool name and input.
- Expected postcondition.
- Verification result.
- Failure details.
- Host/browser metadata.

`product_metrics()` deliberately excludes dashboard-test sessions from billable
usage. Keep this rule intact when adding metrics.

### Archives and generated output

Live/headless runs may create archives and local generated artifacts. Treat
archives, databases, `.env`, graphify output, build output, and service
credentials as environment artifacts, not source changes.

## 13. Testing Strategy

### Unit tests

Use unit tests for plain functions and boundaries:

- `test_graph.py`: graph structure and routing.
- `test_site_graph.py`: YAML validation and revisions.
- `test_tools.py`, `test_cursor.py`: browser-tool contracts.
- `test_recorded_playback.py`: playback behavior.
- `test_audio_bridge.py`: WebSocket inbound/outbound audio.
- `test_live_agent.py`: Gemini Live lifecycle with fakes.
- `test_stt.py`: VAD/STT helpers.
- `test_leave_grace.py`: 25-second leave state machine.

### API tests

`TestClient` tests override dependencies so they do not require real meetings.
Important suites include:

- `test_api.py`.
- `test_demos_start.py`.
- `test_client_dashboard.py`.
- `test_demo_origin_boundaries.py`.
- `test_session_tokens.py`.
- `test_registry.py`.
- `test_demo_readiness.py`.

### Runner tests

- `test_runner_stop.py`: immediate UI stop, bot leave, callback wiring.
- `test_runner_demo_runs.py`: persistence and final statuses.
- `test_runner_multi_worker.py`: optional state-store behavior.

### Integration boundaries

Tests that need Attendee, Docker, cloudflared, Zoom, Meet, or provider keys are
environment-sensitive. Mark or isolate them rather than making all unit tests
depend on a live meeting.

### Documentation checks

API/schema changes require:

```bash
.venv/bin/python -m navigator.docs build
.venv/bin/python -m navigator.docs check
```

Generated `fern/` and `docs/index.html` outputs should be regenerated, not
hand-edited.

## 14. Deployment and Operations

### Local Attendee

Navigator can use a self-hosted Attendee checkout. Operational helpers live in
`scripts/` and `navigator/meeting/attendee_stack.py`.

Typical Attendee commands:

```bash
./scripts/sync-attendee-compose.sh
cd ~/projects/attendee
docker compose -f dev.docker-compose.yaml -f local.docker-compose.yaml \
  --profile webpage-streamer up -d --build
```

### Ports

| Port | Service |
|---|---|
| `8000` | Navigator API and dashboard |
| `8001` | Attendee webpage streamer |
| `8002` | Attendee API/Django UI |
| `9000` | Optional MinIO object storage |

### Live meeting infrastructure

The live path may use two tunnels:

- Audio tunnel: raw WebSocket endpoint to `AudioBridge`.
- Screenshare tunnel: relay HTTP paths such as `/view`, `/agent`, and frames.

`meeting/tunnel.py` drains cloudflared output, waits for registration, retries
flaky quick tunnels, and verifies Docker DNS for fresh public hostnames.

### Startup log rule

When starting Navigator, inspect logs before saying the service is ready. Look
for:

```text
ERROR
Traceback
WARN
[runner]
[live]
[attendee]
[zoom]
```

The server should be started with one worker unless Redis state coordination is
explicitly configured.

## 15. Troubleshooting Playbook

### Dashboard shows no active demo after refresh

Check:

1. `useDemoSession.hydrate()` calls `GET /client/api/demos`.
2. The JWT refresh route succeeds.
3. The demo row exists in `demo_runs`.
4. The server process has not changed without shared state.
5. Uvicorn is running with `--workers 1`.

### Start button returns "Demo not ready"

The readiness endpoint reports blocking checks. Inspect:

- Product domain is not a placeholder.
- Site graph exists and validates.
- A flow/playlist is configured.
- Published revision exists for public demos.
- Attendee is reachable.
- Required provider credentials exist.
- Meeting provider configuration is valid.

Start with `GET /client/api/demo-readiness?origin=dashboard_test` and the
server log.

### Bot joins but narration is silent

Trace the boundary in order:

1. Does `live_demo.py` print an audio websocket URL?
2. Does Attendee receive `websocket_settings.audio`?
3. Did the meeting host grant recording permission?
4. Does Navigator print `Attendee websocket connected`?
5. Does `chunks_received` become nonzero?
6. Does Gemini Live start and receive inbound frames?
7. Does `chunks_sent`/`audio_s_sent` increase?
8. Is a second TTS owner draining or flushing the output?

Attendee recording permission may be delayed. The current audio wait default is
120 seconds. A missing `[audio] Attendee websocket connected` line means the
failure is before Gemini and should be debugged as a tunnel/Attendee/config
problem.

### Human leaves the meeting

`_start_human_leave_watcher()` polls participant events. Behavior:

```text
human present -> leave_grace_remaining = null
human leaves  -> 25
each second   -> 24 ... 1 ... 0
human rejoins -> null, countdown cancelled
at zero       -> stop event + Attendee leave
```

The backend exposes the transient value on `DemoView`. `LiveDemo.tsx` and the
global App banner render it beside the End control.

### Screenshare is blank

Check:

- Relay started before the screenshare tunnel.
- cloudflared process is alive.
- `/view` responds locally and publicly.
- Attendee webpage-streamer is running on port `8001`.
- Docker can resolve the newly created tunnel hostname.
- `arm_screenshare()` was called after bot join.

### Static Meet waits forever

Static non-open-access meetings are an admit-flow by design. Open the meeting
as the host and admit Navigator. Public embeds reject this configuration before
starting because an End User cannot admit the bot.

### UI button appears to do nothing

Debug in this order:

1. Find the JSX control in the panel.
2. Find its handler in the same file.
3. Find the `api.*` method called by the handler.
4. Inspect the browser Network request.
5. Inspect FastAPI route and server log.
6. Check whether the panel calls `invalidate()` or updates its local state.
7. Check the toast for an `ApiError` or validation response.

Many editor buttons intentionally make local changes until an explicit Save.
Do not assume that changing an input sent a request.

## 16. Change Recipes

### Add a dashboard setting

1. Add typed backend storage/validation in `Registry`, vault, or settings owner.
2. Add authenticated GET/PUT route in `main.py`.
3. Add Pydantic response/request models if needed.
4. Add TypeScript type and `api` method.
5. Add state/loading/error behavior in the relevant panel.
6. Add success/error toast.
7. Invalidate `useProductData` when other panels depend on it.
8. Add API and UI-adjacent tests.
9. Regenerate API docs.

### Add a new graph action

1. Extend the typed schema in `navigator/core/schemas.py` or site-graph model.
2. Implement the browser operation in `navigator/automation/browser/`.
3. Add postcondition handling and ActionLog output.
4. Add planner/serialization support.
5. Add playback support if recorded/timeline flows can use it.
6. Add focused unit tests and graph validation tests.
7. Do not expose arbitrary browser execution just to make the feature work.

### Change live meeting behavior

Start at `navigator/meeting/live_demo.py`, then trace:

```text
main._run_live_demo
  -> DemoRunner.start_live
  -> DemoRunner._run_live
  -> run_live_meet_demo
  -> AttendeeClient / AudioBridge / relay / LiveAgent
```

Add a fake-based test where possible. Keep real provider calls behind the
Attendee/meeting client boundary.

### Change a button

Use this checklist:

1. Update the visible JSX and accessibility label.
2. Keep the event handler near the component.
3. Call an existing `api` method or add one centrally.
4. Set loading/disabled state to prevent duplicate requests.
5. Show success/error feedback through `useUi`.
6. Update shared stores or invalidate product data.
7. Add or update a component/API test if the behavior is important.

## 17. SDK and Documentation Pipeline

The SDK under `sdk/` lets a Client author graph declarations and verify them in
their own CI:

```bash
cd sdk
npm install
npm run build
npm test
npx navigator compile
npx navigator push
npx navigator verify
```

The docs generator derives API documentation from live FastAPI models and
schemas. The generated outputs include HTML and Fern files. Run the generator
after adding routes, fields, postconditions, or public behavior.

## 18. Useful Source Navigation Shortcuts

| Question | Start here |
|---|---|
| Why did this API request fail? | `client/web/src/lib/api.ts`, then `app/main.py` |
| Why is the dashboard stuck on a demo? | `demoSession.ts`, `runner.py`, `main.py` |
| Why did the bot not join? | `live_demo.py`, `attendee.py`, `attendee_stack.py` |
| Why is audio silent? | `audio_bridge.py`, `live_agent.py`, `tunnel.py`, Attendee logs |
| Why did a browser action fail? | `executing.py`, `verifying.py`, `site_graph.py`, ActionLog |
| Why did a public demo use the wrong graph? | `main._run_live_demo`, `Registry.published_revision` |
| Why did a draft change live behavior? | Check origin and revision resolution |
| Why did a save not affect another panel? | `productData.invalidate()` and panel fetch effects |
| Why did a button not persist? | Check whether it is local-until-save |
| Why are docs stale? | Run `navigator.docs build` and `navigator.docs check` |

## 19. Glossary

| Term | Meaning |
|---|---|
| Site graph | Typed Client product description with pages, aliases, flows, and checks |
| Page ID | Named graph page, such as `inbox` |
| Flow ID | Named sequence of allowed actions on a page |
| Postcondition | DOM fact expected after an action |
| Demo playlist | Ordered set of flows for a scripted walkthrough |
| Draft revision | Editable graph not used by public live demos |
| Published revision | Revision selected for public live demos |
| `dashboard_test` | Non-billable Client validation demo |
| `public_embed` | Billable End User demo |
| Attendee | Meeting bot service used to join calls and stream media |
| AudioBridge | Navigator local WebSocket server for PCM in/out |
| Relay | Local HTTP service that exposes Playwright frames for screenshare |
| LiveAgent | Gemini Live audio session |
| ActionLog | SQLite audit record for actions and verification |
| Decision trace | Persisted planner branch and reasoning metadata |
| Pending correction | Failed behavior awaiting human approval |
| DemoRunner | In-process lifecycle manager for demo worker threads |

## 20. Before Opening a Pull Request

- Read `docs/PRODUCT_MODEL.md` for auth, origin, revision, and billing rules.
- Add or update tests for changed behavior.
- Run focused tests first, then the broader suite.
- Run `npm run typecheck` and `npm run build` for frontend changes.
- Run `navigator.docs build` and `navigator.docs check` for API changes.
- Inspect `git diff` and `git status`.
- Do not include `.env`, secrets, databases, `graphify-out/`, or generated local
  artifacts unless the repository explicitly tracks them.
- Check that public embed code still uses `sess_`, not `nav_`.
- Check that public demos still use published revisions.
- Check that all tenant data remains scoped by `product_id`.
