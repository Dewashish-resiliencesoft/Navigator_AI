# Navigator AI — Product Model

The standing reference for what Navigator AI is, who its three roles are, and
which rules the code must never break. **Read this file before starting any work
in this repository.** Every rule here is enforced somewhere in the code; where it
is, the module is named.

---

## 1. What Navigator AI is

Navigator AI is an API + SDK built by **Resiliencesoft**. A company buys it,
embeds it on their own landing page, and their visitors get a live, AI-run demo
of *that company's* product — an agent that actually drives the product in a real
browser and narrates it in a meeting.

Navigator AI is **infrastructure, not a consumer brand**. The visitor never sees
the words "Navigator AI". They see the Client's landing page and a button.

---

## 2. The three roles

| Role | Who | Sees | Credential |
|---|---|---|---|
| **Platform** | Resiliencesoft | everything | — |
| **Client** | the company that buys Navigator (e.g. Edureka) | dashboard, flow editor, site graph, corrections, usage | dashboard JWT, or `nav_` API key server-side |
| **End User** | a visitor on the Client's landing page | one button, then a live demo | scoped `sess_` embed session token |

### Platform (Resiliencesoft)

Builds and operates Navigator: the FastAPI app (`navigator/app/main.py`), the
LangGraph agent (`navigator/agent/`), the meeting infrastructure
(`navigator/meeting/`), the voice pipeline (`navigator/voice/`). Not a tenant.
Has no role in the request path.

### Client

A tenant, scoped entirely by `product_id`. Registered in
`navigator/app/registry.py` (`Registry.register`). Owns:

- the **site graph** — their product's pages, selectors, and flows
- the **flows** the agent can run
- the **knowledge base**
- their **credentials** (`nav_` API key, dashboard login)

Logs into the dashboard (`navigator/client/web/`, served at `/client`) with a
JWT. Embeds the SDK on their own landing page.

### End User

A visitor on the Client's landing page. **Never authenticates.** Never sees the
dashboard, flow editor, site graph, or corrections queue. Sees exactly one
thing: a button labelled **"Start a demo"**, and then a live demo.

---

## 3. Test demo vs live demo

This is a **first-class explicit field**, not something inferred at read time.

| | Test demo | Live demo |
|---|---|---|
| `origin` | `dashboard_test` | `public_embed` |
| Triggered by | Client, from the dashboard | End User, on the Client's landing page |
| Auth | dashboard JWT | `sess_` embed session token (or `nav_` key server-side) |
| Purpose | validate a flow or site graph before publishing | the actual product |
| Counts toward usage / billing | **no** | **yes** |
| Site graph used | may be an unpublished **draft** | **published revision only** |
| Visible to | Client only | End User only |

### Where `origin` lives

- `navigator/app/runner.py` — `DemoOrigin` type; `DemoHandle.origin` is a
  required field with no default; `DemoRunner.start()` and
  `DemoRunner.start_live()` both take `origin` as a required keyword.
- `navigator/logs/store.py` — `demo_runs.origin` column; `upsert_run(origin=...)`
  is required. `origin` is deliberately **absent from the `ON CONFLICT DO UPDATE`
  clause**: a later status upsert must never be able to reclassify a billable
  live run as a test.
- `navigator/app/main.py` — `origin` is set **at the auth boundary, from the
  credential type**, never from a request body:
  - `POST /v1/demos/start` (`sess_` token or `nav_` key) → `public_embed`
  - `POST /client/api/demos/start` (dashboard JWT) → `dashboard_test`
  - `POST /v1/demos` (`nav_` key, headless — this is `navigator verify`) →
    `dashboard_test`

  Both public and dashboard routes funnel into one helper, `_run_live_demo(...,
  origin=...)`, so the two paths cannot drift apart.

**A dashboard JWT is not accepted on the public live route.** A Client's own
test run must go through the dashboard route so it is never counted as live
traffic.

---

## 4. Drafts and publishing

A Client editing their site graph must not change what live visitors are seeing
mid-edit. So a site graph revision has two states.

- **Draft** — stored, versioned, editable, and runnable in a *test* demo. Not
  visible to End Users.
- **Published** — the one revision live demos run.

Enforced in `navigator/app/registry.py`:

- `SiteGraphRevision.published`
- `put_site_graph(..., publish: bool)` — `publish` is **required, no default**,
  so no call site can silently go live by accident. `PUT /v1/products/site-graph`
  and `PUT /client/api/site-graph` both default it to `False`.
- `latest_revision(product_id)` — newest revision, draft or not. **What the
  dashboard reads and edits, and what a test demo runs.**
- `published_revision(product_id)` — the active revision. **What a live demo
  runs.** Raises `ProductNotFound` if nothing is published; a product with only
  drafts serves no live demos.
- `activate(product_id, revision)` — the only way a revision becomes visible to
  End Users. Also the rollback path. Exposed as
  `POST /client/api/site-graph/publish` and
  `POST /v1/products/site-graph/activate`.

A rejected upload is validated *before* anything is written, so a bad push can
never break a running live demo.

### Two ways to create a flow, one way to activate it

A Client can produce a flow two ways. They differ only in who does the walking.

| | Manual recording | Autonomous exploration |
|---|---|---|
| Trigger | "Record a flow" — `POST /client/api/record/start` | "Auto-Explore & Generate Flow" — `POST /client/api/explore/start` |
| Who drives | A human clicks through the product | The explorer picks actions itself (`navigator/automation/explore/`) |
| Actions used | Playwright, captured from real clicks | The same four tools — `click_element`, `fill_field`, `navigate`, `wait_for` |
| Output | `list[RecordedStep]` | `list[RecordedStep]` — the identical type |
| Merge | `merge_recorded_flow()` | `merge_recorded_flow()` — the identical function |
| Stored as | `put_site_graph(..., "recorded", publish=False)` | `put_site_graph(..., "explored", publish=False)` |

