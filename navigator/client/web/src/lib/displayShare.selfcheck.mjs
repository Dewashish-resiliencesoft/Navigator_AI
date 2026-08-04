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
