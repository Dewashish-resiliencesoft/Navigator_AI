"""Narrated actions: a scripted line that SAYS it clicks/opens something must
actually do it on screen, verified, before the words about the result are spoken.

Root cause this fixes: flow steps authored as ``navigate`` carry narration like
"Let's click on a chat to open the conversation on the right" but no action, so
the bot described a click that never happened. Here we derive the implied action
from the line itself (generic, no tenant names), locate the real element by
role / accessible name / text (coordinates never), scroll it into view inside
its own scroll container, click it, and verify an observable UI change.
If it cannot be done, the claim is dropped from the narration (honest).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

_APOS = str.maketrans({"\u2019": "'", "\u2018": "'"})
_CLAIM = re.compile(
    r"\b(let'?s|let us|now we|first we|then we|we(?:'ll| will)?|i'?ll)\s+(?:just\s+|now\s+|quickly\s+)?"
    r"(click|tap|press|open|select|choose|pick)\s+(?:on\s+|into\s+)?"
    r"(.+?)(?=\s+(?:to|and|so|which|then|from|in order|where|for)\b|[,.;:!?\u2014\u0964]|$)",
    re.I,
)
_LEAD = re.compile(r"^(?:the|a|an|one|any|our|this|that|its|their|up)\s+", re.I)
_GENERIC = {"chat", "chats", "conversation", "conversations", "contact", "contacts", "row", "item",
            "entry", "record", "card", "lead", "campaign", "ticket", "order", "message", "flow",
            "template", "first", "second", "last", "top", "latest", "one"}
_LOGINISH = re.compile(r"\b(log\s*in|login|sign\s*in|sign\s*up|business owner|password|email|otp)\b", re.I)

_FP_JS = """() => {
  const vis = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const q = s => [...document.querySelectorAll(s)].filter(vis).length;
  return { url: location.href, dialogs: q('[role=dialog],[aria-modal=true],dialog[open]'), inputs: q('input,select,textarea'),
    sel: q('[aria-selected=true],[aria-expanded=true],[aria-current=page],[aria-current=true],[aria-pressed=true],.active,.selected'),
    text: (document.body && document.body.innerText || '').length,
    nodes: document.querySelectorAll('*').length };
}"""

# Scroll the element into view inside its own scroll container (page, sidebar,
# modal or nested panel), then clear sticky headers; report what it did.
_REVEAL_JS = """(e) => {
  const scrollable = n => { const s = getComputedStyle(n);
    return /(auto|scroll|overlay)/.test(s.overflowY + s.overflowX) && (n.scrollHeight > n.clientHeight + 2 || n.scrollWidth > n.clientWidth + 2); };
  let c = e.parentElement; while (c && c !== document.body && !scrollable(c)) c = c.parentElement;
  const container = (!c || c === document.body) ? 'page' : (c.getAttribute('role') || c.tagName.toLowerCase()) + (c.id ? '#' + c.id : '');
  const inView = () => { const r = e.getBoundingClientRect();
    return r.top >= 0 && r.left >= 0 && r.bottom <= innerHeight && r.right <= innerWidth && r.width > 0; };
  const before = inView();
  if (!before) e.scrollIntoView({block: 'center', inline: 'nearest'});
  // sticky/fixed header covering the target -> nudge the container
  const r = e.getBoundingClientRect(); const top = document.elementFromPoint(r.left + r.width / 2, r.top + Math.min(r.height / 2, 8));
  let covered = !!top && top !== e && !e.contains(top) && !top.contains(e);
  if (covered) { (c && c !== document.body ? c : document.scrollingElement).scrollBy(0, -Math.max(80, r.height));
    const r2 = e.getBoundingClientRect(); const t2 = document.elementFromPoint(r2.left + r2.width / 2, r2.top + r2.height / 2);
    covered = !!t2 && t2 !== e && !e.contains(t2) && !t2.contains(e); }
  return {container, scrolled: !before, in_view: inView(), covered};
}"""


@dataclass
class Claim:
    sentence: int
    verb: str
    target: str
    subject: str = "let's"

    @property
    def firm(self) -> bool:
        """'let's click X' promises an action now; 'we select X' may just describe a process."""
        return self.subject.startswith(("let", "i")) or self.verb in ("click", "tap", "press")

    @property
    def generic(self) -> bool:
        words = re.findall(r"[a-z]+", self.target.lower())
        return bool(words) and all(w in _GENERIC for w in words)