Both land as an **unpublished draft**. Both are reviewed and edited in the same
dashboard UI, and both become live only through `activate()` /
`POST /client/api/site-graph/publish`. The `source` column records provenance
for audit; it grants no extra privilege. **Nothing produced by autonomous
exploration is ever auto-activated.**

Exploration is bounded and fail-closed:

- **Bounded** by `ExplorationBudget` — max pages, max steps, wall clock, and an
  early stop when a full pass finds no unvisited interactive element. Page
  states are identified by URL *path plus a DOM structure hash*, never URL
  alone, so an SPA that changes state without changing the URL is tracked, and a
  paginated list that links back to itself terminates.
- **Fail-closed on mutation.** Before dispatching anything,
  `guardrail.classify_action()` runs a keyword heuristic *and* an LLM judgment
  pass on the element about to be touched. A hit, a missing judge, an
  unreachable judge, or an unparseable answer all mean *do not execute*; the
  action goes on a "skipped — needs your review" list instead. This check lives
  in the executor (`explore/explorer.py::_step`), not in the reasoning prompt, so
  a model that suggests a destructive action still cannot perform one.
- **Fail-closed on unknown data.** A form field is auto-filled only when a
  generic placeholder is provably harmless (a name, an email, a date). Anything
  business-specific pauses the run and asks the Client over the live exploration
  channel; the run resumes when they answer, and skips the field if they don't.

Exploration failures are written to `ActionLog` with the same schema and the same
`product_id` scoping as live-call failures, so the reflection pass and the
corrections queue treat them identically and a later run can retrieve what an
earlier one learned.

---

## 5. Usage and billing

`ActionLog.product_metrics()` in `navigator/logs/store.py` counts **live demos
only**. A Client running test demos from their own dashboard must not move their
own usage numbers.

Implemented as a subtraction, not a selection:

```sql
AND session_id NOT IN (
  SELECT session_id FROM demo_runs WHERE product_id = ? AND origin = 'dashboard_test'
)
```

Subtracting test sessions rather than selecting live ones means a run row that
failed to persist still bills, instead of a bookkeeping failure silently erasing
a Client's usage. Test runs are returned separately as `test_sessions` so the
dashboard can show them, clearly labelled as non-billable.

---

## 6. Auth boundaries

Three credentials, three surfaces. `product_id` is **always** derived from the
credential — never from a path segment or request body.

| Credential | Prefix | Who holds it | Surface |
|---|---|---|---|
| API key | `nav_` | Client, **server-side only** | `/v1/*` |
| Embed session token | `sess_` | End User's browser, single-use, short-lived | `POST /v1/demos/start` |
| Dashboard JWT | Bearer + refresh cookie | Client, in the dashboard | `/client/api/*` |

Rules:

- **Never** expose a `nav_` key in client-side HTML or JS. The public embed
  mints a `sess_` token server-side (`POST /v1/session-tokens`) and uses only
  that. See `navigator/client/embed/README.md`.
- The dashboard is **loopback-only** and must never be reachable from, or
  embeddable on, a public landing page.
- Every `/client/api/*` route takes `DashboardAuthedProduct`. There are no
  unauthenticated config or recorder routes — the recorder
  (`/client/api/record/*`) and the explorer (`/client/api/explore/*`) both drive
  a browser and write to the site graph, so both are dashboard-JWT-only.
- The one exception in *form* is `WS /client/api/explore/ws`, because a browser
  WebSocket cannot send an `Authorization` header. It is not an exception in
  *substance*: the dashboard exchanges its JWT on the authed
  `POST /client/api/explore/ticket` for a single-use 60-second ticket, and the
  socket is read-only. Answers to the explorer's questions go back over the
  authed `POST /client/api/explore/answer`, never over the socket, so a redeemed
  ticket can never be used to inject a field value.

---

## 7. UI copy rules

- The public embed renders exactly one control, labelled exactly
  **"Start a demo"** (`navigator/client/embed/navigator.js`). Not "Show Demo",
  not "Navigator AI Demo", not any Platform-branded label. The End User is being
  offered a demo of the *Client's* product.
- The dashboard's own start button reads as a **test**: "Run a test demo"
  (`navigator/client/web/src/panels/LiveDemo.tsx`), because that is what it is.
- The site graph editor says **"Save draft"** and **"Publish"**, and shows
  whether the revision on screen is live or a draft
  (`navigator/client/web/src/panels/Editors.tsx`).
- Dashboard metrics label the billable number as visitor sessions and show test
  sessions separately.

---

## 8. Tenant neutrality

Navigator core (`navigator/app`, `navigator/agent`, `navigator/meeting`,
`navigator/voice`, shared UI) must contain **no hardcoded ResilioHub or
Resiliencesoft names, URLs, copy, credentials, or flows**. Sample tenants live
only in `config/`, fixtures, docs, and examples. Anything a specific customer
needs belongs in their site graph, not in the code.

---

## 9. Invariants — the short list

1. `origin` is explicit, set from the credential type at the auth boundary, and
   immutable once written.
2. A live demo runs the **published** revision, or fails. Never a draft.
3. A test demo may run a draft. That is its entire point.
4. Test demos never count toward usage or billing.
5. `product_id` comes from the credential, never from the request.
6. An End User never authenticates and never reaches a Client surface.
7. A Client dashboard surface is never reachable from the public embed path.
8. The public embed button says exactly "Start a demo".
9. Manual recording and autonomous exploration both produce unpublished drafts
   and converge on the same review-and-activate gate. Nothing explored is ever
   auto-activated, and the exploration guardrail is enforced in the executor,
   not in a prompt.
