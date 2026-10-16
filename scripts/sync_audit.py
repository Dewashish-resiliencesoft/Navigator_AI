"""Voice/screen sync audit: runs a flow through the real EXECUTING node with real
Gemini Live audio and a real browser. For every spoken sentence it samples the
screen (URL, heading, visible text, screenshot) at the sentence's speech start
and checks it against the step's expected screen and the nav names it mentions.

  python scripts/sync_audit.py --product resiliencesoft-com --rev 73 --flow rasiliohub \
      --page dashboard [--steps 0-28] [--questions] --email E --password P
Exit 1 on any mismatch. Table + screenshots in --out."""
import argparse, array, json, queue, random, re, sys, threading, time
from urllib.parse import urlparse

ap = argparse.ArgumentParser()
ap.add_argument("--product", required=True); ap.add_argument("--rev", type=int, required=True)
ap.add_argument("--flow", required=True); ap.add_argument("--page", required=True)
ap.add_argument("--steps", default=""); ap.add_argument("--questions", action="store_true")
ap.add_argument("--email", default=""); ap.add_argument("--password", default="")
ap.add_argument("--out", default="/tmp/navsync")
A = ap.parse_args()

from playwright.sync_api import sync_playwright

from navigator.automation.browser.viewport import context_kwargs
from navigator.app.deps import _registry
from navigator.agent.state import CallDeps
from navigator.agent.nodes import executing as ex
from navigator.core.gemini_keys import gemini_live_key_candidates
from navigator.meeting import ui_jobs
from navigator.meeting.audio_bridge import resample_pcm16
from navigator.meeting.playback_handle import PlaybackHandle
from navigator.voice.live_agent import LiveAgent, LiveAgentConfig

graph = _registry.load_graph(A.product, A.rev)
meta = getattr(graph, "meta", None) or {}
ui_jobs.NAV_INVENTORY = meta.get("nav_inventory") or []
ui_jobs.SCREEN_MAP = ui_jobs.build_screen_map(graph)
NAV_LABELS = sorted({str(e.get("label") or "").strip() for e in ui_jobs.NAV_INVENTORY
                     if 3 <= len(str(e.get("label") or "").strip()) <= 30}, key=len, reverse=True)
steps = list(graph.pages[A.page].flows[A.flow])
idx = list(range(len(steps)))
if A.steps:
    a, b = A.steps.split("-"); idx = list(range(int(a), int(b) + 1))
keys = gemini_live_key_candidates()
EV = []          # (kind, wall, data) from Live thread
LOG = []
def log(*a):
    s = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a); print(s, flush=True); LOG.append(s)

class Bridge:   # fake meeting output: arrival-timed playback clock
    def __init__(s): s.inbound = queue.Queue(); s.audio_s_sent = 0.0; s._tap_play_end = 0.0; s.echo_free = True; s.play_end = 0.0
    def push_outbound_pcm(s, pcm, sample_rate=24000, writer=None):
        d = len(pcm) / (2.0 * sample_rate); now = time.monotonic()
        if s.play_end < now:
            EV.append(("audio_start", now, None))
            s.play_end = now
        s.play_end += d; s.audio_s_sent += d
    @property
    def out_play_end(s): return s.play_end
    def clear_outbound(s): s.play_end = time.monotonic(); return 0
    def flush_bot_output(s): pass
    def mark_line_end(s): pass

def synth(text):
    class Cap(Bridge):
        def __init__(s): super().__init__(); s.buf = bytearray()
        def push_outbound_pcm(s, pcm, sample_rate=24000, writer=None): s.buf += pcm; s.audio_s_sent += len(pcm) / 48000
    c = Cap(); v = LiveAgent(LiveAgentConfig(api_key=keys[-1], system_instruction="You are a customer in a meeting. Read the given line aloud as yourself."), c)
    assert v.start(); v.say(text, mode="verbatim"); time.sleep(0.5); v.close()
    return resample_pcm16(bytes(c.buf), 24000, 16000)

