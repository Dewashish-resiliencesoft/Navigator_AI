"""Offline script-to-screen check: run an aligned flow with the real browser
tools, highlight per step, screenshot each screen, log narration vs URL.
Usage: sync_e2e.py <yaml> <flow_id> <outdir> (creds via NAVSYNC_EMAIL/NAVSYNC_PASSWORD)."""
import json, os, re, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright
from navigator.knowledge.site_graph import parse_site_graph
from navigator.automation.browser.tools import execute as run_tool
from navigator.meeting.ui_jobs import highlight_text

ypath, flow_id, out = sys.argv[1], sys.argv[2], Path(sys.argv[3]); out.mkdir(parents=True, exist_ok=True)
g = parse_site_graph(Path(ypath).read_text())
page_id = next(pid for pid, p in g.pages.items() if flow_id in p.flows)
steps = g.pages[page_id].flows[flow_id]
hls = (g.meta.get("step_highlights") or {}).get(flow_id) or {}
sync = (g.meta.get("step_sync") or {}).get(flow_id) or {}
rows = []
with sync_playwright() as p:
    b = p.chromium.launch(); pg = b.new_page(viewport={"width": 1280, "height": 720})
    pg.goto(g.base_url, wait_until="domcontentloaded"); pg.wait_for_timeout(1500)
    cur = page_id
    for i, call in enumerate(steps):
        t0 = time.time(); err = ""
        try:
            if i == 1:  # the live demo signs in here (vault creds)
                from navigator.automation.browser.product_login import login_product
                login_url = pg.url if "login" in pg.url else g.base_url.rstrip("/") + "/login/"
                login_product(pg, url=login_url, email=os.environ["NAVSYNC_EMAIL"], password=os.environ["NAVSYNC_PASSWORD"])
            else:
                _, cur = run_tool(pg, g, cur, call, on_frame=None)
        except Exception as exc:  # noqa: BLE001
            err = str(exc).splitlines()[0][:80]
        pg.wait_for_timeout(900)
        hl = hls.get(str(i), "")
        shown = highlight_text(pg, hl) if hl else None
        pg.wait_for_timeout(200)
        shot = out / f"step{i:02d}.png"; pg.screenshot(path=str(shot))
        h1 = ""
        try: h1 = (pg.locator("h1, h2").first.inner_text(timeout=800) or "").strip()[:40]
        except Exception: pass
        rows.append({"step": i, "narration": (call.spoken or "")[:70], "target": (sync.get(str(i)) or {}).get("target", {}).get("href", ""),
                     "url": re.sub(r"^https?://[^/]+", "", pg.url), "heading": h1, "highlight": hl, "highlight_shown": shown,
                     "error": err, "secs": round(time.time() - t0, 1)})
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    b.close()
json.dump(rows, open(out / "report.json", "w"), indent=1, ensure_ascii=False)
