import { useEffect, useRef, useState } from "react";
import { Expand, Eye, EyeOff, Minimize2, X } from "lucide-react";
import {
  bindVideoStream,
  displayShareSupported,
  startDisplayShare,
  stopMediaStream,
} from "../lib/displayShare";
import { Button } from "./ui";
import { useUi } from "../store";

export function ExploreWatch({ live }: { live: boolean }) {
  const { err, ok } = useUi();
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [expanded, setExpanded] = useState(false);
  const streamRef = useRef<MediaStream | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const modalVideoRef = useRef<HTMLVideoElement | null>(null);

  const watching = !!stream;

  const teardown = () => {
    stopMediaStream(streamRef.current);
    streamRef.current = null;
    setStream(null);
    setExpanded(false);
  };

  // Keep ref in sync for unmount / live-end cleanup without stale closures.
  useEffect(() => {
    streamRef.current = stream;
  }, [stream]);

  // Explore ended → drop capture.
  useEffect(() => {
    if (!live && streamRef.current) teardown();
    // ponytail: only react to live edge; teardown reads streamRef
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live]);

  // Unmount cleanup.
  useEffect(() => {
    return () => {
      stopMediaStream(streamRef.current);
      streamRef.current = null;
    };
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

  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  const start = async () => {
    const result = await startDisplayShare();
    if (!result.ok) {
      err(result.message);
      return;
    }
    streamRef.current = result.stream;
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
