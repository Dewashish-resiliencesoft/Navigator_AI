"""Local HTTP pages that mirror a Playwright viewport for Attendee.

`/agent`  — minimal mic page for voice_agent_settings.url (bot camera tile).
`/view`   — demo frames + live status badge (Speaking / Listening / …).
`/status` — JSON status for the overlay poller.
`/frame.jpg` — latest Playwright JPEG.

ponytail: frames come from CDP Page.screencast (see start_screencast) — Chromium
pushes a JPEG whenever the page repaints, so in-page CSS animation reaches Meet
at ~60fps and an idle page costs nothing. push_frame stays for seeding the first
paint and for demos with no screencast attached.
"""

from __future__ import annotations

import base64
import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import Page

_AVATAR_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "avatar"
# Prefer the file the operator dropped; keep legacy name as fallback.
_AVATAR_CANDIDATES = ("female_avatar.glb", "navigator_avatar.glb")

#: Meet `/view` JPEG poll cadence. 30fps looks smooth without the JPEG spam that
#: froze Chromium at 60fps. Viable now that Attendee's ffmpeg recorder is off.
#: ponytail: 30fps ceiling. Bump toward 16ms only if the box has headroom.
VIEW_FRAME_MS = 33
#: CDP screencast every repaint (60fps source) — cursor motion stays fluid.
SCREENCAST_EVERY_NTH_FRAME = 1


def resolve_avatar_glb() -> Path | None:
    for name in _AVATAR_CANDIDATES:
        path = _AVATAR_DIR / name
        if path.is_file() and path.stat().st_size > 1000:
            return path
    return None


