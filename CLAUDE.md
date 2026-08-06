# Navigator AI — agent instructions

## Read this first, every task

**[`docs/PRODUCT_MODEL.md`](docs/PRODUCT_MODEL.md) is the standing reference for
this repository.** Read it before touching code. It defines the three roles, the
test-vs-live demo split, the draft/publish model, the auth boundaries, and the
UI copy rules. Everything below is a summary — the doc is authoritative.

## Non-negotiable invariants

1. **`origin` is explicit.** Every demo carries `origin: "dashboard_test" |
   "public_embed"`, set at the auth boundary from the credential type. Never
   infer it, never read it from a request body, never mutate it after write.
2. **A live demo runs the published revision, or it fails.** Never a draft.
   `Registry.published_revision()` for live, `Registry.latest_revision()` for
   dashboard edits and test demos.
3. **A test demo may run a draft.** That is the point of a test demo.
4. **Test demos never count toward usage or billing.**
   `ActionLog.product_metrics()` excludes `dashboard_test` sessions.
5. **`product_id` comes from the credential**, never from a path or body.
6. **An End User never authenticates** and never reaches a Client surface
   (dashboard, flow editor, site graph, corrections queue).
7. **A Client surface is never reachable from the public embed path**, and the
   dashboard is never embeddable on a public landing page.
8. **The public embed button says exactly "Start a demo."** Not "Show Demo", not
   any Platform-branded label.

## Three roles — use these words in code, docs, and UI

- **Platform** — Resiliencesoft. Builds and operates Navigator. Not a tenant.
- **Client** — the company that buys Navigator (e.g. Edureka). A tenant scoped by
  `product_id`. Owns their site graph, flows, knowledge base, credentials.
- **End User** — a visitor on the Client's landing page. Never authenticates.
  Sees one button, then a demo of the *Client's* product.

Never call a Client an "end user". Never build UI that assumes an End User
configures anything.

## Security rules

- Never expose a `nav_` API key in client-side HTML or JS. The public embed uses
  single-use `sess_` session tokens only.
- Every `/client/api/*` route requires dashboard JWT auth. No exceptions — the
  recorder routes drive a headful browser and write to the site graph.
- The client dashboard is loopback-only.

## Tenant neutrality

No hardcoded ResilioHub or Resiliencesoft names, URLs, copy, credentials, or
flows in Navigator core (`navigator/app`, `navigator/agent`, `navigator/meeting`,
`navigator/voice`, shared UI). Sample tenants live only in config, fixtures,
docs, and examples. Defaults and placeholders must be generic ("your product") or
loaded from the active Client's registry / site graph / product brief.

## Before coding, ask

1. Who is this for — Platform, Client, or End User?
2. Which demo origin does this path produce, and is it set from the credential?
3. Does this read a draft or a published revision, and is that correct for the
   caller?
4. Does this move a usage counter? Should it, given the origin?
5. Any tenant-specific string in core? Move it to config or the site graph.

## Housekeeping

- Regenerate docs after API changes: `python -m navigator.docs build`.
- Never commit `graphify-out/` or `graphify/`.

## Starting the dev stack

When you start Navigator (uvicorn, docker, scripts) from this repo, **always read
the logs** before telling the user the stack is ready. Scan for `ERROR`,
`Traceback`, `WARN`, `[runner]`, `[live]`, `[attendee]`, `[zoom]`. If you see
errors, diagnose and fix them (or document the blocker) — then restart and
re-check logs. Do not skip this step.
