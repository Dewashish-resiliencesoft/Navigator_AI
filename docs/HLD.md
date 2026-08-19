# Navigator AI — High-Level Design

## 1. Purpose and current product

Navigator AI is a multi-tenant demo-as-a-service platform operated by Resiliencesoft. A Client company registers its product, supplies credentials and a site graph, and embeds a small “Start a demo” control on its own landing page. An End User clicks that control and receives a live AI-run demonstration of the Client’s real product. Navigator is infrastructure: the visitor should see the Client’s product and meeting experience, not a Platform-branded consumer surface.

This document describes the implementation present in the repository. Statements marked **Planned / Not Yet Implemented** describe the forward-looking autonomy/readiness roadmap, not shipped behavior.

## 2. Roles and trust boundaries

```mermaid
flowchart LR
    P[Platform\nResiliencesoft] -->|operates| API[Navigator FastAPI]
    C[Client] -->|dashboard JWT| DASH[Operator console\n/client]
    C -->|server-side nav_ key| API
    C -->|server mints scoped sess_ token| EMBED[Client landing page\nembed SDK]
    EU[End User] -->|clicks exactly “Start a demo”| EMBED
    EMBED -->|single-use sess_ token| API
    API -->|live meeting/demo| EU
    API -->|draft editing, testing, publishing| DASH
```

- **Platform** builds and operates the service. It is not a tenant.
- **Client** is the tenant, scoped by credential-derived `product_id`. It owns the site graph, flows, knowledge, credentials, and publish decision.
- **End User** never authenticates and never reaches the dashboard, editor, site graph, or corrections queue.

## 3. System architecture

```mermaid
flowchart TB
    subgraph ClientSurfaces[Client-owned surfaces]
        LAND[Product landing page]
        SDK[TypeScript/public embed script]
        CONSOLE[Vite + React operator console]
    end

    subgraph Navigator[Navigator AI]
        API[FastAPI backend\nroute/auth boundary]
        REG[Registry\nproducts + graph revisions]
        RUN[DemoRunner\nthread + browser isolation]
        ENGINES[Demo engine layer\ngemini_live | timeline | strict_playlist | langgraph]
        EXP[Autonomous explorer\nPlaywright + guardrails]
        KNOW[Knowledge + retrieval\nsite graph, Chroma, Product Map]
        LOG[SQLite ActionLog\ndecisions + demo runs]
        REDIS[(Redis\ndemo state + pub/sub)]
        ATT[Attendee adapter\nMeet / Zoom bot]
        RELAY[Relay + cloudflared\naudio / screenshare / avatar pages]
    end

    subgraph Providers[External providers]
        GROQ[Groq\nLlama 3.3 70B + Whisper]
        OPENAI[OpenAI\nGPT-4o-mini reflection\nGPT-4o vision fallback]
        GEMINI[Gemini Vision / Live]
        TTS[Fish Audio Sarah / Piper]
        MEET[Google Meet]
        ZOOM[Zoom]
    end

    LAND --> SDK --> API
    CONSOLE --> API
    API --> REG
    API --> RUN
    API --> EXP
    API --> KNOW
    RUN --> REDIS
    RUN --> LOG
    RUN --> ENGINES
    ENGINES --> RELAY
    ENGINES --> ATT
    EXP --> KNOW
    EXP --> LOG
    ENGINES --> GROQ
    ENGINES --> OPENAI
    ENGINES --> GEMINI
    ENGINES --> TTS
    ATT --> MEET
    ATT --> ZOOM
    RELAY --> ATT
```