_AGENT_HTML = """<!doctype html>
<html><head><meta charset=utf-8><title>Navigator AI</title>
<style>
html,body{margin:0;width:1280px;height:720px;overflow:hidden;background:#0b1220}
canvas{display:block;width:1280px;height:720px}
/* CSS fallback if Three.js or GLB fails */
#fallback{display:none;width:1280px;height:720px;background:#0b1220;color:#9fb3c8;
font:600 28px system-ui;align-items:center;justify-content:center;flex-direction:column;gap:20px}
#fallback .mouth{width:36px;height:8px;background:#60a5fa;border-radius:4px;
transition:all 0.15s ease}
#fallback.speaking .mouth{animation:talk 0.3s infinite alternate ease-in-out}
@keyframes talk{0%{height:8px;width:36px}50%{height:22px;width:30px;border-radius:50%}100%{height:12px;width:34px}}
.status-label{position:fixed;bottom:24px;left:50%;transform:translateX(-50%);
color:#60a5fa;font:500 14px system-ui;letter-spacing:.04em;text-transform:uppercase;opacity:.7}
</style>
<script type="importmap">
{"imports":{
  "three":"https://cdn.jsdelivr.net/npm/three@0.170.0/build/three.module.js",
  "three/addons/":"https://cdn.jsdelivr.net/npm/three@0.170.0/examples/jsm/"
}}
</script>
</head><body>
<canvas id="c" width="1280" height="720"></canvas>
<div id="fallback">
  <div style="font-size:64px">🤖</div>
  <div>Navigator AI</div>
  <div class="mouth"></div>
</div>
<div class="status-label" id="slabel">Ready</div>

<script type="module">
import * as THREE from 'three';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';

navigator.mediaDevices.getUserMedia({audio:true}).catch(()=>{});

const canvas = document.getElementById('c');
const fallback = document.getElementById('fallback');
const slabel = document.getElementById('slabel');

let avatarState = 'idle';
let headMesh = null;
let teethMesh = null;
let morphDict = {};

// Viseme sequence for simulated speech
const VISEMES = ['jawOpen','mouthOpen','viseme_aa','viseme_O','viseme_E','viseme_U',
                 'viseme_I','viseme_FF','viseme_TH','viseme_SS'];
const BLINK_L = 'eyeBlinkLeft';
const BLINK_R = 'eyeBlinkRight';
let visemeIdx = 0;
let visemeTimer = 0;
let blinkTimer = 0;
let nextBlink = 3 + Math.random()*2;

// Scene setup
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b1220);

const camera = new THREE.PerspectiveCamera(22, 1280/720, 0.1, 100);
camera.position.set(0, 1.55, 0.9);
camera.lookAt(0, 1.5, 0);

const renderer = new THREE.WebGLRenderer({canvas, antialias:true});
renderer.setSize(1280, 720);
renderer.setPixelRatio(1);
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.2;

// Lighting: soft 3-point for portrait feel
const ambient = new THREE.AmbientLight(0x404060, 1.2);
scene.add(ambient);
const keyLight = new THREE.DirectionalLight(0xddeeff, 2.5);
keyLight.position.set(1, 2, 2);
scene.add(keyLight);
const fillLight = new THREE.DirectionalLight(0x6080a0, 1.0);
fillLight.position.set(-1.5, 1, 1);
scene.add(fillLight);
const rimLight = new THREE.DirectionalLight(0x3060ff, 0.8);
rimLight.position.set(0, 1.5, -2);
scene.add(rimLight);

// Subtle gradient backdrop sphere
const bgGeo = new THREE.SphereGeometry(8, 32, 32);
const bgMat = new THREE.MeshBasicMaterial({
  color: 0x0b1220,
  side: THREE.BackSide
});
scene.add(new THREE.Mesh(bgGeo, bgMat));

// Load avatar GLB
const loader = new GLTFLoader();
let model = null;
let loadFailed = false;

function findMorphMeshes(obj) {
  obj.traverse(child => {
    if (!child.isMesh || !child.morphTargetDictionary) return;
    const name = (child.name || '').toLowerCase();
    if (name.includes('wolf3d_head') || name === 'head' ||
        name.includes('head') || name.includes('face')) {
      // Prefer the named head mesh over a random morph mesh.
      if (!headMesh || name.includes('wolf3d_head') || name.includes('head')) {
        headMesh = child;
        morphDict = child.morphTargetDictionary;
      }
    }
    if (name.includes('teeth')) teethMesh = child;
  });
  if (!headMesh) {
    obj.traverse(child => {
      if (child.isMesh && child.morphTargetDictionary && !headMesh) {
        headMesh = child;
        morphDict = child.morphTargetDictionary;
      }
    });
  }
}

function frameModel(obj) {
  // Portrait crop: head (or whole model) fills the tile like a photo.
  const target = headMesh || obj;
  const box = new THREE.Box3().setFromObject(target);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  if (!isFinite(size.x) || size.x + size.y + size.z < 1e-4) return;
  const maxDim = Math.max(size.x, size.y, size.z);
  camera.fov = 26;
  camera.updateProjectionMatrix();
  const fov = camera.fov * Math.PI / 180;
  // Closer than full-body — shoulders-up portrait.
  const dist = (maxDim / (2 * Math.tan(fov / 2))) * (headMesh ? 1.35 : 0.75);
  camera.position.set(center.x, center.y + size.y * 0.02, center.z + Math.max(dist, 0.35));
  camera.lookAt(center.x, center.y + size.y * 0.05, center.z);
}

try {
  loader.load('/avatar.glb',
    gltf => {
      model = gltf.scene;
      model.position.set(0, 0, 0);
      scene.add(model);
      findMorphMeshes(model);
      frameModel(model);
      if (!headMesh) {
        console.warn('No ARKit morph targets — showing static 3D avatar (no lip sync)');
      } else {
        console.log('Lip sync ready — morphs:', Object.keys(morphDict).length);
      }
    },
    undefined,
    err => {
      console.warn('GLB load failed:', err);
      useFallback();
    }
  );
} catch(e) {
  useFallback();
}

function useFallback() {
  loadFailed = true;
  canvas.style.display = 'none';
  fallback.style.display = 'flex';
}

// Set morph target by name with lerp smoothing
function setMorph(name, target, speed = 0.15) {
  if (!headMesh || !(name in morphDict)) return;
  const idx = morphDict[name];
  const cur = headMesh.morphTargetInfluences[idx];
  const val = THREE.MathUtils.lerp(cur, target, speed);
  headMesh.morphTargetInfluences[idx] = val;
  // Mirror to teeth if available
  if (teethMesh && teethMesh.morphTargetDictionary &&
      name in teethMesh.morphTargetDictionary) {
    const ti = teethMesh.morphTargetDictionary[name];
    teethMesh.morphTargetInfluences[ti] = val;
  }
}

// Reset all morph targets to 0
function resetMorphs(speed = 0.1) {
  if (!headMesh) return;
  for (let i = 0; i < headMesh.morphTargetInfluences.length; i++) {
    headMesh.morphTargetInfluences[i] =
      THREE.MathUtils.lerp(headMesh.morphTargetInfluences[i], 0, speed);
  }
}

const clock = new THREE.Clock();

function animate() {
  requestAnimationFrame(animate);
  const dt = clock.getDelta();

  if (!loadFailed && model) {
    if (headMesh) {
      // Blink
      blinkTimer += dt;
      if (blinkTimer > nextBlink) {
        setMorph(BLINK_L, 1, 0.5);
        setMorph(BLINK_R, 1, 0.5);
        if (blinkTimer > nextBlink + 0.15) {
          blinkTimer = 0;
          nextBlink = 2.5 + Math.random() * 3;
        }
      } else {
        setMorph(BLINK_L, 0, 0.3);
        setMorph(BLINK_R, 0, 0.3);
      }
    }

    if (avatarState === 'speaking') {
      if (headMesh) {
        visemeTimer += dt;
        if (visemeTimer > 0.07 + Math.random() * 0.1) {
          visemeTimer = 0;
          visemeIdx = (visemeIdx + 1) % VISEMES.length;
        }
        for (const v of VISEMES) {
          const isActive = v === VISEMES[visemeIdx];
          const targetVal = isActive ? 0.45 + Math.random() * 0.45 : 0;
          setMorph(v, targetVal, 0.35);
        }
        // Strong jaw so lip motion reads on a Meet thumbnail.
        setMorph('jawOpen', 0.28 + Math.random() * 0.42, 0.35);
        setMorph('mouthClose', 0, 0.3);
      }
      // Subtle head nod / sway synced to speech cadence.
      model.rotation.y = Math.sin(clock.elapsedTime * 2.4) * 0.06;
      model.rotation.x = Math.sin(clock.elapsedTime * 1.7) * 0.03;
      model.position.y = Math.sin(clock.elapsedTime * 5.5) * 0.006;
    } else if (avatarState === 'listening') {
      resetMorphs(0.08);
      setMorph('browInnerUp', 0.2, 0.1);
      setMorph('mouthSmileLeft', 0.15, 0.1);
      setMorph('mouthSmileRight', 0.15, 0.1);
      model.rotation.y = Math.sin(clock.elapsedTime * 0.3) * 0.02;
    } else if (avatarState === 'thinking') {
      resetMorphs(0.08);
      setMorph('browInnerUp', 0.3, 0.1);
      setMorph('mouthPucker', 0.15 + Math.sin(clock.elapsedTime * 2) * 0.1, 0.1);
      model.rotation.y = Math.sin(clock.elapsedTime * 0.4) * 0.04;
      model.rotation.z = Math.sin(clock.elapsedTime * 0.6) * 0.01;
    } else {
      resetMorphs(0.06);
      model.rotation.y = THREE.MathUtils.lerp(model.rotation.y, 0, 0.05);
      model.rotation.x = THREE.MathUtils.lerp(model.rotation.x || 0, 0, 0.05);
      model.rotation.z = THREE.MathUtils.lerp(model.rotation.z || 0, 0, 0.05);
      model.position.y = Math.sin(clock.elapsedTime * 0.8) * 0.003;
    }
    renderer.render(scene, camera);
  } else if (loadFailed) {
    fallback.className = avatarState === 'idle' ? '' : avatarState;
  }
}
animate();

// Poll avatar state from relay
async function pollState() {
  try {
    const r = await fetch('/avatar-state?t='+Date.now(), {cache:'no-store'});
    if (r.ok) {
      const j = await r.json();
      avatarState = (j.state || 'idle').toLowerCase();
      const labels = {speaking:'Speaking',listening:'Listening',
                      thinking:'Thinking',idle:'Ready'};
      slabel.textContent = labels[avatarState] || 'Ready';
    }
  } catch(e){}
  setTimeout(pollState, 150);
}
pollState();
</script></body></html>
"""

