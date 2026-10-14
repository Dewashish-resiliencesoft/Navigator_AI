"""Navigator side of the screenshare harness: real Chromium + real relay +
real ScreenThread screencast, with optional synthetic audio-path load
(numpy resample + base64 + websocket-sized work every 20ms, like the bridge).
Prints relay fps every 5s. Usage: chain.py <bind_host> <seconds> <audio_load 0|1>"""
import base64, os, sys, threading, time
import numpy as np
sys.path.insert(0, os.getcwd())
from playwright.sync_api import sync_playwright
from navigator.meeting.relay import start_relay
from navigator.meeting import ui_jobs

host, dur, load = sys.argv[1], float(sys.argv[2]), sys.argv[3] == "1"
relay = start_relay(host, 0)
print(f"VIEW http://{host}:{relay.port}/view", flush=True)
stop = threading.Event()

def audio_load():
    t = np.arange(480) / 24000.0
    while not stop.is_set():
        pcm = (np.sin(2 * np.pi * 220 * t) * 8000).astype(np.int16)
        up = np.repeat(pcm, 2)
        b = base64.b64encode(up.tobytes())
        _ = base64.b64decode(b); sum(int(x) for x in pcm[::4])  # pure-python GIL work
        time.sleep(0.02)

with sync_playwright() as p:
    port = 9333
    br = p.chromium.launch(args=[f"--remote-debugging-port={port}"])
    pg = br.new_page(viewport={"width": 1280, "height": 720})
    pg.goto(os.environ.get("CLOCK_URL", "http://172.18.0.1:47123/clock.html"))
    st = ui_jobs.ScreenThread(port, relay)
    assert st.start(timeout=15), st.error
    if load:
        for _ in range(2): threading.Thread(target=audio_load, daemon=True).start()
    t0 = time.time(); last = relay._seq
    while time.time() - t0 < dur:
        pg.wait_for_timeout(5000)
        s = relay._seq; print(f"relay_fps={(s - last) / 5:.1f} streamed={relay.frames_streamed}", flush=True); last = s
    stop.set(); st.stop(); br.close()
