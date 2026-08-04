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
