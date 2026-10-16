"""Live tasks on the shared screen: 'show me X', 'how do I create Y', 'create Y with ...'.

Generic: forms and fields are discovered from the live DOM (labels, placeholders,
aria), spoken details are mapped to fields by name heuristics (LLM fallback),
navigation uses the crawled nav inventory. No product-specific code.

Safety: only create/add/edit-type submits. Delete/logout/send/broadcast/payment/
password/API-key/account-settings actions are shown and explained, never done.
A submit needs the prospect's confirmation (``confirmed``) or a follow-up
``confirm_submit`` call. Results are validated from the real DOM.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

BLOCKED = re.compile(
    r"\b(delete|remove|erase|log\s*-?\s*out|sign\s*-?\s*out|send|broadcast|blast|pay|payment|purchase|buy|"
    r"checkout|billing|subscribe|upgrade|password|api\s*key|token|regenerate|revoke|deactivate|disable|"
    r"disconnect|reset|transfer|withdraw|change\s+(email|plan|account)|account\s+settings)\b", re.I)
ALLOWED_SUBMIT = re.compile(r"\b(save|create|add|submit|done|confirm|update|ok|next|continue|apply)\b", re.I)
CANCELISH = re.compile(r"\b(cancel|close|back|discard|dismiss)\b", re.I)
OPEN_VERBS = re.compile(r"\b(create|add|new|\+)\b", re.I)
SINGLE_HINT = re.compile(r"\b(manual|single|one|add a|individual|form)\b", re.I)
BULK_HINT = re.compile(r"\b(bulk|import|csv|upload|batch|file)\b", re.I)

#: What the prospect actually said (Live transcripts). do_task 'do' may only type
#: values that appear here: 15:15 audit Live invented "John / 1234567890".
HEARD: list[str] = []


def unspoken_values(vals: dict[str, str], heard: list[str]) -> list[str]:
    """Keys whose value the prospect never said (digits compared without spaces)."""
    said = " ".join(heard or []).lower()
    said_digits = re.sub(r"\D", "", said)
    bad = []
    for k, v in (vals or {}).items():
        v = str(v or "").strip()
        if not v:
            continue
        d = re.sub(r"\D", "", v)
        if len(d) >= 5:
            ok = d[-8:] in said_digits
        else:
            toks = [t for t in re.findall(r"\w+", v.lower()) if len(t) > 1]
            ok = bool(toks) and all(t in said for t in toks)
        if not ok:
            bad.append(k)
    return bad


#: The form a do_task filled and is waiting to submit (one at a time per demo).
PENDING: dict[str, Any] = {}

_FIELDS_JS = r"""() => {
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const dlgs = Array.from(document.querySelectorAll('[role=dialog],[aria-modal=true],dialog[open]')).filter(vis);
  const root = dlgs.length ? dlgs[dlgs.length - 1] : (document.querySelector('main') || document.body);
  // ids are per-discovery: drop tags left on fields outside this form (e.g. a page search box)
  document.querySelectorAll('[data-nav-field]').forEach(e => { if (!root.contains(e)) e.removeAttribute('data-nav-field'); });
  const txt = (e) => (e && (e.innerText || e.textContent) || '').trim().replace(/\s+/g, ' ');
  const labelOf = (e) => {
    if (e.labels && e.labels[0] && txt(e.labels[0])) return txt(e.labels[0]);
    const al = e.getAttribute('aria-label'); if (al) return al;
    const lb = e.getAttribute('aria-labelledby');
    if (lb) { const t = lb.split(' ').map(id => txt(document.getElementById(id))).join(' ').trim(); if (t) return t; }
    let p = e.parentElement;
    for (let i = 0; i < 4 && p && p !== root.parentElement; i++, p = p.parentElement) {
      const l = p.querySelector('label'); if (l && txt(l) && !l.contains(e)) return txt(l);
    }
    return e.placeholder || e.name || '';
  };
  const out = [];
  const els = root.querySelectorAll('input,textarea,select,[contenteditable=true]');
  let i = 0;
  for (const e of els) {
    const t = (e.type || '').toLowerCase();
    if (['hidden','submit','button','checkbox','radio','file','image','reset'].includes(t) || !vis(e) || e.disabled || e.readOnly) continue;
    const id = 'navf' + (i++); e.setAttribute('data-nav-field', id);
    const label = labelOf(e).slice(0, 60);
    out.push({id, label, type: t || e.tagName.toLowerCase(), name: e.name || '', placeholder: e.placeholder || '',
      required: !!e.required || /\*\s*$/.test(label), value: t === 'password' ? '' : (e.value || '')});
  }
  const buttons = Array.from(root.querySelectorAll('button,[role=button],input[type=submit]')).filter(vis)
    .map(b => txt(b) || b.value || b.getAttribute('aria-label') || '').filter(Boolean).slice(0, 30);
  return {in_dialog: dlgs.length > 0, fields: out, buttons};
}"""

_SYN = {
    "first_name": ["first name", "given name", "first"],
    "last_name": ["last name", "surname", "family name", "last"],
    "name": ["full name", "name", "contact name", "first name"],
    "phone": ["mobile", "phone", "whatsapp", "number", "contact number", "tel"],
    "email": ["email", "e-mail", "mail"],
    "address": ["address", "notes", "location"],
    "company": ["company", "business", "organisation", "organization"],
}


def parse_details(details: Any) -> dict[str, str]:
    """'name: X, phone: Y' / JSON / dict -> {key: value}."""
    if isinstance(details, dict):
        return {str(k).strip().lower(): str(v).strip() for k, v in details.items() if str(v).strip()}
    text = str(details or "").strip()
    if not text:
        return {}
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return parse_details(obj)
    except Exception:  # noqa: BLE001
        pass
    out: dict[str, str] = {}
    for part in re.split(r"[;,\n](?=\s*[A-Za-z][A-Za-z _-]{1,30}\s*[:=])", text):
        m = re.match(r"\s*([A-Za-z][A-Za-z _-]{1,30})\s*[:=]\s*(.+)", part)
        if m:
            out[m.group(1).strip().lower()] = m.group(2).strip().strip(",;")
    return out


def _canon(key: str) -> str:
    k = key.lower().strip()
    for canon, syns in _SYN.items():
        if k == canon or k in syns or any(s == k for s in syns):
            return canon
    if "mail" in k:
        return "email"
    if any(w in k for w in ("phone", "mobile", "number", "whatsapp")):
        return "phone"
    if "first" in k:
        return "first_name"
    if "last" in k or "surname" in k:
        return "last_name"
    if "name" in k:
        return "name"
    return k


def map_details(fields: list[dict], details: dict[str, str],
                llm: Callable[[str, str], str] | None = None) -> dict[str, str]:
    """field id -> value. Splits a full name over first/last fields when needed."""
    vals = {_canon(k): v for k, v in details.items()}
    has_first = any(re.search(r"first", f["label"], re.I) for f in fields)
    if "name" in vals and has_first and "first_name" not in vals:
        parts = vals.pop("name").split()
        vals["first_name"] = parts[0]
        if len(parts) > 1:
            vals.setdefault("last_name", " ".join(parts[1:]))
    out: dict[str, str] = {}
    used: set[str] = set()
    for f in fields:
        lab = f"{f['label']} {f['placeholder']} {f['name']}".lower()
        for canon in ("first_name", "last_name", "email", "phone", "name", "address", "company"):
            if canon in vals and canon not in used and any(s in lab for s in _SYN.get(canon, [canon])):
                if canon == "name" and re.search(r"first|last|user\s*name|company|business", lab):
                    continue
                if canon == "phone" and f["type"] == "email":
                    continue
                out[f["id"]] = vals[canon]
                used.add(canon)
                break
    rest = {k: v for k, v in vals.items() if k not in used}
    free = [f for f in fields if f["id"] not in out]
    if rest and free and llm is not None:
        try:
            raw = llm("Map each detail to the best form field id. Return ONLY JSON {\"field_id\": \"value\"}. "
                      "Skip details that fit no field.",
                      json.dumps({"fields": [{k: f[k] for k in ("id", "label", "placeholder", "type")} for f in free],
                                  "details": rest}))
            obj = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
            ids = {f["id"] for f in free}
            out.update({k: str(v) for k, v in obj.items() if k in ids and str(v).strip()})
        except Exception as exc:  # noqa: BLE001
            print(f"[task] llm field map skipped: {exc}", flush=True)
    return out


# ------------------------------------------------------------------ screen helpers

def _push(push_frame):
    if push_frame is not None:
        try:
            push_frame()
        except Exception:  # noqa: BLE001
            pass


def _outline(page, el, color="#ffb020") -> None:
    try:
        page.evaluate("""() => document.querySelectorAll('[data-nav-hl]').forEach(e => {
            e.style.outline = e.dataset.navHl === '-' ? '' : e.dataset.navHl; e.removeAttribute('data-nav-hl'); })""")
        el.scroll_into_view_if_needed(timeout=1500)
        el.evaluate(f"e => {{ e.dataset.navHl = e.style.outline || '-'; e.style.outline = '3px solid {color}'; e.style.outlineOffset = '2px'; }}")
        el.hover(timeout=1500)
    except Exception:  # noqa: BLE001
        pass


def discover(page) -> dict:
    try:
        return page.evaluate(_FIELDS_JS) or {"fields": [], "buttons": [], "in_dialog": False}
    except Exception:  # noqa: BLE001
        return {"fields": [], "buttons": [], "in_dialog": False}


def _goal_words(goal: str) -> set[str]:
    stop = {"how", "do", "i", "a", "an", "the", "to", "me", "show", "please", "can", "you", "it", "my", "for", "new", "create", "add", "make"}
    return {w.rstrip("s") for w in re.findall(r"[a-z]+", goal.lower()) if w not in stop and len(w) > 2}


def _score(text: str, words: set[str]) -> int:
    t = {w.rstrip("s") for w in re.findall(r"[a-z]+", (text or "").lower())}
    return len(t & words) * 2 + (1 if OPEN_VERBS.search(text or "") else 0)


def _click_text(page, text: str, *, in_dialog: bool = False):
    from navigator.meeting.ui_jobs import _find_main

    scope = page.locator("[role=dialog],[aria-modal=true]").last if in_dialog else page
    pat = re.compile(r"^\s*" + re.escape(text.split("\n")[0].strip()), re.I)
    for loc in (scope.get_by_role("button", name=pat), scope.get_by_role("link", name=pat), scope.get_by_text(pat)):
        try:
            for i in range(min(loc.count(), 4)):
                el = loc.nth(i)
                if el.is_visible():
                    return el
        except Exception:  # noqa: BLE001
            continue
    return None if in_dialog else _find_main(page, text)


def _goal_control_here(page, words: set[str]) -> bool:
    nouns = [w for w in words if len(w) > 3 and w not in {"create", "make", "new", "show", "open", "how"}]
    for n in nouns:
        try:
            loc = page.get_by_role("button", name=re.compile(rf"\b{re.escape(n)}", re.I))
            for i in range(min(loc.count(), 4)):
                if loc.nth(i).is_visible() and OPEN_VERBS.search(loc.nth(i).inner_text() or ""):
                    return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _navigate_for(page, goal: str, push_frame) -> str:
    """Go to the screen the goal is about (nav inventory; LLM picks if needed)."""
    from urllib.parse import urljoin

    from navigator.meeting import ui_jobs

    inv = [r for r in (ui_jobs.NAV_INVENTORY or []) if str(r.get("href", "")).startswith("/")]
    if not inv:
        return ""
    words = _goal_words(goal)
    if _goal_control_here(page, words):
        # Already on the right screen (its own 'Create Contact' button is visible):
        # skip the menu LLM pick, which cost 11s on /phonebook in the 13:57 audit.
        return ""
    best = max(inv, key=lambda r: _score(r.get("label", "") + " " + r.get("href", "").replace("-", " "), words))
    if _score(best.get("label", "") + " " + best["href"].replace("-", " "), words) < 2:
        try:
            from navigator.knowledge.flow_authoring import default_llm

            llm = default_llm()
            if llm:
                raw = llm("Pick the ONE menu href where the user can do this task. Reply with the href only.",
                          json.dumps({"task": goal, "menu": [{"label": r["label"], "href": r["href"]} for r in inv]}))
                href = (re.search(r"/[\w\-/]*", raw or "") or [""])[0]
                best = next((r for r in inv if r["href"] == href), None) or {}
        except Exception:  # noqa: BLE001
            best = {}
    href = best.get("href", "")
    if not href:
        return ""
    if (page.url or "").rstrip("/").endswith(href.rstrip("/")):
        return href
    page.goto(urljoin(page.url, href), wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(1200)
    _push(push_frame)
    return href


def _open_form(page, goal: str, push_frame) -> list[str]:
    """Click the create/add control for the goal, then pick a single-entry option
    if a chooser dialog appears. Returns what was clicked."""
    steps: list[str] = []
    form = discover(page)
    if form["in_dialog"] and form["fields"]:
        return steps
    from navigator.meeting.ui_jobs import _IN_MENU_JS, visible_controls

    words = _goal_words(goal)
    cands = [c for c in visible_controls(page) if OPEN_VERBS.search(c) and not BLOCKED.search(c) and len(c) <= 30]
    cands.sort(key=lambda c: _score(c, words), reverse=True)
    for c in cands[:3]:
        el = _click_text(page, c)
        if el is None:
            continue
        try:
            if el.evaluate(_IN_MENU_JS):
                continue
        except Exception:  # noqa: BLE001
            pass
        _outline(page, el)
        _push(push_frame)
        page.wait_for_timeout(700)
        el.click(timeout=4000)
        page.wait_for_timeout(1000)
        steps.append(c)
        break
    for _ in range(2):  # chooser dialogs: "Bulk upload" vs "Manual entry"
        form = discover(page)
        if form["fields"] and any(f["type"] not in ("search",) and "search" not in f["label"].lower() for f in form["fields"]):
            if form["in_dialog"]:
                break
        opts = [b for b in form["buttons"] if not CANCELISH.search(b) and not BLOCKED.search(b)]
        pick = next((b for b in opts if SINGLE_HINT.search(b) and not BULK_HINT.search(b)), None)
        if not form["in_dialog"] or not pick:
            break
        el = _click_text(page, pick, in_dialog=True)
        if el is None:
            break
        _outline(page, el)
        _push(push_frame)
        page.wait_for_timeout(600)
        el.click(timeout=4000)
        page.wait_for_timeout(900)
        steps.append(pick.split("\n")[0])
    _push(push_frame)
    return steps


def _type_human(page, fid: str, value: str, push_frame) -> bool:
    el = page.locator(f"[data-nav-field='{fid}']").first
    try:
        _outline(page, el, "#2f80ed")
        try:
            el.click(timeout=4000)
        except Exception:  # noqa: BLE001  (dialog still animating / overlay label)
            el.focus()
        el.fill("")
        el.press_sequentially(value, delay=55)
        try:
            if el.input_value(timeout=1000).strip() != value.strip():
                el.fill(value)  # keystrokes were lost (field re-rendered): set it directly
        except Exception:  # noqa: BLE001
            pass
        _push(push_frame)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[task] type {fid} failed: {exc}", flush=True)
        return False


def _phone_value(field: dict, value: str, form_text: str) -> str:
    """A '+' country prefix next to the field means: type the national number."""
    digits = re.sub(r"[^\d+]", "", value)
    if field["type"] in ("tel", "text", "number") and re.search(r"mobile|phone|number", field["label"], re.I):
        if digits.startswith("+") and len(digits) > 11:
            return digits  # keep the full international number; most inputs accept it
    return value


_PREFIX_JS = r"""(el) => {
  const p = el.parentElement; if (!p) return '';
  return Array.from(p.children).filter(c => c !== el && !c.matches('input,textarea,select'))
    .map(c => (c.innerText || '').trim()).join(' ').slice(0, 20);
}"""


def _fit_prefix(page, field: dict, value: str) -> str:
    """Match what the input expects: a visible '+' adornment or an all-digits
    placeholder means type digits only (country code included, no '+')."""
    try:
        el = page.locator(f"[data-nav-field='{field['id']}']").first
        prefix = el.evaluate(_PREFIX_JS) or ""
    except Exception:  # noqa: BLE001
        prefix = ""
    ph = re.sub(r"\s", "", field.get("placeholder") or "")
    if value.startswith("+") and (prefix.strip() == "+" or re.fullmatch(r"\d{8,15}", ph)):
        return re.sub(r"\D", "", value)
    return value


_FORM_ERR_JS = r"""() => {
  const d = Array.from(document.querySelectorAll('[role=dialog],[aria-modal=true]')).pop() || document;
  return Array.from(d.querySelectorAll('[aria-invalid=true] ~ *, [class*=destructive], [class*=error i], [role=alert]'))
    .map(e => (e.innerText || '').trim()).filter(t => t && t.length < 160).slice(0, 4).join(' | ');
}"""


def _submit_button(page) -> tuple[Any, str]:
    form = discover(page)
    opts = [b for b in form["buttons"] if ALLOWED_SUBMIT.search(b) and not CANCELISH.search(b)
            and not SINGLE_HINT.search(b) and not BULK_HINT.search(b) and "\n" not in b.strip() and len(b) <= 30]
    _strong = re.compile(r"^\W*(save|submit|create|update|confirm|done)\b", re.I)
    opts.sort(key=lambda b: (not _strong.search(b), len(b)))
    for b in opts:
        if BLOCKED.search(b):
            return None, b
    for b in opts:
        el = _click_text(page, b, in_dialog=form["in_dialog"])
        if el is not None:
            return el, b
    return None, ""


def _validate(page, probe: str, before_dialog: bool) -> dict:
    page.wait_for_timeout(1800)
    toast = ""
    try:
        toast = page.evaluate("""() => Array.from(document.querySelectorAll('[role=status],[role=alert],[data-sonner-toast],[class*=toast i],[class*=notif i]'))
            .map(e => (e.innerText||'').trim()).filter(Boolean).join(' | ').slice(0, 200)""") or ""
    except Exception:  # noqa: BLE001
        pass
    form = discover(page)
    still_open = form["in_dialog"] and bool(form["fields"])
    found = False
    if probe:
        try:
            found = probe.lower() in (page.inner_text("body", timeout=3000) or "").lower()
        except Exception:  # noqa: BLE001
            pass
    err = bool(re.search(r"error|invalid|required|failed|already exists", toast, re.I))
    ok = (found or (bool(toast) and not err)) and not still_open
    form_errors = ""
    if still_open:
        try:
            form_errors = page.evaluate(_FORM_ERR_JS) or ""
        except Exception:  # noqa: BLE001
            pass
    return {"ok": ok, "toast": toast, "form_still_open": still_open, "new_item_visible": found,
            "form_errors": form_errors}


# ------------------------------------------------------------------ tools

def do_task(page, goal: str, details: Any = "", mode: str = "explain", confirmed: bool = False,
            push_frame: Callable[[], None] | None = None) -> dict:
    goal = (goal or "").strip()
    mode = (mode or "explain").lower()
    if BLOCKED.search(goal):
        from navigator.meeting.ui_jobs import highlight_text

        words = [w for w in re.findall(r"[A-Za-z]+", goal) if len(w) > 3]
        shown = next((w for w in words if highlight_text(page, w)), "")
        return {"ok": False, "refused": True,
                "say": "That action is not something I do in a live demo (it deletes, sends, pays or changes "
                       "account security). I can show where it is and explain it instead.",
                "highlighted": shown}
    t0 = time.monotonic()
    url0 = page.url
    href = ""
    form = discover(page)
    t_disc = time.monotonic() - t0
    if not (form["in_dialog"] and form["fields"]):
        href = _navigate_for(page, goal, push_frame)
    t_nav = time.monotonic() - t0
    opened = _open_form(page, goal, push_frame) if mode in ("explain", "do") else []
    print(f"[task] timing discover={t_disc:.1f}s navigate={t_nav - t_disc:.1f}s "
          f"open_form={time.monotonic() - t0 - t_nav:.1f}s opened={opened!r}", flush=True)
    if opened:
        page.wait_for_timeout(800)  # let a freshly opened form finish mounting
    form = discover(page)
    fields = [f for f in form["fields"] if "search" not in (f["label"] + f["placeholder"]).lower()]
    out: dict[str, Any] = {"ok": True, "mode": mode, "url_path": re.sub(r"^https?://[^/]+", "", page.url or ""),
                           "opened": opened, "fields": [{"label": f["label"], "required": f["required"]} for f in fields],
                           "screen_moved": page.url != url0}
    if not fields:
        out.update(ok=bool(href or opened), note="no form found on this screen; explain what is visible instead")
        return out
    if mode == "explain":
        # Highlights are paced to the narration by the screen thread after this returns.
        out["tour"] = [f["id"] for f in fields]
        _submit_hint = _submit_button(page)[1]
        out["submit_button"] = _submit_hint
        out["say_hint"] = "Explain each field in order as it lights up; do not submit."
        return out
    vals = parse_details(details)
    invented = unspoken_values(vals, HEARD) if HEARD is not None else []
    if invented:
        print(f"[task] refusing invented details for {invented} (not said by the prospect)", flush=True)
        vals = {k: v for k, v in vals.items() if k not in invented}
    if not vals:
        out.update(ok=False, status="need_details", missing=[f["label"].rstrip(" *") for f in fields if f["required"]],
                   say_hint="The form is open on screen. Ask the prospect for the details to type "
                            "(e.g. name and mobile). Never invent values.")
        return out
    try:
        from navigator.knowledge.flow_authoring import default_llm

        llm = default_llm()
    except Exception:  # noqa: BLE001
        llm = None
    mapping = map_details(fields, vals, llm)
    missing = [f["label"].rstrip(" *") for f in fields if f["required"] and f["id"] not in mapping and not f.get("value")]
    if missing:
        out.update(ok=False, status="need_details", missing=missing,
                   say_hint=f"Ask the prospect for: {', '.join(missing)}.")
        return out
    form_text = " ".join(f["label"] for f in fields)
    typed = []
    for f in fields:
        if f["id"] not in mapping:
            continue
        v = mapping[f["id"]]
        if re.search(r"mobile|phone", f["label"], re.I):
            v = _phone_value(f, v, form_text)
            v = _fit_prefix(page, f, v)
        if _type_human(page, f["id"], v, push_frame):
            typed.append({"label": f["label"].rstrip(" *"),
                          "value": "••••" if f["type"] == "password" else v})
    # Some forms reset fields right after they mount: re-check the real values
    # and retype anything that got wiped.
    page.wait_for_timeout(400)
    for f in fields:
        want = next((t for t in typed if t["label"] == f["label"].rstrip(" *")), None)
        if not want or f["type"] == "password":
            continue
        try:
            have = page.locator(f"[data-nav-field='{f['id']}']").first.input_value(timeout=1500)
        except Exception:  # noqa: BLE001
            continue
        if have.strip() != want["value"].strip():
            print(f"[task] {f['label']!r} was cleared by the page; retyping", flush=True)
            _type_human(page, f["id"], want["value"], push_frame)
    out["typed"] = typed
    probe = next((t["value"] for t in typed if re.search(r"first|name", t["label"], re.I)), "")
    PENDING.clear()
    PENDING.update({"goal": goal, "probe": probe, "at": time.monotonic()})
    btn, label = _submit_button(page)
    if btn is None:
        out.update(ok=False, status="no_safe_submit", submit_button=label,
                   say_hint="The form is filled but I won't press that button in a demo.")
        return out
    _outline(page, btn, "#27ae60")
    _push(push_frame)
    if not confirmed:
        out.update(status="awaiting_confirmation", submit_button=label,
                   say_hint=f"Read back the details and ask: shall I press {label}? If yes, call confirm_submit.")
        return out
    return {**out, **confirm_submit(page, push_frame)}


def confirm_submit(page, push_frame=None) -> dict:
    if not PENDING:
        return {"ok": False, "error": "nothing is waiting to be submitted"}
    btn, label = _submit_button(page)
    if btn is None:
        return {"ok": False, "error": f"no safe submit button ({label or 'none'})"}
    probe = PENDING.get("probe", "")
    try:
        btn.click(timeout=4000)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"could not press {label}: {exc}"[:200]}
    _push(push_frame)
    res = _validate(page, probe, True)
    PENDING.clear()
    res.update(status="submitted", pressed=label,
               say_hint=("Confirm it worked, pointing at the new entry." if res["ok"] else
                         "Say honestly that you could not confirm it saved, and what the screen shows."))
    _push(push_frame)
    return res


def fill_field(page, label: str, value: str, push_frame=None) -> dict:
    form = discover(page)
    want = (label or "").lower()
    f = next((x for x in form["fields"] if want and want in (x["label"] + " " + x["placeholder"]).lower()), None)
    if f is None:
        return {"ok": False, "error": f"no field '{label}'", "fields": [x["label"] for x in form["fields"]]}
    ok = _type_human(page, f["id"], value, push_frame)
    return {"ok": ok, "field": f["label"], "value": "••••" if f["type"] == "password" else value}


_ITEMS_JS = r"""(navLabels) => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 120 && r.height > 18 && r.bottom > 0 && r.top < innerHeight && s.visibility !== 'hidden'; };
  const nav = new Set(navLabels.map(t => t.toLowerCase()));
  // The app menu: smallest container holding >=4 entries named like menu items.
  const navEls = Array.from(document.querySelectorAll('a,button,li,span'))
    .filter(e => nav.has((e.innerText || '').trim().toLowerCase()));
  // Menu columns: the widest narrow (<35% of the viewport) ancestor of each menu entry.
  const menus = new Set();
  for (const n of navEls) {
    let top = null;
    for (let a = n.parentElement; a && a !== document.body; a = a.parentElement) {
      const w = a.getBoundingClientRect().width;
      if (w > 0 && w < 0.35 * innerWidth) top = a; else if (w >= 0.35 * innerWidth) break;
    }
    if (top) menus.add(top);
  }
  const inNav = e => !!e.closest('nav, header, [role=navigation], [role=menubar], [role=dialog]')
    || [...menus].some(m => m.contains(e) && navEls.filter(n => m.contains(n)).length >= 3);
  const groups = new Map();
  for (const e of document.querySelectorAll('body *')) {
    if (e.children.length === 0) continue;
    const tag = e.tagName;
    const clickable = /^(A|BUTTON|LI|TR)$/.test(tag) || ['listitem', 'option', 'row'].includes(e.getAttribute('role'))
      || e.hasAttribute('tabindex') || getComputedStyle(e).cursor === 'pointer';
    if (!clickable || !vis(e) || inNav(e)) continue;
    const t = (e.innerText || '').trim();
    if (!t || t.length > 400 || nav.has(t.toLowerCase()) || t.split(/\s+/).length < 2) continue;
    const key = tag + '|' + (typeof e.className === 'string' ? e.className : '');
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(e);
  }
  let best = [];
  for (const items of groups.values()) {
    // a list: several rows of the same kind, none nested in another
    const top = items.filter(i => !items.some(o => o !== i && o.contains(i)));
    if (top.length >= 2 && top.length > best.length) best = top;
  }
  best.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
  document.querySelectorAll('[data-nav-item]').forEach(e => e.removeAttribute('data-nav-item'));
  best.forEach((e, i) => e.setAttribute('data-nav-item', String(i)));
  return best.map(e => (e.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 80));
}"""

_ORDINALS = {"first": 0, "1st": 0, "top": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2,
             "fourth": 3, "fifth": 4, "last": -1, "latest": 0, "newest": 0}
_ITEM_HINT = re.compile(r"\b(first|second|third|fourth|fifth|last|top|latest|newest|1st|2nd|3rd|any|a|one)\b"
                        r".*\b(chat|conversation|message|contact|item|row|entry|record|card|lead|campaign|ticket|order)s?\b"
                        r"|\b(chat|conversation|contact|row|item)\b", re.I)


def open_item(page, which: str, push_frame=None) -> dict:
    """Open one entry of the list on screen: 'first chat', 'last contact', or a name."""
    from navigator.meeting import ui_jobs

    navl = [str(r.get("label", "")) for r in (ui_jobs.NAV_INVENTORY or []) if r.get("label")]
    try:
        texts = page.evaluate(_ITEMS_JS, navl) or []
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"list scan failed: {exc}"}
    if not texts:
        return {"ok": False, "error": "no list of items visible on this screen"}
    low = (which or "").lower()
    idx = None
    for w, i in _ORDINALS.items():
        if re.search(rf"\b{w}\b", low):
            idx = i if i >= 0 else len(texts) - 1
            break
    if idx is None:
        words = [w for w in re.findall(r"[a-z0-9@.+]+", low) if len(w) > 2 and w not in
                 {"chat", "contact", "open", "the", "with", "conversation", "item", "row", "click", "show"}]
        scored = sorted(((sum(w in t.lower() for w in words), i) for i, t in enumerate(texts)), reverse=True)
        idx = scored[0][1] if scored and scored[0][0] > 0 else (0 if not words else None)
    if idx is None or idx >= len(texts):
        return {"ok": False, "error": f"no item matching {which!r}", "items": texts[:8]}
    el = page.locator(f"[data-nav-item='{idx}']").first
    _outline(page, el)
    _push(push_frame)
    page.wait_for_timeout(500)
    el.click(timeout=4000)
    page.wait_for_timeout(900)
    _push(push_frame)
    return {"ok": True, "opened": texts[idx], "index": idx, "items_seen": len(texts),
            "url_path": re.sub(r"^https?://[^/]+", "", page.url or ""),
            "say_hint": "Describe what opened on screen. Never ask the prospect to click."}


def click_control(page, label: str, push_frame=None) -> dict:
    if BLOCKED.search(label or "") or (ALLOWED_SUBMIT.search(label or "") and not OPEN_VERBS.search(label or "")):
        from navigator.meeting.ui_jobs import highlight_text

        highlight_text(page, label)
        return {"ok": False, "refused": True,
                "say": "I'll only highlight that one; use confirm_submit for saving, and I never delete/send/pay."}
    el = None if re.match(r"^\s*(open\s+)?(the\s+)?(first|second|third|last|top|latest)\b", label or "", re.I) \
        else _click_text(page, label, in_dialog=discover(page)["in_dialog"])
    if el is None:
        # Not a button/tab label: maybe a list entry ("first chat", a contact's name).
        res = open_item(page, label, push_frame)
        if res.get("ok") or _ITEM_HINT.search(label or ""):
            return res
        return {"ok": False, "error": f"'{label}' not visible", "items": res.get("items")}
    _outline(page, el)
    el.click(timeout=4000)
    page.wait_for_timeout(700)
    _push(push_frame)
    return {"ok": True, "clicked": label, "url_path": re.sub(r"^https?://[^/]+", "", page.url or "")}


def tour_fields(page, field_ids: list[str], gap_s: float = 2.2, start_s: float = 1.5,
                wait: Callable[[float], None] | None = None, push_frame=None) -> int:
    """Highlight form fields one by one at narration pace (explain mode)."""
    sleep = wait or time.sleep
    sleep(start_s)
    n = 0
    for fid in field_ids:
        _outline(page, page.locator(f"[data-nav-field='{fid}']").first, "#2f80ed")
        _push(push_frame)
        n += 1
        sleep(gap_s)
    return n