_VIEW_HTML = """<!doctype html>
<html><head><meta charset=utf-8><title>Navigator screen</title>
<style>
html,body{margin:0;background:#000;width:1280px;height:720px;overflow:hidden;
font-family:ui-sans-serif,system-ui,sans-serif}
img{width:1280px;height:720px;object-fit:fill;display:block;image-rendering:auto}
#badge{position:fixed;left:24px;bottom:24px;z-index:9;padding:10px 16px;
border-radius:999px;background:rgba(11,18,32,.82);color:#e8eef7;font:600 15px/1.2 system-ui;
letter-spacing:.02em;backdrop-filter:blur(6px);border:1px solid rgba(255,255,255,.12);
display:flex;align-items:center;gap:10px}
#dot{width:10px;height:10px;border-radius:50%;background:#6ee7b7;box-shadow:0 0 0 0 rgba(110,231,183,.7);
animation:pulse 1.6s infinite}
#badge[data-mode=speaking] #dot{background:#60a5fa}
#badge[data-mode=listening] #dot{background:#fbbf24}
#badge[data-mode=tailoring] #dot{background:#c084fc}
#badge[data-mode=demo] #dot{background:#6ee7b7}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(255,255,255,.35)}70%{box-shadow:0 0 0 10px rgba(255,255,255,0)}100%{box-shadow:0 0 0 0 rgba(255,255,255,0)}}
</style></head>
<body>
<img id=f alt=frame width=1280 height=720>
<div id=badge data-mode=demo><span id=dot></span><span id=label>Demo</span></div>
<script>
async function tickFrame(){
  try {
    const r = await fetch('/frame.jpg?ts='+Date.now(), {cache:'no-store'});
    if (r.ok) {
      const b = await r.blob();
      const url = URL.createObjectURL(b);
      const img = document.getElementById('f');
      const old = img.src;
      img.src = url;
      if (old && old.startsWith('blob:')) URL.revokeObjectURL(old);
    }
  } catch (e) {}
  setTimeout(tickFrame, 33);
}
async function tickStatus(){
  try {
    const r = await fetch('/status?ts='+Date.now(), {cache:'no-store'});
    if (r.ok) {
      const j = await r.json();
      const mode = (j.mode || 'demo').toLowerCase();
      const label = j.label || mode;
      const badge = document.getElementById('badge');
      badge.dataset.mode = mode;
      document.getElementById('label').textContent = label;
    }
  } catch (e) {}
  setTimeout(tickStatus, 400);
}
tickFrame();
tickStatus();
</script></body></html>
"""