bridge = Bridge()
from navigator.meeting import audio_bridge as _ab; _ab.ACTIVE = bridge
SPEECH = queue.Queue()
def feeder():
    while True:
        try: pcm = SPEECH.get_nowait()
        except queue.Empty: pcm = None
        if pcm is None:
            bridge.inbound.put(array.array("h", [random.randint(-60, 60) for _ in range(320)]).tobytes()); time.sleep(0.02); continue
        log("PROSPECT SPEAKS (injected)")
        for i in range(0, len(pcm), 640): bridge.inbound.put(pcm[i:i + 640]); time.sleep(0.02)
threading.Thread(target=feeder, daemon=True).start()
QS = {}
if A.questions:
    QS = {2: synth("Sorry, quick question. Does this dashboard update in real time?"),
          3: synth("Can you open the first chat in the inbox for me?"),
          4: synth("Wait, sorry, does this also work on mobile?"),
          5: synth("Can you open Local Seats?"),
          6: synth("Can you show me how to add a new contact here?"),
          9: synth("You create one."),
          11: synth("First open the phone book and create a contact step by step. Show me the screen.")}

ROWS = []
def sentences(t): return [x for x in re.split(r"(?<=[.!?\u0964])\s+", (t or "").strip()) if x.strip()]
def path(u): return (urlparse(u or "").path or "/").rstrip("/") or "/"

