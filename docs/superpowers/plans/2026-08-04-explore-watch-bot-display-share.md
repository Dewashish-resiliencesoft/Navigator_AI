# Explore Watch Bot (Chrome display share) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Client watch the bot’s Playwright/Chrome window live during Auto-Explore via `getDisplayMedia`, shown inline above the explore log with expand-to-modal.

**Architecture:** Frontend-only. User gesture opens the OS/browser share picker; Client selects the bot Chrome window; a local `MediaStream` feeds a muted `<video>`. No Navigator API, no WebRTC server, no Fern changes. Stream lifecycle tied to Auto-Explore UI (stop on Stop watching, explore end, or unmount).

**Tech Stack:** React 19, TypeScript, existing `Flows.tsx` / `ui.tsx` patterns, `getDisplayMedia`

**Spec:** `docs/superpowers/specs/2026-08-04-explore-watch-bot-display-share-design.md`

---

## File map

| File | Responsibility |
|---|---|
| `navigator/client/web/src/lib/displayShare.ts` | Start/stop display capture helpers (no React) |
| `navigator/client/web/src/components/ExploreWatch.tsx` | Watch button, inline preview, expand modal |
| `navigator/client/web/src/panels/Flows.tsx` | Mount `ExploreWatch` above Exploration log when explore live / has log |
| *(no backend / Fern)* | Spec non-goal |

---

### Task 1: Display-share helpers

**Files:**
- Create: `navigator/client/web/src/lib/displayShare.ts`
- Create: `navigator/client/web/src/lib/displayShare.selfcheck.mjs` (node assert, no new deps)

- [ ] **Step 1: Write self-check for `stopMediaStream` (fails until helper exists in sync)**

Create `navigator/client/web/src/lib/displayShare.selfcheck.mjs`:

```js
import assert from "node:assert/strict";

/** Mirrors stopMediaStream — keep in sync with displayShare.ts */
function stopMediaStream(stream) {
  if (!stream) return;
  for (const track of stream.getTracks()) {
    try {
      track.stop();
    } catch {
      /* ignore */
    }
  }
}

let stopped = 0;
const fake = {
  getTracks: () => [
    { stop: () => { stopped += 1; } },
    { stop: () => { stopped += 1; } },
  ],
};

stopMediaStream(null);
assert.equal(stopped, 0);
stopMediaStream(fake);
assert.equal(stopped, 2);
console.log("displayShare.selfcheck ok");
```

- [ ] **Step 2: Run self-check**

Run:

```bash
node navigator/client/web/src/lib/displayShare.selfcheck.mjs
```

Expected: `displayShare.selfcheck ok`

- [ ] **Step 3: Implement TypeScript helpers**

Create `navigator/client/web/src/lib/displayShare.ts`:

```ts
/** Local Chrome/window capture for Client “Watch bot” during explore. */

export function displayShareSupported(): boolean {
  return (
    typeof navigator !== "undefined" &&
    !!navigator.mediaDevices?.getDisplayMedia
  );
}

/** Stop every track. Safe on null/undefined. */
export function stopMediaStream(stream: MediaStream | null | undefined): void {
  if (!stream) return;
  for (const track of stream.getTracks()) {
    try {
      track.stop();
    } catch {
      /* already stopped */
    }
  }
}

export type StartDisplayShareResult =
  | { ok: true; stream: MediaStream }
  | { ok: false; reason: "unsupported" | "denied" | "failed"; message: string };

/** Must be called from a user gesture (button click). Video only. */
export async function startDisplayShare(): Promise<StartDisplayShareResult> {
  if (!displayShareSupported()) {
    return {
      ok: false,
      reason: "unsupported",
      message: "Screen share is not available in this browser.",
    };
  }
  try {
    const stream = await navigator.mediaDevices.getDisplayMedia({
      video: true,
      audio: false,
    });
    return { ok: true, stream };
  } catch (e) {
    const name = e instanceof DOMException ? e.name : "";
    if (name === "NotAllowedError" || name === "AbortError") {
      return {
        ok: false,
        reason: "denied",
        message: "Screen share cancelled or blocked.",
      };
    }
    return {
      ok: false,
      reason: "failed",
      message: e instanceof Error ? e.message : String(e),
    };
  }
}

/** Attach stream to a video element; returns a cleanup that clears srcObject. */
export function bindVideoStream(
  video: HTMLVideoElement | null,
  stream: MediaStream | null,
): () => void {
  if (!video) return () => {};
  video.srcObject = stream;
  if (stream) {
    void video.play().catch(() => {
      /* autoplay policies — muted + playsInline should allow */
    });
  }
  return () => {
    video.srcObject = null;
  };
}
```

