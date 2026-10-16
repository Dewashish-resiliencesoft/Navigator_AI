"""Script-to-screen alignment.

Problem: editing the full script re-distributes *narration* across the
recorded steps, but each step keeps its recorded *action*. After an edit, step
3 says "Now let's open the Inbox" while it clicks Phonebook.

Fix (authoring time, cached in the draft so the live run adds no latency):
1. A nav inventory of the product (sidebar/menu links, incl. collapsed
   groups) is crawled once and stored in ``_meta.nav_inventory``.
2. Each step's narration is resolved to the screen it talks about
   (LLM over the inventory, exact-label heuristic as fallback).
3. A step whose recorded action does not lead there is rewritten to a
   ``navigate`` step to that screen. The original action is kept in
   ``_meta.step_sync`` so the author can see/undo it in Flows.
4. ``_meta.step_highlights`` names the control the line talks about; the
   runtime outlines it (never clicks it) before narrating.

Generic: no product names here; everything comes from the crawled inventory.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib.parse import urlparse

import yaml

DESTRUCTIVE = re.compile(r"\b(log\s*out|logout|sign\s*out|delete|remove|revoke|deactivate)\b", re.I)
LOGIN_WORDS = re.compile(r"\b(log\s*in|login|sign\s*in|password|landing page)\b", re.I)


# ------------------------------------------------------------------ inventory

_EXPAND_JS = """() => {
  const side = Array.from(document.querySelectorAll('aside, nav, [class*=sidebar i], [class*=sidenav i]'));
  const out = [];
  for (const root of side) for (const b of root.querySelectorAll('button,[role=button],[aria-expanded]')) {
    const t = (b.innerText || b.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ');
    if (!t || t.length > 40) continue;
    out.push(t);
  }
  return Array.from(new Set(out));
}"""

_LINKS_JS = """() => {
  const out = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const t = (a.innerText || a.getAttribute('aria-label') || a.title || '').trim().replace(/\\s+/g, ' ');
    const h = a.getAttribute('href') || '';
    let g = '';
    const grp = a.closest('li, div')?.parentElement?.closest('li, div');
    if (grp) { const b = grp.querySelector('button'); if (b) g = (b.innerText || '').trim().replace(/\\s+/g, ' '); }
    out.push({label: t.slice(0, 60), href: h, group: g.slice(0, 40)});
  }
  return out;
}"""


def collect_nav_inventory(page) -> list[dict[str, str]]:
    """Visible + collapsed-group nav links of the current (signed-in) page.

    Expands collapsible sidebar groups one by one. Never clicks anything that
    looks destructive (logout/delete)."""
    found: dict[str, dict[str, str]] = {}

    def grab() -> None:
        for item in page.evaluate(_LINKS_JS) or []:
            href = str(item.get("href") or "")
            label = str(item.get("label") or "")
            if not label or not href.startswith("/") or DESTRUCTIVE.search(label + " " + href):
                continue
            if re.search(r"\d", label) and len(label.split()) > 1 and label.split()[-1].isdigit():
                continue  # dashboard counters like 'Campaigns 116'
            key = href.split("#")[0]
            if key not in found or len(label) < len(found[key]["label"]):
                found[key] = {"label": label, "href": href, "group": str(item.get("group") or "")}

    grab()
    for t in page.evaluate(_EXPAND_JS) or []:
        if DESTRUCTIVE.search(t) or "@" in t or "theme" in t.lower() or "toggle" in t.lower():
            continue
        try:
            page.get_by_role("button", name=t, exact=True).first.click(timeout=1500)
            page.wait_for_timeout(250)
            grab()
        except Exception:  # noqa: BLE001
            continue
    return list(found.values())


def inventory_from_yaml(raw: dict[str, Any]) -> list[dict[str, str]]:
    inv = [dict(x) for x in ((raw.get("_meta") or {}).get("nav_inventory") or []) if isinstance(x, dict)]
    have = {urlparse(x["href"]).path.rstrip("/") for x in inv}
    for pid, p in (raw.get("pages") or {}).items():
        if not isinstance(p, dict):
            continue
        url = str(p.get("url") or "")
        path = urlparse(url).path.rstrip("/")
        if url and path and path not in have and not LOGIN_WORDS.search(pid + " " + url) \
                and not re.search(r"signup|forgot|about|contact|features", pid):
            inv.append({"label": str(p.get("name") or pid).strip(), "href": url, "group": ""})
            have.add(path)
    return inv


# ------------------------------------------------------------------ resolving

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9& ]+", " ", (s or "").lower()).strip()


def heuristic_target(text: str, inventory: list[dict[str, str]]) -> dict[str, str] | None:
    """Earliest inventory label (or its group name) mentioned in the line."""
    t = " " + _norm(text) + " "
    best: tuple[int, int, dict[str, str]] | None = None
    for item in inventory:
        for name in {item.get("label", ""), item.get("group", "")}:
            n = _norm(name)
            if len(n) < 3:
                continue
            m = re.search(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])", t)
            if m and (best is None or (m.start(), -len(n)) < (best[0], best[1])):
                best = (m.start(), -len(n), item)
    return dict(best[2], how="label") if best else None


_SYSTEM = (
    "You map demo narration lines to the product screen they talk about. "
    "Return ONLY JSON: a list with one object per line, in order: "
    '{"i": <line index>, "href": <href from the inventory or "">, '
    '"highlight": <exact visible text of the button/tab the line mentions on that screen, or "">}. '
    'Use "" for href when the line is about logging in, a greeting/closing on the current screen, '
    "or continues the previous screen. Never choose logout/delete."
)


def llm_targets(texts: list[str], inventory: list[dict[str, str]],
                llm: Callable[[str, str], str]) -> list[dict[str, str]] | None:
    inv = [{"label": x["label"], "group": x.get("group", ""), "href": x["href"]} for x in inventory]
    user = "INVENTORY:\n" + json.dumps(inv, ensure_ascii=False) + "\n\nLINES:\n" + "\n".join(
        f"{i}: {t}" for i, t in enumerate(texts))
    try:
        raw = llm(_SYSTEM, user)
        raw = raw[raw.find("["): raw.rfind("]") + 1]
        rows = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None
    hrefs = {x["href"]: x for x in inventory}
    out: list[dict[str, str]] = [{} for _ in texts]
    for r in rows if isinstance(rows, list) else []:
        try:
            i = int(r.get("i"))
        except Exception:  # noqa: BLE001
            continue
        if not 0 <= i < len(texts):
            continue
        h = str(r.get("href") or "")
        hl = str(r.get("highlight") or "")
        if DESTRUCTIVE.search(hl):
            hl = ""
        if h in hrefs:
            out[i] = {**hrefs[h], "highlight": hl, "how": "llm"}
        elif hl:
            out[i] = {"highlight": hl, "how": "llm"}
    return out


def resolve_targets(texts: list[str], inventory: list[dict[str, str]],
                    llm: Callable[[str, str], str] | None = None) -> list[dict[str, str]]:
    llm_rows = llm_targets(texts, inventory, llm) if (llm and inventory) else None
    out = []
    for i, text in enumerate(texts):
        row = (llm_rows[i] if llm_rows else {}) or {}
        if not row.get("href") and not LOGIN_WORDS.search(text or ""):
            h = heuristic_target(text, inventory)
            if h:
                row = {**h, "highlight": row.get("highlight", "")}
        out.append(row)
    return out


# ------------------------------------------------------------------ aligning

def _step_text_target(step: dict[str, Any], selectors: dict[str, Any]) -> str:
    sel = str(step.get("selector") or "")
    css = str(selectors.get(sel) or "")
    m = re.match(r'^text=["\']?(.*?)["\']?$', css)
    return _norm(m.group(1) if m else sel.replace("_", " "))


def align_flow(yaml_text: str, flow_id: str, *, inventory: list[dict[str, str]] | None = None,
               llm: Callable[[str, str], str] | None = None,
               targets: list[dict[str, str]] | None = None) -> tuple[str, list[dict[str, Any]]]:
    """Rewrite mismatched steps of ``flow_id`` to navigate where their narration
    points. Returns (new_yaml, report rows). Never publishes anything."""
    from navigator.knowledge.flow_authoring import _ensure_sel, _find_flow, _page_for_url

    raw = yaml.safe_load(yaml_text)
    page_id, steps = _find_flow(raw, flow_id)
    selectors = (raw["pages"][page_id].get("selectors") or {})
    inv = inventory if inventory is not None else inventory_from_yaml(raw)
    meta = raw.setdefault("_meta", {})
    if inventory is not None:
        meta["nav_inventory"] = inventory
    sync = meta.setdefault("step_sync", {})
    prev = {int(k): v for k, v in (sync.get(flow_id) or {}).items()}
    texts = [str(s.get("spoken") or "") for s in steps]
    targets = targets or resolve_targets(texts, inv, llm)
    report: list[dict[str, Any]] = []
    flow_sync: dict[str, Any] = {}
    highlights: dict[str, str] = {}
    for i, (step, tgt) in enumerate(zip(steps, targets)):
        href = (tgt or {}).get("href", "")
        label = (tgt or {}).get("label", "")
        hl = (tgt or {}).get("highlight", "")
        if hl:
            highlights[str(i)] = hl
        recorded = prev.get(i, {}).get("original") or {k: v for k, v in step.items() if k != "spoken"}
        action = "kept"
        if href:
            rec_txt = _step_text_target(recorded, selectors) if recorded.get("tool") == "click_element" else ""
            if rec_txt and rec_txt in {_norm(label), _norm(urlparse(href).path.strip("/").replace("-", " "))}:
                action = "kept (recorded click matches)"
            else:
                target_pid = _page_for_url(raw, href)
                tp = raw["pages"][target_pid]
                if label and str(tp.get("name", "")).lower() == target_pid.replace("_", " ").lower():
                    tp["name"] = label
                _ensure_sel(raw, target_pid, "body", "body")
                new = {"tool": "navigate", "page_id": target_pid,
                       "expects": {"check": "visible", "selector": "body", "timeout_ms": 8000}}
                if step.get("spoken"):
                    new["spoken"] = step["spoken"]
                steps[i] = new
                action = f"navigate -> {href}"
                flow_sync[str(i)] = {"original": recorded, "target": {"label": label, "href": href},
                                     "how": tgt.get("how", "")}
        elif i in prev and prev[i].get("original"):
            # Narration no longer points anywhere: restore the recorded action.
            steps[i] = {**prev[i]["original"], **({"spoken": step["spoken"]} if step.get("spoken") else {})}
            action = "restored recorded action"
        report.append({"step": i, "narration": texts[i][:90], "target": label, "href": href,
                       "highlight": hl, "action": action})
    sync[flow_id] = flow_sync
    meta.setdefault("step_highlights", {})[flow_id] = highlights
    return yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), report


def crawl_inventory(base_url: str, login: tuple[str, str, str] | None) -> list[dict[str, str]]:
    """Sign in (vault creds) and collect the nav inventory. ~10-20s."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 720})
            if login and login[1] and login[2]:
                from navigator.automation.browser.product_login import login_product

                login_product(page, url=login[0] or base_url, email=login[1], password=login[2])
            else:
                page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(2500)
            return collect_nav_inventory(page)
        finally:
            browser.close()