@dataclass
class ActionResult:
    ok: bool
    target: str
    strategy: str = ""
    scrolled: bool = False
    container: str = ""
    verified: bool = False
    retries: int = 0
    detail: str = ""
    started: float = 0.0
    ended: float = 0.0
    extra: dict = field(default_factory=dict)

    def log_line(self, step: Any) -> str:
        return (f"[step] id={step} action target={self.target!r} locator={self.strategy or '-'} "
                f"scrolled={self.scrolled} container={self.container or '-'} verified={self.verified} "
                f"retries={self.retries} took={max(0.0, self.ended - self.started) * 1000:.0f}ms"
                f"{'' if self.ok else ' FAILED: ' + self.detail}")


def sentences(text: str) -> list[str]:
    return [p for p in re.split(r"(?<=[.!?\u0964])\s+", (text or "").strip()) if p.strip()]


def _clean_target(t: str) -> str:
    t = t.strip().strip("\"'\u201c\u201d")
    for _ in range(3):
        t = _LEAD.sub("", t)
    t = re.sub(r"\s+(?:button|tab|link|option|menu|icon)$", "", t, flags=re.I)
    return t.strip()


def parse_claims(text: str, skip_targets: list[str] | None = None) -> list[Claim]:
    """Every 'let's click X' / 'we choose X' in the line (first per sentence)."""
    skip = [s.lower() for s in (skip_targets or []) if s]
    out = []
    for i, sn in enumerate(sentences(text)):
        m = _CLAIM.search(sn.translate(_APOS))
        if not m or re.search(r"\b(?:can|could|may|might)\s+" + m.group(2), sn, re.I):
            continue
        if re.search(r"\b(?:when|whenever|if|once|after|before|until)\b", sn.translate(_APOS)[:m.start()], re.I):
            continue  # conditional ("When we open one, ...") describes, does not act
        tgt = _clean_target(m.group(3))
        if not tgt or len(tgt) > 40 or len(tgt.split()) > 5 or _LOGINISH.search(tgt):
            continue
        if any(s in tgt.lower() or tgt.lower() in s for s in skip):
            continue
        out.append(Claim(i, m.group(2).lower(), tgt, m.group(1).lower()))
    return out


def blocked(target: str) -> bool:
    from navigator.meeting.task_runner import ALLOWED_SUBMIT, BLOCKED, OPEN_VERBS

    return bool(BLOCKED.search(target) or (ALLOWED_SUBMIT.search(target) and not OPEN_VERBS.search(target)))


def already_there(page, claim: Claim) -> bool:
    """'Now let's open the Inbox' after the step already navigated to /inbox."""
    from urllib.parse import urlparse

    path = (urlparse(getattr(page, "url", "") or "").path or "").lower()
    words = [w for w in re.findall(r"[a-z]+", claim.target.lower()) if len(w) > 2]
    return bool(words) and all(w in path for w in words)


def fingerprint(page) -> dict:
    try:
        return page.evaluate(_FP_JS) or {}
    except Exception:  # noqa: BLE001
        return {}


def changed(a: dict, b: dict) -> bool:
    if not a or not b:
        return False
    if a.get("url") != b.get("url") or a.get("dialogs") != b.get("dialogs") or a.get("inputs") != b.get("inputs") or a.get("sel") != b.get("sel"):
        return True
    return abs(int(b.get("text", 0)) - int(a.get("text", 0))) > 20 or abs(int(b.get("nodes", 0)) - int(a.get("nodes", 0))) > 5