- [ ] **Step 4: Commit**

```bash
git add -f navigator/client/web/src/lib/displayShare.ts navigator/client/web/src/lib/displayShare.selfcheck.mjs
git commit -m "feat(client): display share helpers for explore Watch bot"
```

---

### Task 2: `ExploreWatch` UI component

**Files:**
- Create: `navigator/client/web/src/components/ExploreWatch.tsx`
- Modify: none yet

- [ ] **Step 1: Create component**

```tsx
import { useEffect, useRef, useState } from "react";
import { Expand, Eye, EyeOff, Minimize2, X } from "lucide-react";
import {
  bindVideoStream,
  displayShareSupported,
  startDisplayShare,
  stopMediaStream,
} from "../lib/displayShare";
import { Button } from "./ui";
import { errText, useUi } from "../store";

export function ExploreWatch({ live }: { live: boolean }) {
  const { err, ok } = useUi();
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [expanded, setExpanded] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const modalVideoRef = useRef<HTMLVideoElement | null>(null);

  const watching = !!stream;

  const teardown = () => {
    stopMediaStream(stream);
    setStream(null);
    setExpanded(false);
  };

  // Explore ended → drop capture.
  useEffect(() => {
    if (!live && stream) teardown();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only react to live edge
  }, [live]);

  // Unmount cleanup.
  useEffect(() => {
    return () => {
      stopMediaStream(stream);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // If the user stops share from the browser chrome UI, clear state.
  useEffect(() => {
    if (!stream) return;
    const tracks = stream.getVideoTracks();
    const onEnded = () => teardown();
    for (const t of tracks) t.addEventListener("ended", onEnded);
    return () => {
      for (const t of tracks) t.removeEventListener("ended", onEnded);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stream]);

  useEffect(() => {
    return bindVideoStream(videoRef.current, expanded ? null : stream);
  }, [stream, expanded]);

  useEffect(() => {
    return bindVideoStream(modalVideoRef.current, expanded ? stream : null);
  }, [stream, expanded]);

  const start = async () => {
    const result = await startDisplayShare();
    if (!result.ok) {
      if (result.reason !== "denied") err(result.message);
      else err(result.message);
      return;
    }
    // Browser may fire ended immediately on some cancels — keep ref.
    setStream(result.stream);
    ok("Watching bot window — pick the Chrome window Navigator opened.");
  };

  if (!live && !watching) return null;

  return (
    <div className="mt-4 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {!watching ? (
          <Button
            variant="secondary"
            onClick={() => void start()}
            disabled={!live || !displayShareSupported()}
          >
            <Eye size={13} /> Watch bot
          </Button>
        ) : (
          <>
            <Button variant="secondary" onClick={teardown}>
              <EyeOff size={13} /> Stop watching
            </Button>
            <Button variant="ghost" onClick={() => setExpanded(true)}>
              <Expand size={13} /> Expand
            </Button>
          </>
        )}
      </div>
      <p className="text-[0.68rem] text-[var(--muted)]">
        Choose the Chrome window Navigator opened for this explore. Video only —
        no audio.
      </p>
      {watching && !expanded && (
        <div
          className="overflow-hidden rounded-xl border bg-black/80"
          style={{ borderColor: "var(--line)" }}
        >
          <video
            ref={videoRef}
            className="aspect-video w-full object-contain"
            autoPlay
            playsInline
            muted
          />
        </div>
      )}
      {expanded && watching && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          role="dialog"
          aria-label="Bot window preview"
          onClick={() => setExpanded(false)}
          onKeyDown={(e) => {
            if (e.key === "Escape") setExpanded(false);
          }}
        >
          <div
            className="relative w-full max-w-5xl overflow-hidden rounded-xl border bg-black"
            style={{ borderColor: "var(--line)" }}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-end gap-2 p-2">
              <Button variant="ghost" onClick={() => setExpanded(false)}>
                <Minimize2 size={13} /> Collapse
              </Button>
              <Button variant="ghost" onClick={teardown}>
                <X size={13} /> Stop
              </Button>
            </div>
            <video
              ref={modalVideoRef}
              className="aspect-video w-full object-contain"
              autoPlay
              playsInline
              muted
            />
          </div>
        </div>
      )}
    </div>
  );
}
```

