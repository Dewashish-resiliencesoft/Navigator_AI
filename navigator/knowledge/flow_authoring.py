"""Flow authoring helpers: step names, full-script redistribution, AI drafts.

Everything here writes to the DRAFT site graph only (publish=False).
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Callable

import yaml

# ---------------------------------------------------------------- step names


def step_names(yaml_text: str) -> dict[str, dict[int, str]]:
    raw = yaml.safe_load(yaml_text) or {}
    names = ((raw.get("_meta") or {}).get("step_names") or {}) if isinstance(raw, dict) else {}
    out: dict[str, dict[int, str]] = {}
    for fid, m in (names or {}).items():
        if isinstance(m, dict):
            out[str(fid)] = {int(k): str(v) for k, v in m.items() if str(v).strip()}
    return out


def set_step_name(yaml_text: str, *, flow_id: str, step_index: int, name: str) -> str:
    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        raise ValueError("site graph must be a mapping")
    found = any(
        isinstance(p, dict) and isinstance((p.get("flows") or {}).get(flow_id), list)
        and 0 <= step_index < len(p["flows"][flow_id])
        for p in (raw.get("pages") or {}).values()
    )
    if not found:
        raise ValueError(f"no step {step_index} in flow {flow_id!r}")
    meta = raw.setdefault("_meta", {})
    bucket = meta.setdefault("step_names", {}).setdefault(flow_id, {})
    name = (name or "").strip()[:80]
    if name:
        bucket[int(step_index)] = name
    else:
        bucket.pop(int(step_index), None)
    return yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)


def attach_step_names(beats: list[dict[str, Any]], yaml_text: str) -> list[dict[str, Any]]:
    names = step_names(yaml_text)
    for b in beats:
        fid, idx = b.get("flow_id"), b.get("step_index")
        if fid and idx is not None and int(idx) in names.get(str(fid), {}):
            b["step_name"] = names[str(fid)][int(idx)]
    return beats


# ------------------------------------------------------ script redistribution

_SENT = re.compile(r"(?<=[.!?।])\s+")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _by_markers(text: str, steps: list[dict[str, Any]]) -> list[str] | None:
    """'Step 3: …' / '3. …' / '<step name>: …' markers at line start."""
    n = len(steps)
    out = [""] * n
    names = {_norm(s.get("label") or ""): i for i, s in enumerate(steps) if _norm(s.get("label") or "")}
    cur = None
    hit = 0
    for line in (text or "").splitlines():
        m = re.match(r"^\s*(?:step\s*)?(\d{1,2})\s*[:.)\-–]\s*(.*)$", line, re.I)
        idx = None
        body = line
        if m and 1 <= int(m.group(1)) <= n:
            idx, body = int(m.group(1)) - 1, m.group(2)
        else:
            m2 = re.match(r"^\s*([^:]{2,60}):\s*(.*)$", line)
            if m2 and _norm(m2.group(1)) in names:
                idx, body = names[_norm(m2.group(1))], m2.group(2)
        if idx is not None:
            cur = idx
            hit += 1
            if _is_title(body):
                body = ""  # 'Step 2: Login' then text on next lines: the title is a name
        if cur is not None and body.strip():
            out[cur] = (out[cur] + " " + body.strip()).strip()
    return out if hit >= 2 else None


def _proportional(text: str, steps: list[dict[str, Any]]) -> list[str]:
    sents = [s.strip() for s in _SENT.split(" ".join((text or "").split())) if s.strip()]
    n = len(steps)
    if not sents or not n:
        return [""] * n
    # Weight by previous narration length so long steps keep long text.
    w = [max(1, len((s.get("old") or "").split())) for s in steps]
    total = float(sum(w))
    out, si = [], 0
    for i in range(n):
        if i == n - 1:
            take = len(sents) - si
        else:
            take = round(len(sents) * w[i] / total)
            take = max(0, min(take, len(sents) - si - (0 if i == n - 1 else 0)))
        out.append(" ".join(sents[si:si + take]))
        si += take
    if si < len(sents):
        out[-1] = (out[-1] + " " + " ".join(sents[si:])).strip()
    return out


def _by_llm(text: str, steps: list[dict[str, Any]], llm: Callable[[str, str], str]) -> list[str] | None:
    listing = "\n".join(
        f"{i + 1}. {s.get('label') or ''} | screen: {s.get('on_screen') or ''} | previous: {(s.get('old') or '')[:120]}"
        for i, s in enumerate(steps)
    )
    system = ("You split a product demo narration script across ordered demo steps. Keep the "
              "author's words verbatim and in order; do not invent text. A step may get empty text.")
    user = (f"Steps:\n{listing}\n\nScript:\n{text}\n\nReturn ONLY JSON: an array of {len(steps)} "
            "strings, element i = narration for step i+1.")
    raw = llm(system, user)
    m = re.search(r"\[.*\]", raw or "", re.S)
    if not m:
        return None
    arr = json.loads(m.group(0))
    if not isinstance(arr, list) or len(arr) != len(steps):
        return None
    return [str(x or "").strip() for x in arr]


def distribute_script(
    text: str, steps: list[dict[str, Any]], llm: Callable[[str, str], str] | None = None
) -> tuple[list[str], str]:
    """Return (per-step narration, method)."""
    n = len(steps)
    if not n:
        return [], "none"
    if not (text or "").strip():
        return [""] * n, "empty"
    marked = _by_markers(text, steps)
    if marked is not None:
        return marked, "markers"
    paras = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if len(paras) == n:
        return [" ".join(p.split()) for p in paras], "paragraphs"
    if llm is not None:
        try:
            got = _by_llm(text, steps, llm)
            if got is not None:
                return got, "llm"
        except Exception as exc:  # noqa: BLE001
            print(f"[flows] script LLM split failed: {exc}", flush=True)
    return _proportional(text, steps), "proportional"


def default_llm() -> Callable[[str, str], str] | None:
    try:
        from navigator.agent.providers import GeminiProvider  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        GeminiProvider = None  # type: ignore[assignment]
    try:
        from navigator.core.gemini_keys import gemini_key_candidates
        from navigator.core.settings import settings

        keys = gemini_key_candidates()
        if not keys:
            return None
        from navigator.core.gemini_keys import normalize_gemini_model

        model = normalize_gemini_model(
            getattr(settings, "gemini_text_model", "") or getattr(settings, "brain_reasoning_model", "") or "gemini-3.6-flash")

        def _call(system: str, user: str) -> str:
            from google import genai
            from google.genai import types

            last: Exception | None = None
            for k in keys:
                try:
                    client = genai.Client(api_key=k)  # keep a ref: a GC'd client closes mid-call
                    r = client.models.generate_content(
                        model=model, contents=user,
                        config=types.GenerateContentConfig(system_instruction=system, temperature=0.2),
                    )
                    return (r.text or "").strip()
                except Exception as exc:  # noqa: BLE001
                    last = exc
            raise last or RuntimeError("no gemini key")

        return _call
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------ AI draft flows

_CONTROLS_JS = None


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")[:40] or "flow"


class AiFlowJob:
    def __init__(self, product_id: str) -> None:
        self.product_id = product_id
        self.state = "running"
        self.progress: list[str] = []
        self.result: dict[str, Any] = {}
        self.error = ""
        self.started = time.time()

    def log(self, msg: str) -> None:
        self.progress.append(msg)
        print(f"[flows] ai-draft {self.product_id}: {msg}", flush=True)

    def view(self) -> dict[str, Any]:
        return {"state": self.state, "progress": self.progress[-30:], "result": self.result,
                "error": self.error, "elapsed_s": round(time.time() - self.started, 1)}


_jobs: dict[str, AiFlowJob] = {}
_jobs_lock = threading.Lock()


def ai_job(product_id: str) -> AiFlowJob | None:
    return _jobs.get(product_id)


def plan_tour(controls: list[str], brief: str, site: str, llm: Callable[[str, str], str] | None,
              max_steps: int = 6) -> dict[str, Any]:
    """LLM picks an ordered tour from REAL visible controls; fallback = nav order."""
    skip = re.compile(r"log\s*-?out|sign\s*-?out|delete|remove|login|sign\s*-?in|sign\s*-?up|register", re.I)
    cands = [c for c in controls if not skip.search(c)][:40]
    if llm is not None and cands:
        try:
            system = ("You design a short, natural product demo tour. Use ONLY controls from the list, "
                      "exact text. Narration: 1-2 friendly sentences per step, benefit-focused.")
            user = (f"Product: {site}\nProduct notes:\n{(brief or '')[:3000]}\n\nVisible controls:\n"
                    + "\n".join(f"- {c}" for c in cands)
                    + f"\n\nReturn ONLY JSON: {{\"name\": str, \"steps\": [{{\"control\": str, \"name\": str, "
                      f"\"narration\": str}}]}} with 3-{max_steps} steps.")
            raw = llm(system, user)
            m = re.search(r"\{.*\}", raw or "", re.S)
            data = json.loads(m.group(0)) if m else {}
            steps = [s for s in (data.get("steps") or []) if isinstance(s, dict) and s.get("control") in cands]
            if steps:
                return {"name": str(data.get("name") or "AI tour")[:60], "steps": steps[:max_steps]}
        except Exception as exc:  # noqa: BLE001
            print(f"[flows] ai-draft LLM plan failed: {exc}", flush=True)
    return {"name": "AI tour", "steps": [
        {"control": c, "name": c, "narration": f"Here's the {c} section."} for c in cands[:max_steps]]}


def start_ai_flow_job(*, product_id: str, base_url: str, login: tuple[str, str, str] | None,
                      brief: str, site: str, save: Callable[[dict[str, Any]], dict[str, Any]]) -> AiFlowJob:
    with _jobs_lock:
        cur = _jobs.get(product_id)
        if cur is not None and cur.state == "running":
            raise RuntimeError("AI draft already running for this product")
        job = AiFlowJob(product_id)
        _jobs[product_id] = job

    def _run() -> None:
        try:
            from playwright.sync_api import sync_playwright

            from navigator.meeting.ui_jobs import _find, visible_controls

            with sync_playwright() as pw:
                b = pw.chromium.launch(headless=True)
                page = b.new_context(viewport={"width": 1280, "height": 720}).new_page()
                if login and login[1] and login[2]:
                    job.log("signing in with saved demo credentials")
                    from navigator.automation.browser.product_login import login_product

                    login_product(page, url=login[0], email=login[1], password=login[2])
                else:
                    job.log(f"opening {base_url}")
                    page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
                start_path = re.sub(r"^https?://[^/]+", "", page.url or "") or "/"
                controls = visible_controls(page)
                job.log(f"found {len(controls)} visible controls on {start_path}")
                job.log("planning tour from product knowledge")
                plan = plan_tour(controls, brief, site, default_llm())
                verified = []
                for s in plan["steps"]:
                    label = str(s["control"])
                    el = _find(page, label)
                    if el is None:
                        job.log(f"skip '{label}': not visible now")
                        continue
                    try:
                        el.click(timeout=4000)
                        page.wait_for_timeout(1200)
                    except Exception as exc:  # noqa: BLE001
                        job.log(f"skip '{label}': click failed ({str(exc)[:60]})")
                        continue
                    verified.append({"label": label, "name": str(s.get("name") or label)[:80],
                                     "narration": str(s.get("narration") or "").strip(),
                                     "url": re.sub(r"^https?://[^/]+", "", page.url or "")})
                    job.log(f"verified step '{label}' -> {verified[-1]['url']}")
                b.close()
            if not verified:
                raise RuntimeError("no tour step could be verified on the live site")
            job.log("saving as draft flow (not published)")
            job.result = save({"name": plan["name"], "start_path": start_path, "steps": verified})
            job.state = "done"
            job.log(f"done: draft flow '{job.result.get('flow_id')}' with {len(verified)} steps")
        except Exception as exc:  # noqa: BLE001
            job.error = str(exc)[:300]
            job.state = "error"
            job.log(f"failed: {job.error}")

    threading.Thread(target=_run, name="ai-flow-draft", daemon=True).start()
    return job


def add_ai_flow_to_yaml(yaml_text: str, draft: dict[str, Any]) -> tuple[str, str, str]:
    """Insert generated steps as a new flow on the start page + playlist entry."""
    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        raise ValueError("site graph must be a mapping")
    pages = raw.setdefault("pages", {})
    start = draft.get("start_path") or "/"
    page_id = next((pid for pid, p in pages.items() if isinstance(p, dict)
                    and (p.get("url") or "").rstrip("/") == start.rstrip("/")), None)
    if page_id is None:
        page_id = next(iter(pages), "home")
        pages.setdefault(page_id, {"url": start, "flows": {}})
    base = "ai_" + _slug(draft.get("name") or "tour")
    existing = {f for p in pages.values() if isinstance(p, dict) for f in (p.get("flows") or {})}
    flow_id, n = base, 2
    while flow_id in existing:
        flow_id, n = f"{base}_{n}", n + 1
    steps = []
    for s in draft["steps"]:
        steps.append({
            "tool": "click_element",
            "selector": f'text="{s["label"]}"',
            "expects": {"check": "visible", "selector": "body", "timeout_ms": 3000},
            "spoken": s.get("narration") or None,
        })
    pages[page_id].setdefault("flows", {})[flow_id] = steps
    pl = raw.setdefault("demo_playlist", []) or []
    raw["demo_playlist"] = pl
    pl.append({"order": max([int(x.get("order") or 0) for x in pl] or [0]) + 1,
               "name": f"AI draft · {draft.get('name') or 'tour'}", "page_id": page_id, "flow_id": flow_id})
    meta = raw.setdefault("_meta", {})
    meta.setdefault("step_names", {})[flow_id] = {i: s["name"] for i, s in enumerate(draft["steps"])}
    meta.setdefault("ai_drafts", {})[flow_id] = {"created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                                 "needs_review": True}
    return yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), flow_id, page_id


# ------------------------------------------- named segments + step-name proposals

_STEP_MARK = re.compile(r"^\s*(?:step\s*)?(\d{1,2})\s*[:.)\-–]\s*(.*)$", re.I)
_HEAD_MARK = re.compile(r"^\s*#{1,6}\s+(.+?)\s*#*\s*$")
_DASH = re.compile(r"\s+[—–]\s+|\s+-\s+|:\s+")


def _split_name(body: str) -> tuple[str | None, str]:
    """'Inbox — text…' -> ('Inbox', 'text…') when the head is short title-ish."""
    parts = _DASH.split(body, maxsplit=1)
    if len(parts) == 2:
        head, rest = parts[0].strip(), parts[1].strip()
        if 0 < len(head.split()) <= 5 and len(head) <= 40 and not re.search(r"[.!?]$", head) and rest:
            return head, rest
    return None, body.strip()


def _is_title(line: str) -> bool:
    t = (line or "").strip()
    return bool(t) and len(t.split()) <= 7 and len(t) <= 70 and not re.search(r"[.!?…:;]$", t)


_STARTERS = set("""Here This Now In To Next For Let’s Let's Finally The We Our You Click From On Once When
After Then It There These Those First Second Third Lastly Below Above With Using So And That Next,""".split())


def strip_leading_title(text: str, known: str = "") -> tuple[str, str]:
    """'Login Here we choose…' -> ('Login', 'Here we choose…'). ('', text) when unsure.

    Repairs narration where a marker title got glued onto the spoken line.
    """
    t = (text or "").strip()
    if known and t.lower().startswith(known.lower() + " ") and len(t) > len(known) + 3:
        return known, t[len(known):].strip()
    toks = t.split(" ")
    if len(toks) < 3:
        return "", t
    cut = None
    first = re.sub(r"[^\w&]", "", toks[0])
    for k in range(1, min(15, len(toks) - 1)):
        prev, tok = toks[k - 1], toks[k]
        if re.search(r"[.!?…:;]$", prev):
            return "", t  # a real sentence ended before any title boundary
        word = re.sub(r"[^\w’']", "", tok)
        if not word or not word[0].isupper():
            if prev.endswith(")") and word and word[0].isupper():
                cut = k
                break
            continue
        if word in _STARTERS or (first and re.sub(r"[^\w&]", "", tok) == first) or prev.endswith(")"):
            cut = k
            break
    if not cut:
        return "", t
    title = " ".join(toks[:cut]).strip(" ,")
    body = " ".join(toks[cut:]).strip()
    if not _is_title(re.sub(r"\(.*?\)", "", title).strip() or title) or len(body.split()) < 2:
        return "", t
    return title, body


def extract_segments(text: str) -> list[dict[str, Any]]:
    """Script -> ordered [{name|None, text}] from 'Step N: Name — …', 'N. …', '## Name', or paragraphs."""
    segs: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    marked = False
    for line in (text or "").splitlines():
        h = _HEAD_MARK.match(line)
        m = _STEP_MARK.match(line)
        if h:
            cur = {"name": h.group(1).strip()[:60], "text": ""}
            segs.append(cur)
            marked = True
            continue
        if m:
            name, body = _split_name(m.group(2))
            if name is None and _is_title(body):
                name, body = body.strip(), ""
            cur = {"name": name, "text": body}
            segs.append(cur)
            marked = True
            continue
        if cur is not None and line.strip():
            cur["text"] = (cur["text"] + " " + line.strip()).strip()
    if marked:
        return [s for s in segs if s["text"] or s["name"]]
    out = []
    for p in re.split(r"\n\s*\n", (text or "").strip()):
        lines = [ln.strip() for ln in p.splitlines() if ln.strip()]
        if not lines:
            continue
        if len(lines) > 1 and _is_title(lines[0]):
            out.append({"name": lines[0], "text": " ".join(" ".join(lines[1:]).split())})
        else:
            out.append({"name": None, "text": " ".join(" ".join(lines).split())})
    return out


_STOP = set("""a an the this that these those here there we you our your i it its is are was were be been
to of in on at for with and or but so then now just also can will let lets let's go going click clicking
see shows showing show all any some my me us from into by as up down out about over get got this page
screen which what when where who how very really simply okay ok hello hi welcome""".split())


def heuristic_title(text: str, fallback: str = "") -> str:
    words = re.findall(r"[A-Za-z][A-Za-z0-9'\-]+", text or "")
    keep: list[str] = []
    for w in words:
        lw = w.lower().strip("'")
        if lw in _STOP or len(lw) < 3 or lw in (k.lower() for k in keep):
            continue
        keep.append(w)
        if len(keep) == 3:
            break
    if not keep:
        return (fallback or "Narration")[:40]
    t = " ".join(keep[:3])
    return t[0].upper() + t[1:]


def propose_step_names(texts: list[str], llm: Callable[[str, str], str] | None = None,
                       fallbacks: list[str] | None = None) -> list[str]:
    fb = fallbacks or [""] * len(texts)
    out = [""] * len(texts)
    todo = [i for i, t in enumerate(texts) if (t or "").strip()]
    if llm is not None and todo:
        try:
            listing = "\n".join(f"{n + 1}. {texts[i][:300]}" for n, i in enumerate(todo))
            raw = llm("You title product-demo steps. Each title is 2-4 words, Title case, no punctuation, "
                      "describes what is shown (e.g. 'Inbox overview').",
                      f"Segments:\n{listing}\n\nReturn ONLY a JSON array of {len(todo)} strings.")
            m = re.search(r"\[.*\]", raw or "", re.S)
            arr = json.loads(m.group(0)) if m else None
            if isinstance(arr, list) and len(arr) == len(todo):
                for i, t in zip(todo, arr):
                    t = " ".join(str(t or "").split()[:4]).strip(" .")
                    if t:
                        out[i] = t[:40]
        except Exception as exc:  # noqa: BLE001
            print(f"[flows] step-name LLM failed: {exc}", flush=True)
    return [o or (heuristic_title(texts[i], fb[i]) if (texts[i] or "").strip() else fb[i]) for i, o in enumerate(out)]


# ------------------------------------------------------- structural step edits

STEP_KINDS = ("navigate", "click", "wait", "narration")
_TIMELINE_KEYS = ("step_timing", "step_clicks", "step_speech")


def _find_flow(raw: dict[str, Any], flow_id: str) -> tuple[str, list]:
    for pid, p in (raw.get("pages") or {}).items():
        if isinstance(p, dict) and isinstance((p.get("flows") or {}).get(flow_id), list):
            return pid, p["flows"][flow_id]
    raise ValueError(f"no flow {flow_id!r}")


def _page_for_url(raw: dict[str, Any], url: str) -> str:
    from urllib.parse import urlparse

    url = (url or "").strip()
    if not url:
        raise ValueError("navigate step needs a URL")
    path = urlparse(url).path.rstrip("/") or "/"
    pages = raw.setdefault("pages", {})
    for pid, p in pages.items():
        if isinstance(p, dict) and (urlparse(str(p.get("url") or "")).path.rstrip("/") or "/") == path:
            return pid
    base = re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_") or "home"
    pid, n = f"page_{base}"[:40], 2
    while pid in pages:
        pid, n = f"page_{base}_{n}"[:44], n + 1
    pages[pid] = {"name": base.replace("_", " ").title(), "url": url, "selectors": {}, "flows": {}}
    return pid


def _ensure_sel(raw: dict[str, Any], page_id: str, alias: str, css: str) -> str:
    page = raw["pages"][page_id]
    sels = page.get("selectors")
    if not isinstance(sels, dict):
        sels = page["selectors"] = {}
    sels.setdefault(alias, css)
    return alias


def _text_alias(raw: dict[str, Any], page_id: str, text: str) -> str:
    text = text[5:].strip().strip("'\"") if text.lower().startswith("text=") else text
    css = 'text="' + text.replace('"', "'") + '"'
    sels = raw["pages"][page_id].get("selectors") or {}
    for k, v in sels.items():
        if v == css:
            return k
    base = "txt_" + (re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:30] or "el")
    alias, n = base, 2
    while alias in sels and sels[alias] != css:
        alias, n = f"{base}_{n}", n + 1
    return _ensure_sel(raw, page_id, alias, css)


def build_step(raw: dict[str, Any], spec: dict[str, Any], page_id: str) -> dict[str, Any]:
    _ensure_sel(raw, page_id, "body", "body")
    kind = str(spec.get("kind") or "narration")
    value = str(spec.get("value") or "").strip()
    narration = str(spec.get("narration") or "").strip()
    exp = {"check": "visible", "selector": "body", "timeout_ms": 3000}
    if kind == "navigate":
        target = _page_for_url(raw, value)
        _ensure_sel(raw, target, "body", "body")
        step = {"tool": "navigate", "page_id": target, "expects": exp}
    elif kind == "click":
        if not value:
            raise ValueError("click step needs the element text")
        sel = _text_alias(raw, page_id, value)
        step = {"tool": "click_element", "selector": sel, "expects": dict(exp, timeout_ms=5000)}
    elif kind == "wait":
        if value:
            sel = _text_alias(raw, page_id, value)
            step = {"tool": "wait_for", "selector": sel, "timeout_ms": 15000, "expects": exp}
        else:
            step = {"tool": "wait_for", "selector": "body", "timeout_ms": 3000, "expects": exp}
    elif kind == "narration":
        step = {"tool": "wait_for", "selector": "body", "timeout_ms": 1000, "expects": dict(exp, timeout_ms=1000)}
    else:
        raise ValueError(f"unknown step kind {kind!r}; use one of {STEP_KINDS}")
    if narration:
        step["spoken"] = narration
    return step


def step_kind(step: dict[str, Any], selectors: dict[str, Any] | None = None) -> tuple[str, str]:
    """(kind, value) for the UI editor."""
    tool = step.get("tool")
    sel = str(step.get("selector") or "")
    if sel != "body" and str((selectors or {}).get(sel, "")).startswith("text="):
        sel = str(selectors[sel])
    txt = re.match(r'^text=["\']?(.*?)["\']?$', sel)
    if tool == "navigate":
        return "navigate", str(step.get("page_id") or "")
    if tool == "wait_for" and sel.strip().lower() == "body":
        return ("narration" if int(step.get("timeout_ms") or 0) <= 1000 else "wait"), ""
    if tool == "wait_for":
        return "wait", txt.group(1) if txt else sel
    if tool == "click_element":
        return "click", txt.group(1) if txt else sel
    return str(tool or ""), sel


def _remap(value: Any, mapping: dict[int, int | None]) -> Any:
    """Re-key per-step meta (lists of str, lists of {idx}, {int: v}) after a reorder."""
    if isinstance(value, list):
        if all(isinstance(x, dict) and "idx" in x for x in value):
            out = []
            for x in value:
                ni = mapping.get(int(x["idx"]))
                if ni is not None:
                    out.append({**x, "idx": ni})
            return sorted(out, key=lambda x: x["idx"])
        n = max([v for v in mapping.values() if v is not None] or [-1]) + 1
        res: list[Any] = [""] * n
        for oi, ni in mapping.items():
            if ni is not None and oi < len(value):
                res[ni] = value[oi]
        return res
    if isinstance(value, dict):
        if value and all(str(k).lstrip("-").isdigit() for k in value):
            return {mapping[int(k)]: v for k, v in value.items()
                    if int(k) in mapping and mapping[int(k)] is not None}
        if isinstance(value.get("steps"), list):
            return {**value, "steps": _remap(value["steps"], mapping)}
    return value


def _reindex_meta(raw: dict[str, Any], flow_id: str, mapping: dict[int, int | None]) -> None:
    meta = raw.setdefault("_meta", {})
    for key, section in list(meta.items()):
        if not isinstance(section, dict) or flow_id not in section:
            continue
        if key in _TIMELINE_KEYS:
            # Recorded click/speech timeline no longer matches the steps: fall back to step playback.
            section.pop(flow_id, None)
            continue
        if key in ("step_names", "narration_suggestions", "semantics"):
            section[flow_id] = _remap(section[flow_id], mapping)
    ds = meta.get("demo_script")
    beats = ((ds or {}).get("full_demo") or {}).get("beats") if isinstance(ds, dict) else None
    if isinstance(beats, list):
        kept = []
        for b in beats:
            if isinstance(b, dict) and b.get("flow_id") == flow_id and b.get("step_index") is not None:
                ni = mapping.get(int(b["step_index"]))
                if ni is None:
                    continue
                old_id = str(b.get("id") or "")
                b = {**b, "step_index": ni}
                if old_id.endswith(f"_{int(b.get('step_index', ni))}") or re.search(r"_\d+$", old_id):
                    b["id"] = re.sub(r"_\d+$", f"_{ni}", old_id)
            kept.append(b)
        ds["full_demo"]["beats"] = kept


def edit_flow_steps(yaml_text: str, *, flow_id: str, op: str, index: int,
                    spec: dict[str, Any] | None = None) -> tuple[str, int]:
    """op: insert (after index; -1 = at start) | update | delete | up | down. Returns (yaml, focus index)."""
    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        raise ValueError("site graph must be a mapping")
    page_id, steps = _find_flow(raw, flow_id)
    n = len(steps)
    order: list[int | None] = list(range(n))
    focus = index
    if op == "insert":
        if not (-1 <= index < n):
            raise ValueError("bad index")
        new = build_step(raw, spec or {}, page_id)
        order.insert(index + 1, None)
        focus = index + 1
    elif op == "update":
        if not (0 <= index < n):
            raise ValueError("bad index")
        steps[index] = {**build_step(raw, spec or {}, page_id),
                        **({"spoken": steps[index]["spoken"]} if "narration" not in (spec or {})
                           and steps[index].get("spoken") else {})}
    elif op == "delete":
        if not (0 <= index < n) or n <= 1:
            raise ValueError("cannot delete the only step" if n <= 1 else "bad index")
        order.pop(index)
        focus = max(0, index - 1)
    elif op in ("up", "down"):
        j = index - 1 if op == "up" else index + 1
        if not (0 <= index < n and 0 <= j < n):
            raise ValueError("cannot move further")
        order[index], order[j] = order[j], order[index]
        focus = j
    else:
        raise ValueError(f"unknown op {op!r}")
    if op != "update":
        new_steps = [steps[o] if o is not None else new for o in order]  # type: ignore[possibly-undefined]
        steps[:] = new_steps
        mapping = {o: ni for ni, o in enumerate(order) if o is not None}
        for oi in range(n):
            mapping.setdefault(oi, None)
        _reindex_meta(raw, flow_id, mapping)
    if op in ("insert", "update") and spec and str(spec.get("name") or "").strip():
        raw["_meta"].setdefault("step_names", {}).setdefault(flow_id, {})[focus] = str(spec["name"]).strip()[:80]
    return yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), focus


def append_narration_steps(yaml_text: str, *, flow_id: str, extras: list[dict[str, Any]]) -> str:
    for ex in extras:
        raw = yaml.safe_load(yaml_text)
        _pid, steps = _find_flow(raw, flow_id)
        yaml_text, _ = edit_flow_steps(yaml_text, flow_id=flow_id, op="insert", index=len(steps) - 1,
                                       spec={"kind": "narration", "narration": ex.get("text") or "",
                                             "name": ex.get("name") or ""})
    return yaml_text


def set_step_names_bulk(yaml_text: str, *, flow_id: str, names: dict[int, str]) -> str:
    for idx, name in names.items():
        yaml_text = set_step_name(yaml_text, flow_id=flow_id, step_index=int(idx), name=name)
    return yaml_text