def locate(page, claim: Claim):
    """(locator, strategy) for the claimed target, or (None, reason)."""
    if claim.generic:
        from navigator.meeting.task_runner import _ITEMS_JS
        from navigator.meeting import ui_jobs

        navl = [str(r.get("label", "")) for r in (ui_jobs.NAV_INVENTORY or []) if r.get("label")]
        try:
            items = page.evaluate(_ITEMS_JS, navl) or []
        except Exception:  # noqa: BLE001
            items = []
        if not items:
            for sel in ("[role=listitem]", "[role=option]", "[role=row]", "li", "tbody tr"):
                loc = page.locator(sel)
                try:
                    for i in range(min(loc.count(), 6)):
                        if loc.nth(i).is_visible() and len((loc.nth(i).inner_text() or "").split()) >= 2:
                            return loc.nth(i), f"list-item {sel}[{i}]"
                except Exception:  # noqa: BLE001
                    continue
            return None, "no list of items on screen"
        low = claim.target.lower()
        idx = len(items) - 1 if re.search(r"\blast\b", low) else 1 if re.search(r"\bsecond\b", low) else 0
        return page.locator(f"[data-nav-item='{min(idx, len(items) - 1)}']").first, f"list-item[{idx}]"
    pat = re.compile(r"^\s*" + re.escape(claim.target) + r"\b", re.I)
    scope = page
    try:
        dlg = page.locator("[role=dialog],[aria-modal=true],dialog[open]")
        if dlg.count() and dlg.last.is_visible():
            scope = dlg.last  # an open modal owns the interaction
    except Exception:  # noqa: BLE001
        pass
    for role in ("button", "tab", "link", "menuitem", "option", "radio", "checkbox"):
        loc = scope.get_by_role(role, name=pat)
        try:
            for i in range(min(loc.count(), 5)):
                if loc.nth(i).is_visible() or loc.nth(i).is_enabled():
                    return loc.nth(i), f"role={role} name~{claim.target!r}"
        except Exception:  # noqa: BLE001
            continue
    for how, loc in (("label", scope.get_by_label(pat)), ("text", scope.get_by_text(pat))):
        try:
            for i in range(min(loc.count(), 5)):
                return loc.nth(i), f"{how}~{claim.target!r}"
        except Exception:  # noqa: BLE001
            continue
    if scope is not page:  # not in the modal: try the page behind it
        for role in ("button", "link", "tab"):
            loc = page.get_by_role(role, name=pat)
            try:
                for i in range(min(loc.count(), 5)):
                    if loc.nth(i).is_visible():
                        return loc.nth(i), f"role={role} name~{claim.target!r} (outside dialog)"
            except Exception:  # noqa: BLE001
                continue
    return None, f"{claim.target!r} not found"


def _wait(page, ms: int) -> None:
    try:
        page.wait_for_timeout(ms)
    except Exception:  # noqa: BLE001
        time.sleep(ms / 1000)


def find_with_retry(page, claim: Claim, timeout_ms: int = 3000, gap_ms: int = 250):
    """Fresh lookup until it appears or the budget runs out (slow page load:
    observable readiness, not a fixed sleep)."""
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        el, why = locate(page, claim)
        if el is not None or time.monotonic() >= deadline:
            return el, why
        _wait(page, gap_ms)