| Component | Current responsibility |
|---|---|
| FastAPI backend | Tenant registration, auth boundaries, graph revisions, demos, recorder/explorer, corrections, metrics, and operator APIs. |
| Operator console | Client dashboard for onboarding, site graph/flow editing, recording, autonomous exploration, test demos, readiness, logs, and settings. |
| Embed script and SDK | Server-minted `sess_` token flow for public demos; TypeScript DSL/CLI for authoring and pushing graphs. |
| Registry | SQLite product registry and immutable site-graph revisions; `latest_revision()` for dashboard/test and `published_revision()` for live. |
| DemoRunner | Per-demo Playwright context, thread, `CallDeps`, lifecycle handle, run persistence, and optional Redis synchronization. |
| Demo engines | Choose the appropriate execution strategy for conversational, narrated-timeline, strict scripted, or live-agent paths. |
| Autonomous explorer | Bounded browser exploration using click/fill/navigate/wait tools, field classification, guardrails, semantic labeling, repair, episodes, and draft-flow generation. |
| Knowledge/memory | Site graph validation, product brief/bio, flow semantics, Chroma collections, pending corrections, and Product Map persistence. |
| Attendee and relay | Joins meetings, transports mixed audio, publishes screenshare/relay pages, and supports Meet/Zoom-specific behavior. |

## 4. Demo modes

`origin` is explicit and assigned at the authentication boundary. It is never read from the request body and is immutable after the run row is created.

| Mode | Trigger/auth | Revision | Usage |
|---|---|---|---|
| Test demo | Client dashboard, JWT; also server-side `nav_` verify route | Latest revision, including a draft | Non-billable |
| Live demo | End User public embed, single-use `sess_` token; server-side `nav_` public start is also supported | Published revision only | Billable |

### Test demo flow

```mermaid
sequenceDiagram
    actor C as Client
    participant D as Operator console
    participant A as FastAPI
    participant R as Registry
    participant DR as DemoRunner
    participant E as Engine layer
    participant B as Playwright browser
    participant L as ActionLog

    C->>D: Run a test demo
    D->>A: POST /client/api/demos/start (Bearer JWT)
    A->>A: Resolve product_id and origin=dashboard_test
    A->>R: latest_revision(product_id)
    R-->>A: Draft or latest graph revision
    A->>DR: Start isolated test handle
    DR->>E: Select/execute engine
    E->>B: Navigate and run flow
    E->>L: Append actions, verification, decisions
    DR-->>D: 202 DemoView + status polling
```

### Live demo flow

```mermaid
sequenceDiagram
    actor EU as End User
    participant W as Client landing page/embed
    participant A as FastAPI
    participant T as Session-token validator
    participant R as Registry
    participant M as Meeting provider
    participant AT as Attendee
    participant DR as DemoRunner
    participant E as Live engine

    EU->>W: Click Start a demo
    W->>A: POST /v1/demos/start with sess_ token
    A->>T: Redeem single-use token
    T-->>A: Credential-scoped product_id
    A->>A: Set origin=public_embed
    A->>R: published_revision(product_id)
    R-->>A: Active graph or failure if none published
    A->>M: Create Meet/Zoom meeting
    M-->>A: Meeting URL
    A->>AT: Join bot, configure audio/screenshare
    AT-->>A: Bot joined/ready
    A->>DR: Start live runner pinned to revision
    DR->>E: Run selected live engine
    E-->>AT: Narration/audio/screenshare
    AT-->>EU: Meeting experience
```

## 5. Engine layer

The engines coexist because Clients have different authoring/runtime maturity and live-demo needs:

| Engine | Why it exists | Current use |
|---|---|---|
| `gemini_live` | Low-latency live voice agent that can listen, reason, narrate, and respond to End User questions. | Preferred when a Gemini Live agent is available. |
| `timeline` | Replays recorded narration and timed cursor actions with smooth pacing. | Used when playlist metadata has narration/timing/click data. |
| `strict_playlist` | Deterministic YAML replay for incomplete playlist metadata. | Used for playlist demos when timeline metadata is incomplete and no live agent is active. |
| `langgraph` | Explicit orchestration for planning, narration, browser execution, verification, reflection, and conversational turns. | Used when there is no Gemini Live agent and either no usable playlist or conversational mode is selected. |

Runtime selection reports `gemini_live`, `timeline`, `strict_playlist`, `langgraph_conversational`, or `langgraph`. The last two are LangGraph variants rather than separate orchestration frameworks.

## 6. Autonomy and self-healing