@dataclass
class RelayHandle:
    host: str
    port: int
    _httpd: ThreadingHTTPServer
    _thread: threading.Thread
    _frame: bytes = b""
    _lock: threading.Lock = field(default_factory=threading.Lock)
    frame_hits: int = 0
    view_hits: int = 0
    screencast: bool = False
    status_mode: str = "demo"
    status_label: str = "Demo"
    avatar_state: str = "idle"

    @property
    def view_url(self) -> str:
        return f"http://{self.host}:{self.port}/view"

    @property
    def agent_url(self) -> str:
        return f"http://{self.host}:{self.port}/agent"

    def set_status(self, mode: str, label: str | None = None) -> None:
        mode = (mode or "demo").strip().lower() or "demo"
        self.status_mode = mode
        self.status_label = label or mode.replace("_", " ").title()

    def set_avatar_state(self, state: str) -> None:
        self.avatar_state = (state or "idle").strip().lower() or "idle"

    def stop(self) -> None:
        self._httpd.shutdown()
        self._thread.join(timeout=5)


def start_relay(host: str = "127.0.0.1", port: int = 0) -> RelayHandle:
    """Start the HTTP server. Push frames from the Playwright thread via push_frame."""
    holder: dict[str, RelayHandle] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object, **_kwargs: object) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            handle = holder["h"]
            path = self.path.split("?", 1)[0]
            if path.startswith("/agent"):
                body = _AGENT_HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith("/avatar-state"):
                payload = json.dumps({"state": handle.avatar_state}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if path.startswith("/avatar.glb"):
                glb_path = resolve_avatar_glb()
                if glb_path is not None:
                    data = glb_path.read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "model/gltf-binary")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Cache-Control", "public, max-age=3600")
                    self.end_headers()
                    self.wfile.write(data)
                else:
                    self.send_response(404)
                    self.end_headers()
                return
            if path.startswith("/view"):
                handle.view_hits += 1
                body = _VIEW_HTML.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith("/status"):
                payload = json.dumps(
                    {"mode": handle.status_mode, "label": handle.status_label}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            if path.startswith("/frame"):
                handle.frame_hits += 1
                with handle._lock:
                    data = handle._frame
                if not data:
                    self.send_response(204)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            handle = holder["h"]
            path = self.path.split("?", 1)[0]
            if path.startswith("/avatar-state"):
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else b"{}"
                try:
                    data = json.loads(body)
                    handle.avatar_state = str(data.get("state", "idle"))
                except Exception:
                    pass
                self.send_response(200)
                self.end_headers()
                return
            self.send_response(404)
            self.end_headers()

    httpd = ThreadingHTTPServer((host, port), Handler)
    real_port = int(httpd.server_address[1])
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
    )
    handle = RelayHandle(host=host, port=real_port, _httpd=httpd, _thread=thread)
    holder["h"] = handle
    thread.start()
    return handle


