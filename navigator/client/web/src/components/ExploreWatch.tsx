import { useEffect, useState } from "react";
import { Expand, Eye, EyeOff, Minimize2 } from "lucide-react";
import { useExploreSession } from "../lib/exploreSession";
import { Button } from "./ui";

/** Live viewport of the server Chromium running explore — no screen-share picker. */
export function ExploreWatch({ live }: { live: boolean }) {
  const latestFrame = useExploreSession((s) => s.latestFrame);
  const pullFrame = useExploreSession((s) => s.pullFrame);
  const [watching, setWatching] = useState(false);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!live) {
      setWatching(false);
      setExpanded(false);
    }
  }, [live]);

  useEffect(() => {
    if (!watching || !live) return;
    void pullFrame();
    const t = setInterval(() => {
      void pullFrame();
    }, 1500);
    return () => clearInterval(t);
  }, [watching, live, pullFrame]);

  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  if (!live && !watching) return null;

  const src = latestFrame
    ? `data:${latestFrame.mime};base64,${latestFrame.data}`
    : null;

  return (
    <div className="mt-4 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {!watching ? (
          <Button
            variant="secondary"
            onClick={() => setWatching(true)}
            disabled={!live}
          >
            <Eye size={13} /> Watch bot
          </Button>
        ) : (
          <>
            <Button
              variant="secondary"
              onClick={() => {
                setWatching(false);
                setExpanded(false);
              }}
            >
              <EyeOff size={13} /> Stop watching
            </Button>
            <Button
              variant="ghost"
              onClick={() => setExpanded(true)}
              disabled={!src}
            >
              <Expand size={13} /> Expand
            </Button>
          </>
        )}
      </div>
      <p className="text-[0.68rem] text-[var(--muted)]">
        Live view of Chromium on this Navigator host (same window the bot
        drives). No screen-share permission — works the same on a VPS.
      </p>
      {watching && !expanded && (
        <div
          className="overflow-hidden rounded-xl border bg-black/80"
          style={{ borderColor: "var(--line)" }}
        >
          {src ? (
            <img
              src={src}
              alt="Bot Chromium viewport"
              className="aspect-video w-full object-contain"
            />
          ) : (
            <p className="px-3 py-8 text-center text-[0.72rem] text-[var(--muted)]">
              Waiting for first frame from the explore browser…
            </p>
          )}
        </div>
      )}
      {expanded && watching && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          role="dialog"
          aria-label="Bot window preview"
          onClick={() => setExpanded(false)}
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
            </div>
            {src ? (
              <img
                src={src}
                alt="Bot Chromium viewport"
                className="aspect-video w-full object-contain"
              />
            ) : (
              <p className="px-3 py-16 text-center text-[0.72rem] text-[var(--muted)]">
                Waiting for frame…
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