The implemented autonomous path accepts a product URL and stored credentials, explores with bounded budgets, refuses risky actions through keyword plus LLM guardrails, pauses on business-specific fields, and merges generated `RecordedStep` values into an unpublished draft through the same merge path as manual recording.

```mermaid
flowchart LR
    FAIL[Failed browser step] --> DIAG[Diagnose\nnot found, nav stalled, login, timeout, etc.]
    DIAG --> REPAIR[Bounded repair ladder\nalternate selector / tactic]
    REPAIR -->|success| EPISODE[Episode attempt + repair outcome]
    REPAIR -->|exhausted| EPISODE
    EPISODE --> LEARN[Draft client-scoped corrective rule]
    LEARN --> PENDING[Pending correction review\nnot auto-promoted to Chroma]
    PENDING --> NEXT[Later exploration/live retrieval\ncan avoid repeated mistakes after approval]
```

Exploration episodes are durable JSONL/JSON artifacts with capped screenshots and retention. Live-call failures are persisted through ActionLog and reflection/correction storage. Learning is review-gated: exploration learning writes pending rules rather than silently changing the trained correction collection.

## 7. Technology choices

| Component | Technology | Why chosen |
|---|---|---|
| HTTP/API | FastAPI + Pydantic | Typed contracts, dependency-based auth, REST and WebSocket support. |
| Dashboard | Vite + React + TypeScript | Fast local operator console with typed API calls and focused panels. |
| Browser automation | Playwright Chromium | Real product interaction, isolated browser contexts, DOM verification, screenshots, and CDP screencast. |
| Orchestration | LangGraph | Explicit state transitions and testable node boundaries for voice/browser turns. |
| Live reasoning | Groq Llama 3.3 70B | Low-latency conversational planning and response generation. |
| Reflection | OpenAI GPT-4o-mini | Cost-conscious post-failure correction drafting. |
| Vision fallback | OpenAI GPT-4o and Gemini Vision | Visual interpretation and turn-brain/interrupt handling when DOM reasoning is insufficient. |
| Speech-to-text | Groq Whisper | Fast transcription of mixed meeting audio. |
| Text-to-speech | Fish Audio Sarah / Piper | Human-sounding primary voice with local/alternative fallback. |
| Meeting bot | Self-hosted Attendee | Meet/Zoom joining, audio transport, and screenshare integration. |
| Vector memory | ChromaDB | Product-scoped semantic retrieval for knowledge and approved corrections. |
| Shared demo state | Redis hashes, TTLs, and pub/sub | Cross-worker visibility and stop propagation. |
| Durable relational data | SQLite/WAL | Simple append/read workloads for registry, logs, runs, users, and pending corrections. |
| Public authoring | TypeScript SDK + Fern schema | Typed DSL and CLI now; generated Fern client remains stubbed. |

## 8. Tenancy and scaling

The primary tenant key is `product_id`, derived from the credential. Route paths and request bodies cannot select another tenant. Registry, ActionLog, Chroma collection names, credentials, flows, episode paths, and dashboard dependencies all scope reads by that key.

Each demo owns its Playwright browser context, `CallDeps`, thread, and stop event. The current runner maintains a local `_demos` dictionary for fast polling. With Redis configured, it also serializes handles under per-product hashes and per-demo keys with 24-hour TTLs, records a worker owner, and uses worker-specific pub/sub channels for remote stop requests. Other workers can read Redis state and merge it with their local handles.

This removes the single-process visibility bottleneck for reads and stop control, but it is not a fully distributed job scheduler: the browser and execution thread remain owned by one worker, and the local cache remains part of the implementation. SQLite is also the current durable store; PostgreSQL is a future deployment consideration noted in code comments.

## 9. Integrations and failure behavior