def perform(page, claim: Claim, push_frame: Callable[[], None] | None = None,
            attempts: int = 2, verify_ms: int = 3000) -> ActionResult:
    res = ActionResult(ok=False, target=claim.target, started=time.monotonic())
    if blocked(claim.target):
        res.detail = "blocked by live-task safety policy"
        res.ended = time.monotonic()
        return res
    for attempt in range(attempts):
        res.retries = attempt
        el, why = find_with_retry(page, claim)
        res.strategy = why
        if el is None:
            res.detail = why
            continue
        try:
            info = el.evaluate(_REVEAL_JS) or {}
            res.scrolled = res.scrolled or bool(info.get("scrolled"))
            res.container = str(info.get("container") or "")
            if not el.is_visible():
                res.detail = "element not visible"
                continue
            if not el.is_enabled():
                res.detail = "element disabled"
                break
            try:
                el.evaluate("e => { e.style.outline = '3px solid #ffb020'; e.style.outlineOffset = '3px'; }")
                el.hover(timeout=1500)
            except Exception:  # noqa: BLE001
                pass
            if push_frame is not None:
                push_frame()
            before = fingerprint(page)
            el.click(timeout=4000)
        except Exception as exc:  # noqa: BLE001
            res.detail = (str(exc).splitlines() or ["click failed"])[0][:160]
            continue
        deadline = time.monotonic() + verify_ms / 1000
        while time.monotonic() < deadline:
            if changed(before, fingerprint(page)):
                res.verified = True
                break
            _wait(page, 150)
        if res.verified:
            _wait(page, 250)  # let the opened panel settle before the frame
            res.ok = True
            res.detail = ""
            break
        res.detail = "click produced no visible change"
    res.ended = time.monotonic()
    if push_frame is not None:
        try:
            push_frame()
        except Exception:  # noqa: BLE001
            pass
    return res


def _as_description(sentence: str) -> str:
    """'In step two we choose the campaign type' -> 'In step two you choose ...':
    explains the process without claiming the bot did it on screen."""
    t = sentence.translate(_APOS)
    t2 = re.sub(r"\b(?:now we|first we|then we|we(?:'ll| will)?)\s+", "you ", t, count=1, flags=re.I)
    return t2 if t2 != t else ""


def run_line(page, text: str, *, speak: Callable[[str], None], step: Any = "",
             push_frame: Callable[[], None] | None = None, skip_targets: list[str] | None = None,
             log: Callable[[str], None] = lambda s: print(s, flush=True)) -> tuple[str, list[ActionResult]]:
    """Speak the line up to each claimed action, do + verify the action, and
    return the part of the line still to speak (about the resulting screen).
    Unperformable claims are dropped from the narration, never spoken."""
    claims = [c for c in parse_claims(text, skip_targets) if not already_there(page, c)]
    if not claims:
        return text, []
    sents = sentences(text)
    results: list[ActionResult] = []
    cur = 0
    for c in claims:
        el, why = find_with_retry(page, c, timeout_ms=2500)
        if el is None or blocked(c.target):
            reason = "blocked by safety policy" if el is not None else why
            keep = "" if c.firm else _as_description(sents[c.sentence])
            log(f"[step] id={step} claim {c.target!r} cannot be done ({reason}) — "
                + ("kept as a description" if keep else "sentence dropped, not narrated"))
            pre = " ".join(sents[cur:c.sentence] + ([keep] if keep else []))
            if pre:
                speak(pre)
            cur = c.sentence + 1
            results.append(ActionResult(ok=False, target=c.target, detail=reason))
            continue
        pre = " ".join(sents[cur:c.sentence + 1])
        if pre:
            t = time.monotonic()
            log(f"[step] id={step} narrating (before action) {len(pre)} chars")
            speak(pre)
            log(f"[step] id={step} narration segment done in {time.monotonic() - t:.1f}s")
        cur = c.sentence + 1
        log(f"[step] id={step} phase=executing target={c.target!r}")
        r = perform(page, c, push_frame)
        log(r.log_line(step))
        results.append(r)
        if not r.ok:
            speak("Hmm, that didn't open just now, so let's keep going.")
            # the sentences right after described the (missing) result: drop up to the next claim/screen
            nxt = next((k.sentence for k in claims if k.sentence > c.sentence), len(sents))
            cur = max(cur, min(nxt, cur + 1))
    return " ".join(sents[cur:]), results