def start_screencast(handle: RelayHandle, page: Page):
    """Stream repaints into ``handle`` via CDP instead of polling screenshots.

    A `page.screenshot()` costs ~30ms of the Playwright thread — the same thread
    that has to keep 24kHz PCM flowing — so the old per-hop push capped motion at
    ~12fps and overran its own timing by ~1.5x. Chromium pushes screencast frames
    on repaint for free, so an in-page CSS animation arrives at ~60fps.

    Returns the CDP session, or None if screencast is unavailable (the caller
    then keeps using push_frame).
    """
    from navigator.core.settings import settings

    quality = max(1, min(100, int(settings.screenshot_quality or 70)))
    try:
        cdp = page.context.new_cdp_session(page)
    except Exception as exc:  # noqa: BLE001
        print(f"[live] screencast unavailable, using screenshots: {exc}", flush=True)
        return None

    def _on_frame(event: dict) -> None:
        data = event.get("data")
        if data:
            with handle._lock:
                handle._frame = base64.b64decode(data)
        # Chromium stops sending until the frame is acked.
        try:
            cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})
        except Exception:  # noqa: BLE001
            pass

    try:
        cdp.on("Page.screencastFrame", _on_frame)
        cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": quality,
                "maxWidth": 1280,
                "maxHeight": 720,
                "everyNthFrame": SCREENCAST_EVERY_NTH_FRAME,
            },
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[live] startScreencast failed, using screenshots: {exc}", flush=True)
        return None
    print(f"[live] screencast=on quality={quality}", flush=True)
    handle.screencast = True
    return cdp


def stop_screencast(cdp) -> None:
    if cdp is None:
        return
    try:
        cdp.send("Page.stopScreencast")
    except Exception:  # noqa: BLE001
        pass


def push_frame(handle: RelayHandle, page: Page) -> None:
    """Screenshot on the Playwright thread only (sync API is not thread-safe)."""
    from navigator.core.settings import settings

    # Screencast already encoded the repaint; the frame just has to be collected.
    # Playwright's sync client only dispatches CDP events while it is inside a
    # call, so yield to it briefly (~2ms) instead of taking a ~30ms screenshot.
    if handle.screencast:
        try:
            page.wait_for_timeout(1)
        except Exception:  # noqa: BLE001
            pass
        return
    quality = max(50, min(100, int(settings.screenshot_quality or 95)))
    try:
        data = page.screenshot(
            type="jpeg",
            quality=quality,
            clip={"x": 0, "y": 0, "width": 1280, "height": 720},
        )
    except Exception:
        data = page.screenshot(type="jpeg", quality=quality)
    with handle._lock:
        handle._frame = data