| Dependency | Responsibility | Current failure behavior |
|---|---|---|
| Groq Llama / Whisper | Live reasoning, reflection helpers, recording transcription, STT | Provider/key failures use configured fallback paths where available; missing keys can disable optional narration/STT. |
| Gemini Live / Vision | Live voice agent and visual turn-brain | Gemini Live availability is detected; runtime can fall back to other engines. Exact synchronization parity with LangGraph remains open. |
| OpenAI GPT-4o-mini / GPT-4o | Reflection and vision fallback | Reflection is fail-soft; visual fallback is optional and guarded. |
| Fish Audio / Piper | Spoken output | Fish Audio is primary; Piper is the local/alternative TTS path. |
| Attendee | Meeting bot, audio WebSocket, screenshare, avatar/camera tile | Live start fails early if Attendee is unreachable. Bot readiness and screenshare reachability are checked; Zoom can still be unreliable. |
| Google Meet / Zoom | End-user meeting surface | Provider-specific creation/join/authentication errors prevent a live session from starting. |
| cloudflared | Makes local audio/screenshare relay reachable by Attendee | Tunnel/DNS readiness is probed and retried; screenshare may continue after timeout with a warning. |
| ChromaDB | Product-scoped semantic memory | Retrieval is optional/fail-soft in several paths; collection names enforce tenant separation. |
| Redis | Shared state and stop pub/sub | Without Redis the runner falls back to process-local state; multi-worker coordination is not equivalent. |

## 10. Non-functional considerations

### Voice latency

The implementation favors short asynchronous seams: mixed meeting audio enters the bridge, STT produces text, the live agent decides, and TTS/audio is returned through Attendee. The repository does not define a formal end-to-end latency SLO. A practical live budget should be measured separately for audio capture, STT, model turn, TTS, Attendee transport, and browser action latency; current code contains timing diagnostics but no single contractual number.

### Security

- Dashboard routes require a JWT bound to a tenant product.
- Public browsers receive only single-use, short-lived `sess_` tokens.
- `nav_` keys are intended for server-side use and are hashed in the registry.
- `product_id` is credential-derived.
- Public live demos use published revisions only.
- The client-side embedding design is still incomplete: the current minimal script accepts a token in a data attribute, while safe server-side session-token minting and deployment/CORS policy require product integration. Never put a `nav_` key in browser HTML or JavaScript.

### Current limits

- Browser/meeting workers are still tied to the process that owns them.
- SQLite is not the intended high-concurrency durable store.
- Redis is optional and the local cache remains active.
- Attendee, Zoom, tunnel, and external LLM availability directly affect live readiness.
- The 3D avatar asset/relay exists, but it is not yet appearing reliably in the Meet video feed.

## 11. Planned architecture — Planned / Not Yet Implemented

The larger autonomy roadmap builds on the shipped semantic labeling and retrieval work:

1. **Phase B1 — flow auto-segmentation:** split long exploration traces into coherent demo flows automatically, with Client review before activation.
2. **Phase B2 — episode history read-back:** feed prior episode outcomes and successful repair tactics directly into later exploration decisions.
3. **Phase B3 — automatic Product Map:** derive and maintain a product-area map from explored flow semantics, with retrieval and dashboard review. Product Map storage/helpers exist, but the complete automatic workflow is not treated as shipped here.
4. **Phase C — guarded VLM selector repair:** use visual selector repair only as a bounded fallback after DOM/selector tactics fail; preserve destructive-action guardrails and human review.
5. **Phase D — readiness verdicts:** score each flow as `ready`, `needs_review`, or `broken`, with financial/destructive flows never eligible for `ready`. Publishing remains an explicit human action.

These phases must preserve the existing origin, revision, tenant, billing, and role boundaries. No autonomous flow may become live merely because it was generated or repaired.

## 12. Known open questions

- Should the Gemini Live engine be audited for the same `SPEAKING`/`EXECUTING` desynchronization recently addressed in LangGraph?
- What production-safe pattern will mint embed sessions without exposing a long-lived API credential or creating an uncontrolled cross-origin token surface?
- Can Attendee’s Zoom screenshare path be made reliable enough for a live SLO?
- How should the 3D avatar be attached so it appears in the Meet video feed rather than only in a local/relay page?
- When should Redis become mandatory, and what durable job/ownership mechanism should replace process-owned browser threads at larger scale?
- Which current tests should be updated as stale fixtures/specs, and which indicate real regressions after Attendee readiness is mocked correctly?
