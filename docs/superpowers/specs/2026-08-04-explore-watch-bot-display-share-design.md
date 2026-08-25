# Explore “Watch bot” — Chrome display share preview

**Date:** 2026-08-04  
**Status:** approved for planning  
**Audience:** Navigator Client (tenant) dashboard — Flows / Auto-Explore only  
**Approach:** browser `getDisplayMedia` (user picks the Playwright/Chrome window)

## Goals

1. While Auto-Explore is live, Client can **watch what the bot is doing** on the product site.
2. Preview uses **real Chrome display capture**: Client clicks Watch → OS/browser share picker → Client selects the Chrome/Playwright window Navigator opened for this explore.
3. Preview sits **above the explore log**: inline by default, expandable to a large modal.
4. **Video only** (no audio).

## Non-goals

- Server-side Playwright screenshot / CDP screencast streaming (rejected; user chose real share).
- Auto-attaching to the bot window without a user gesture (browsers forbid this).
- WebRTC signaling server, recording, or persistence of the stream.
- Showing Watch UI on ExploreFloat, public embed, or End User surfaces.
- Audio / tab-audio capture.
- Cross-machine watch (bot Chrome on another host cannot be shared from the dashboard browser).

## Decisions (locked)

| Topic | Choice |
|---|---|
| Capture source | Client shares the **bot’s Playwright/Chrome window** via share picker |
| Transport | Local `MediaStream` only — no Navigator API |
| Audio | Off |
| Default UI | Inline panel above explore logs |
| Expand | Same stream in lightbox/modal; Esc / Collapse returns to inline |
| Availability | Only while explore is live (`active` / starting / waiting); hide or disable otherwise |
| Stop | Stop watching, explore end, or Flows explore section unmount → stop tracks |
| Copy | Hint: choose the Chrome window Navigator opened for this explore |

## UX

1. Above the explore event log, when explore is live: button **“Watch bot”**.
2. Click → `navigator.mediaDevices.getDisplayMedia({ video: true, audio: false })`.
3. On success: show collapsible **inline** `<video autoplay playsInline muted>` panel above the log, with **Stop watching** and **Expand**.
4. **Expand** opens a modal with the same `<video>` element (or moved stream); **Collapse** / Esc returns inline.
5. Cancel / permission denied → toast; no empty broken video frame.
6. When explore goes terminal (`done` / `failed` / `stopped`) or Client stops watching → `track.stop()` on all tracks, clear `srcObject`, collapse panel.

## Architecture

```
Client dashboard (Flows / AutoExplore)
        │
        │  click “Watch bot”  (user gesture)
        ▼
getDisplayMedia({ video: true, audio: false })
        │
        │  Client picks Playwright/Chrome window
        ▼
MediaStream ──► <video> inline (above logs)
                    │
                    └── Expand ──► same stream in modal
```

No backend changes. No Fern/OpenAPI impact.

## Implementation sketch (frontend only)

- Small helper or local state in `Flows.tsx` / Auto-Explore block:
  - `watching: boolean`
  - `expanded: boolean`
  - `stream: MediaStream | null`
  - `videoRef`
- Start/stop helpers that always stop tracks on teardown.
- Gate button with existing `exploreIsLive` / session status.
- Keep stream ownership in the explore UI mounted on Flows (not ExploreFloat).

## Risks / limits (honest)

- Client must pick the **correct** window; wrong pick shows something else.
- If explore runs headless with no visible window, share has nothing useful to show — Watch still offered; hint copy covers it. (Ops note: explore should remain headed for Watch to be useful.)
- Browser support: Chromium-based dashboards are the primary target (Client dashboard already assumes modern Chrome).
- Secure context required (`https` or `http://127.0.0.1` / localhost) — matches current loopback dashboard.

## Success criteria

- Live explore → Watch bot → share picker → pick bot Chrome → live video above logs.
- Expand / collapse works without restarting capture.
- Stop watching or explore end clears stream; no lingering capture indicator in the browser tab.
- No new API routes; no End User exposure.
