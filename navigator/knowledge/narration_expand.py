"""Expand narration: authoring-time, screen-grounded enrichment of step lines.

Each step's one-liner becomes 2-3 sentences that describe what is actually
visible on that step's screen (headings, buttons, sections, tabs), crawled once
with the product's own login. Runs at authoring time and is saved into a DRAFT
revision, so the live call adds no latency. The author's original line is kept
in ``_meta.narration_original[flow][i]`` so Flows can show/undo it.

Rules for the LLM: stay faithful to the author's line (keep its meaning and
order), only mention things present in the screen facts, never invent
features, never read ids/urls/slugs. Generic: no product names in this module.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib.parse import urljoin

import yaml

_FACTS_JS = """() => {
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const txt = (el) => (el.innerText || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');
  // Side menu = a narrow aside/nav/sidebar ancestor. Some apps wrap the whole
  // page in a "sidebar-provider" div, so width decides, not the class alone.
  const inMenu = (el) => { for (let a = el.parentElement; a; a = a.parentElement) {
      const m = a.matches('aside, nav, [class*=sidebar i], [class*=sidenav i]');
      if (m && a.getBoundingClientRect().width < 420) return true; } return false; };
  const pick = (sel, n, max) => Array.from(new Set(Array.from(document.querySelectorAll(sel))
      .filter(e => vis(e) && !inMenu(e))
      .map(txt).filter(t => t && t.length <= max))).slice(0, n);
  return {
    title: document.title || '',
    headings: pick('h1,h2,h3,h4,[class*=title i]:not(title),[class*=heading i]', 14, 70),
    buttons: pick('button,[role=button],a[class*=btn i],input[type=submit]', 18, 40),
    tabs: pick('[role=tab],[class*=tab i] a,[class*=tab i] button', 10, 40),
    labels: pick('label,th,[class*=card i] h5,[class*=card i] h6,[class*=stat i]', 16, 50),
  };
}"""

_CHROME = re.compile(r"^(toggle\b|close$|menu$|more$|open\b)", re.I)
DESTRUCTIVE = re.compile(r"\b(log\s*out|logout|sign\s*out|delete|remove|revoke|deactivate)\b", re.I)


def step_screens(raw: dict[str, Any], flow_id: str) -> list[str]:
    """Screen URL (path) each step ends on: navigate target, aligned target, else previous."""
    from navigator.knowledge.flow_authoring import _find_flow

    page_id, steps = _find_flow(raw, flow_id)
    sync = ((raw.get("_meta") or {}).get("step_sync") or {}).get(flow_id) or {}
    cur = str(raw["pages"][page_id].get("url") or "/")
    out = []
    for i, st in enumerate(steps):
        tgt = (sync.get(str(i)) or {}).get("target") or {}
        if tgt.get("href"):
            cur = str(tgt["href"])
        elif st.get("tool") == "navigate" and st.get("page_id") in raw["pages"]:
            cur = str(raw["pages"][st["page_id"]].get("url") or cur)
        out.append(cur)
    return out


def crawl_screen_facts(base_url: str, paths: list[str],
                       login: tuple[str, str, str] | None) -> dict[str, dict[str, Any]]:
    """Visit each distinct screen once (read only, no clicks) and collect what's on it."""
    from playwright.sync_api import sync_playwright

    facts: dict[str, dict[str, Any]] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            if login and login[1] and login[2]:
                from navigator.automation.browser.product_login import login_product

                login_product(page, url=login[0] or base_url, email=login[1], password=login[2])
            for path in dict.fromkeys(paths):
                try:
                    page.goto(urljoin(base_url, path), wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(2000)
                    f = page.evaluate(_FACTS_JS)
                    f["buttons"] = [b for b in f.get("buttons", []) if len(b) > 2
                                    and not DESTRUCTIVE.search(b) and not _CHROME.search(b)]
                    facts[path] = f
                except Exception as exc:  # noqa: BLE001
                    facts[path] = {"error": str(exc)[:200]}
        finally:
            browser.close()
    return facts


_SYSTEM = """You enrich spoken narration for a live product demo given by a voice agent while the screen is shared.
For each step you get the author's ORIGINAL line and FACTS = text actually visible on that step's screen.
Write an ADDITION of 1-2 short spoken sentences (max ~35 words) that is appended AFTER the original, unchanged line.
- The original is kept verbatim; never restate, contradict or paraphrase it.
- Use ONLY things named in FACTS (headings, buttons, tabs, sections) that the original does not already mention.
- Never invent features, numbers or benefits. If FACTS add nothing new, return an empty string.
- Natural, conversational ("you can also see..."). No markdown, no URLs, no ids, no underscores. Write UI names in normal case, not ALL CAPS.
- Don't say "click"; say "you can". No quotation marks around names.
- Never mention counts, numbers, dates or other live data values (they change); describe the control instead.
- If the ORIGINAL describes a different view than FACTS (e.g. an editor or dialog not in FACTS), or it is a closing/thank-you line, return an empty string.
Return ONLY a JSON array: [{"i": <index>, "add": "<addition or empty>"}] covering every index."""

_SLUGGY = re.compile(r"[_/]|https?:|\b[0-9a-f]{12,}\b", re.I)


MAX_BASE_WORDS = 70  # already-rich lines are left alone (no live latency, no bloat)


def _decaps(t: str) -> str:
    return re.sub(r"\b[A-Z]{3,}\b", lambda m: m.group(0).capitalize() if m.group(0) not in {"API", "CRM", "SMS", "OTP", "URL", "CSV"} else m.group(0), t)


def _clean(text: str) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    t = re.sub(r"[\"\u201c\u201d]|(?<!\w)'|'(?!\w)", "", t)
    return t


def expand_flow(yaml_text: str, flow_id: str, *, facts: dict[str, dict[str, Any]] | None,
                llm: Callable[[str, str], str]) -> tuple[str, list[dict[str, Any]]]:
    """Return (new_yaml, report). Steps without narration stay silent; a step whose
    LLM output fails validation keeps its original line."""
    from navigator.knowledge.flow_authoring import _find_flow

    raw = yaml.safe_load(yaml_text)
    _page, steps = _find_flow(raw, flow_id)
    meta = raw.setdefault("_meta", {})
    if facts:
        meta.setdefault("screen_facts", {}).update(facts)
    facts = meta.get("screen_facts") or {}
    orig_map = meta.setdefault("narration_original", {}).setdefault(flow_id, {})
    screens = step_screens(raw, flow_id)
    items = []
    for i, st in enumerate(steps):
        base = orig_map.get(str(i)) or str(st.get("spoken") or "")
        if not base.strip():
            continue
        f = facts.get(screens[i]) or {}
        items.append({"i": i, "original": base,
                      "facts": {k: f.get(k) for k in ("headings", "tabs", "buttons", "labels") if f.get(k)}})
    rows: dict[int, str] = {}
    for start in range(0, len(items), 10):
        chunk = items[start:start + 10]
        try:
            out = llm(_SYSTEM, json.dumps(chunk, ensure_ascii=False))
            out = out[out.find("["): out.rfind("]") + 1]
            for r in json.loads(out):
                rows[int(r["i"])] = _clean(r.get("add", ""))
        except Exception as exc:  # noqa: BLE001
            print(f"[expand] chunk {start} failed: {exc}", flush=True)
    report = []
    for it in items:
        i, base = it["i"], it["original"]
        add = _decaps(rows.get(i, ""))
        closing = bool(re.search(r"thank you|thanks for|that.s the complete", base, re.I))
        ok = bool(add) and not closing and not re.search(r"\d", add) and not _SLUGGY.search(add) and len(add.split()) <= 45 \
            and len(base.split()) <= MAX_BASE_WORDS
        new = f"{base.rstrip()} {add}" if ok else base
        if ok:
            orig_map[str(i)] = base
            steps[i]["spoken"] = new
        report.append({"step": i, "before": base, "after": new if ok else base,
                       "changed": ok and new != base, "screen": screens[i]})
    return yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), report