with sync_playwright() as p:
    b = p.chromium.launch(); ctx = b.new_context(**context_kwargs()); page = ctx.new_page()
    jobs = ui_jobs.UiJobs(); jobs.bind(page)
    live = LiveAgent(LiveAgentConfig(
        api_key=keys[0], language="en", fallback_keys=list(keys[1:]),
        system_instruction="You are Navigator, presenting a live product demo over Zoom. Read scripted lines when asked. Answer the prospect briefly." + ui_jobs.LIVE_TOOL_INSTRUCTION,
        tool_handler=jobs.handle_tool, tool_declarations=ui_jobs.TOOL_DECLARATIONS, ui_pump=jobs.pump,
        on_event=lambda e: e.kind == "heard" and log("heard:", e.text)), bridge)
    assert live.start(); live.set_director_only(False)
    orig_put = live._cmds.put
    def put(cmd, *a, **k):
        if getattr(cmd, "kind", "") == "say": EV.append(("piece", time.monotonic(), cmd.text))
        return orig_put(cmd, *a, **k)
    live._cmds.put = put

    CUR = {"step": None, "phase": "", "expect": "", "spoken": ""}
    SAMPLES = []   # (wall, url, heading)
    def snap():
        try:
            info = page.evaluate("() => ({u: location.href, h: (document.querySelector('h1,h2,h3')||{}).innerText||'', t: document.body.innerText.slice(0, 20000)})")
        except Exception:
            return
        SAMPLES.append((time.monotonic(), info["u"], info["h"][:60], info["t"]))
    def poll_until(done):
        while not done():
            jobs.pump(); snap(); time.sleep(0.2)
    def push_frame(): snap()

    def audit_line(text, wall0, step, phase, expect):
        """Map sentences onto the playback clock and check the sample at each start."""
        sents = sentences(text)
        starts = [w for k, w, _ in EV if k == "audio_start" and w >= wall0]
        if not starts:
            ROWS.append(dict(step=step, phase=phase, sentence=text[:60], url="-", expected=expect, ok=False, why="no audio")); return
        t0 = starts[0]; total = sum(len(s) for s in sents) or 1; acc = 0
        end = bridge.play_end
        for s in sents:
            at = t0 + (end - t0) * acc / total; acc += len(s)
            smp = min(SAMPLES, key=lambda x: abs(x[0] - at)) if SAMPLES else None
            if smp is None: continue
            u, h, txt = smp[1], smp[2], smp[3]
            why = []
            if expect and path(u) != expect and not path(u).startswith(expect + "/"):
                why.append(f"url {path(u)} != {expect}")
            if live.cfg.language == "en":
                for lab in NAV_LABELS:
                    if re.search(r"\b" + re.escape(lab) + r"\b", s) and lab.lower() not in txt.lower():
                        why.append(f"names '{lab}' not on screen"); break
            n = len(ROWS)
            page_shot = f"{A.out}/s{n:03d}_step{step}.png"
            ROWS.append(dict(step=step, phase=phase, sentence=s[:70], url=path(u), heading=h, expected=expect,
                             ok=not why, why="; ".join(why), shot=page_shot, at=round(at - t0, 1)))
        # screenshot at the line start for the record
        try: page.screenshot(path=f"{A.out}/line_step{step}_{phase}.png")
        except Exception: pass

    class Talk:
        last_spoken = ""; last_wait_timeout_at = 0.0
        def _hold(self):
            t = time.monotonic()
            while jobs.hold_active(live) and time.monotonic() - t < 30: jobs.pump(); snap(); time.sleep(0.05)
        def say(self, text, **kw):   # blocking (speak-then-act part)
            self._hold(); w0 = time.monotonic(); exp = path(page.url); snap()
            th = threading.Thread(target=live.say, args=(text,), kwargs={"mode": "natural"}, daemon=True); th.start()
            poll_until(lambda: not th.is_alive())
            CUR["pre"].append((text, w0, exp))
        def say_async(self, text):
            self._hold(); h = PlaybackHandle(); w0 = time.monotonic()
            def w():
                try: live.say(text, mode="natural")
                finally: h._finish()
            h._thread = threading.Thread(target=w, daemon=True); h._thread.start()
            CUR["post"].append((text, w0)); return h
        def set_language(self, l): live.set_language(l)

    deps = CallDeps(graph=graph, page=page, log=None, speaker=Talk(), product_id=A.product, push_frame=push_frame, live_agent=live)
    if A.email:
        ex.LOGIN_CREDS = (A.email, A.password)
    ctx.clear_cookies(); page.goto(graph.base_url, wait_until="domcontentloaded"); page.wait_for_timeout(2000); ex.STEP_URL = page.url
    handle = None
    for i in idx:
        call = steps[i]
        CUR.update(pre=[], post=[])
        if i in QS:
            threading.Timer(5.0, lambda pcm=QS[i]: SPEECH.put(pcm)).start()
        st = {"pending_calls": [call], "page_id": A.page, "walkthrough_flow_id": A.flow, "executing_step": i,
              "session_id": "audit", "narration": [call.spoken] if call.spoken else [], "pre_action_speech": handle}
        before = path(page.url)
        out = ex.executing(st, deps)
        handle = out.get("pre_action_speech")
        if handle is not None:
            poll_until(lambda: handle._done.is_set())   # SPEAKING joins the line
            handle = None
        after = ex.expected_path(graph, call) or path(page.url)
        for text, w0, exp in CUR["pre"]:
            audit_line(text, w0, i, "before-act", exp)
        for text, w0 in CUR["post"]:
            audit_line(text, w0, i, "after-act", after)
        log(f"step {i} {call.tool} {getattr(call,'selector','') or getattr(call,'page_id','')} {before} -> {path(page.url)}")
        # Q&A: let Live finish answering before the next step (hold in prod)
        poll_until(lambda: not jobs.hold_active(live) and not getattr(live, "answering", False))
    live.close(); b.close()

bad = [r for r in ROWS if not r["ok"]]
lines = ["| # | step | phase | at s | url | expected | ok | sentence | why |", "|---|---|---|---|---|---|---|---|---|"]
for n, r in enumerate(ROWS):
    lines.append(f"| {n} | {r['step']} | {r['phase']} | {r.get('at','')} | {r['url']} | {r['expected']} | {'OK' if r['ok'] else 'MISMATCH'} | {r['sentence']} | {r['why']} |")
open(f"{A.out}/audit.md", "w").write("\n".join(lines) + "\n")
open(f"{A.out}/audit_log.txt", "w").write("\n".join(LOG))
print("\n".join(lines))
print(f"SYNC AUDIT: sentences={len(ROWS)} mismatches={len(bad)}")
sys.exit(1 if bad else 0)