Fix Esc: add `useEffect` for `keydown` when `expanded` so Escape works without focusing the backdrop.

Add inside the component after other effects:

```tsx
  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);
```

Remove unused `errText` import if present.

- [ ] **Step 2: Typecheck**

Run:

```bash
cd navigator/client/web && npm run typecheck
```

Expected: exit 0

- [ ] **Step 3: Commit**

```bash
git add navigator/client/web/src/components/ExploreWatch.tsx
git commit -m "feat(client): ExploreWatch inline + modal display share UI"
```

---

### Task 3: Wire above Exploration log in Auto-Explore

**Files:**
- Modify: `navigator/client/web/src/panels/Flows.tsx`

- [ ] **Step 1: Import and mount**

At top of `Flows.tsx` imports, add:

```tsx
import { ExploreWatch } from "../components/ExploreWatch";
```

Inside `AutoExplore`, immediately **before** the block that starts with `{(running || logLines.length > 0 || visitedPaths.length > 0) && (` (the Exploration log card — currently ~line 785), insert:

```tsx
      <ExploreWatch live={running} />
```

So order is: meter / question → **Watch bot** → Exploration log.

- [ ] **Step 2: Build dist (dashboard on :8000 serves built assets)**

Run:

```bash
cd navigator/client/web && npm run build
```

Expected: `tsc -b && vite build` succeeds; new hashed `dist/assets/index-*.js`.

- [ ] **Step 3: Commit**

```bash
git add navigator/client/web/src/panels/Flows.tsx navigator/client/web/dist
git commit -m "feat(client): show Watch bot above explore log"
```

(If `dist/` is gitignored, commit only the source change; rebuild locally before manual test.)

- [ ] **Step 4: Manual verification checklist**

1. Open Client dashboard Flows (`http://127.0.0.1:8000/client` or Vite `:5173/client/`).
2. Start exploring (headed Chrome must be visible).
3. Confirm **Watch bot** appears above Exploration log.
4. Click Watch bot → share picker → select the bot Chrome window.
5. Inline video shows the window; Expand → modal; Esc/Collapse → inline again.
6. Stop watching → capture indicator gone; explore still runs.
7. Stop exploring while watching → video clears.
8. Cancel share picker → toast, no blank video stuck open.

---

### Task 4: Housekeeping

**Files:** none required for Fern (no API). Graphify optional for new TS files.

- [ ] **Step 1: graphify**

```bash
graphify update .
```

- [ ] **Step 2: Final commit if graphify-out dirty — do NOT commit graphify-out/**

Only commit source if any leftover fix from manual test.

---

## Spec coverage check

| Spec requirement | Task |
|---|---|
| getDisplayMedia, video only | Task 1 `startDisplayShare` |
| User picks bot Chrome window | Task 1 + Task 2 hint copy |
| Button above explore log when live | Task 2 + Task 3 |
| Inline preview + Expand modal | Task 2 |
| Stop on Stop watching / explore end / unmount | Task 2 effects |
| Toast on deny/fail | Task 2 `start` |
| No ExploreFloat / no API / no audio | Non-goals honored — not in file map |
| Secure context / localhost | Ops note in manual checklist |

## Placeholder scan

None intentionally left. Selfcheck duplicates `stopMediaStream` logic in `.mjs` (no TS loader) — comment says keep in sync; acceptable without adding vitest.
