"""Targeted repair ladder for a failed explore step."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from navigator.automation.explore.diagnose import StuckKind
from navigator.automation.explore.guardrail import classify_action
from navigator.automation.explore.session import element_key
from navigator.automation.record import prefer_selector
from navigator.core.schemas import ClickElement, FillField, Postcondition, ToolResult

# Overlay dismiss heuristics — text/aria only; never invents free-form CSS.
_DISMISS_HINTS = (
    "accept",
    "agree",
    "allow",
    "got it",
    "ok",
    "okay",
    "close",
    "dismiss",
    "continue",
    "i understand",
    "accept all",
    "accept cookies",
)


@dataclass
class RepairAttempt:
    tactic: str
    alias: str
    css: str
    result: ToolResult
    verify_result: Any
    ok: bool


@dataclass
class RepairOutcome:
    ok: bool
    result: ToolResult | None = None
    verify_result: Any = None
    alias: str = ""
    css: str = ""
    attempts: list[RepairAttempt] = field(default_factory=list)

    @property
    def tactics_tried(self) -> list[str]:
        return [a.tactic for a in self.attempts]


def alternate_selectors(el: dict[str, Any]) -> list[tuple[str, str]]:
    """Ranked (alias, css) fallbacks. First entry matches prefer_selector()."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(pair: tuple[str, str]) -> None:
        alias, css = pair
        if not css or css in seen:
            return
        seen.add(css)
        out.append((alias, css))

    _add(prefer_selector(el))
    testid = el.get("testid") or ""
    eid = el.get("id") or ""
    name = el.get("name") or ""
    tag = el.get("tag") or "el"
    text = (el.get("text") or "").splitlines()[0].strip()[:40] if el.get("text") else ""
    role = el.get("role") or ""
    aria = el.get("aria_label") or ""

    if eid:
        _add((_slug(eid, "el"), f"#{eid}"))
    if name:
        _add((_slug(name, "el"), f'{tag}[name="{name}"]'))
    if role and (text or aria):
        label = text or aria
        _add((_slug(label, role), f'role={role}[name="{label}"]'))
    if text:
        _add((_slug(text, tag), f"text={text}"))
    if aria:
        _add((_slug(aria, tag), f'[aria-label="{aria}"]'))
    if testid and text:
        _add((_slug(text, tag), f'{tag}:has-text("{text}")'))
    elif eid and text:
        _add((_slug(text, tag), f'{tag}:has-text("{text}")'))
    return out


def _slug(text: str, fallback: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    return (s[:40] or fallback)


def tactics_for(kind: StuckKind) -> tuple[str, ...]:
    if kind in ("not_found", "detached"):
        return ("reperceive_refind", "alternate_selector")
    if kind == "not_visible":
        return ("scroll_into_view", "alternate_selector")
    if kind == "intercepted":
        return ("dismiss_overlay", "retry")
    if kind in ("timeout", "nav_stalled"):
        return ("wait_settle", "retry", "alternate_selector")
    if kind == "verify_failed":
        return ("relax_verify", "retry")
    return ()


@dataclass
class RepairCtx:
    page: Any
    graph: Any
    page_id: str
    el: dict[str, Any]
    alias: str
    css: str
    fillable: bool
    value: str | None
    execute: Callable[..., tuple[ToolResult, str]]
    verify: Callable[..., Any]
    guard_judge: Callable[[str], str] | None
    is_allowed: Callable[[dict[str, Any], str], bool]
    max_repairs: int
    inventory: Callable[[Any], list[dict[str, Any]]]
    element_key_of: Callable[[dict[str, Any]], str] = element_key


def run_ladder(ctx: RepairCtx, kind: StuckKind) -> RepairOutcome:
    """Try ordered tactics until one lands or budget spent."""
    outcome = RepairOutcome(ok=False, alias=ctx.alias, css=ctx.css)
    tactics = tactics_for(kind)
    if not tactics or ctx.max_repairs <= 0:
        return outcome

    current_el = dict(ctx.el)
    current_alias, current_css = ctx.alias, ctx.css
    alts = alternate_selectors(current_el)
    alt_i = 1  # skip primary (already failed)

    for tactic in tactics:
        if len(outcome.attempts) >= ctx.max_repairs:
            break

        if tactic == "reperceive_refind":
            fresh = ctx.inventory(ctx.page)
            ek = ctx.element_key_of(ctx.el)
            match = next((e for e in fresh if ctx.element_key_of(e) == ek), None)
            if match is None:
                continue
            current_el = match
            current_alias, current_css = prefer_selector(match)
            alts = alternate_selectors(current_el)
            alt_i = 1
            att = _try_act(ctx, "reperceive_refind", current_alias, current_css)
            outcome.attempts.append(att)
            if att.ok:
                return _success(outcome, att)
            continue

        if tactic == "alternate_selector":
            while alt_i < len(alts) and len(outcome.attempts) < ctx.max_repairs:
                current_alias, current_css = alts[alt_i]
                alt_i += 1
                if current_css == ctx.css:
                    continue
                att = _try_act(ctx, "alternate_selector", current_alias, current_css)
                outcome.attempts.append(att)
                if att.ok:
                    return _success(outcome, att)
            continue

        if tactic == "scroll_into_view":
            _scroll(ctx.page, current_css)
            att = _try_act(ctx, "scroll_into_view", current_alias, current_css)
            outcome.attempts.append(att)
            if att.ok:
                return _success(outcome, att)
            continue

        if tactic == "dismiss_overlay":
            _try_dismiss_overlay(ctx)
            att = _try_act(ctx, "dismiss_overlay", current_alias, current_css)
            outcome.attempts.append(att)
            if att.ok:
                return _success(outcome, att)
            continue

        if tactic == "wait_settle":
            _wait_ms(ctx.page, 800)
            att = _try_act(ctx, "wait_settle", current_alias, current_css)
            outcome.attempts.append(att)
            if att.ok:
                return _success(outcome, att)
            continue

        if tactic == "retry":
            att = _try_act(ctx, "retry", current_alias, current_css)
            outcome.attempts.append(att)
            if att.ok:
                return _success(outcome, att)
            continue

        if tactic == "relax_verify":
            att = _try_act(ctx, "relax_verify", current_alias, current_css, relax=True)
            outcome.attempts.append(att)
            if att.ok:
                return _success(outcome, att)
            continue

    return outcome


def _success(outcome: RepairOutcome, att: RepairAttempt) -> RepairOutcome:
    outcome.ok = True
    outcome.result = att.result
    outcome.verify_result = att.verify_result
    outcome.alias = att.alias
    outcome.css = att.css
    return outcome


def _try_act(
    ctx: RepairCtx,
    tactic: str,
    alias: str,
    css: str,
    *,
    relax: bool = False,
) -> RepairAttempt:
    result, verify_result = _act(ctx, alias, css, relax=relax)
    return RepairAttempt(
        tactic=tactic,
        alias=alias,
        css=css,
        result=result,
        verify_result=verify_result,
        ok=_passed(result, verify_result),
    )


def _passed(result: ToolResult, verify_result: Any) -> bool:
    return bool(result.ok and (verify_result is None or verify_result.passed))


def _act(
    ctx: RepairCtx,
    alias: str,
    css: str,
    *,
    relax: bool,
) -> tuple[ToolResult, Any]:
    ctx.graph.add(alias, css)
    if ctx.fillable:
        value = ctx.value or ""
        expects = Postcondition(
            check="value_equals" if not relax else "visible",
            selector=alias,
            expected=value if not relax else "",
        )
        call: Any = FillField(selector=alias, value=value, expects=expects)
    else:
        expects = Postcondition(check="visible", selector=alias)
        call = ClickElement(selector=alias, expects=expects)
    result, _ = ctx.execute(ctx.page, ctx.graph, ctx.page_id, call)
    verify_result = None
    if result.ok:
        try:
            verify_result = ctx.verify(ctx.page, ctx.graph, ctx.page_id, call.expects)
        except Exception:  # noqa: BLE001
            verify_result = None
    return result, verify_result


def _scroll(page: Any, css: str) -> None:
    try:
        page.locator(css).first.scroll_into_view_if_needed(timeout=2000)
    except Exception:  # noqa: BLE001
        try:
            page.evaluate(
                "(sel) => { const el = document.querySelector(sel); "
                "if (el) el.scrollIntoView({block:'center'}); }",
                css,
            )
        except Exception:  # noqa: BLE001
            pass


def _wait_ms(page: Any, ms: int) -> None:
    try:
        page.wait_for_timeout(ms)
    except Exception:  # noqa: BLE001
        time.sleep(ms / 1000)


def _try_dismiss_overlay(ctx: RepairCtx) -> bool:
    """Click a benign dismiss control if the guardrail allows it."""
    elements = ctx.inventory(ctx.page)
    for el in elements:
        label = " ".join(
            str(el.get(k) or "")
            for k in ("text", "label", "aria_label", "title", "value")
        ).lower()
        if not any(h in label for h in _DISMISS_HINTS):
            continue
        alias, css = prefer_selector(el)
        if not ctx.is_allowed(el, css):
            verdict = classify_action(el, judge=ctx.guard_judge)
            if verdict.flagged:
                continue  # never bypass the guardrail via repair
        ctx.graph.add(alias, css)
        call = ClickElement(
            selector=alias, expects=Postcondition(check="visible", selector=alias)
        )
        try:
            ctx.execute(ctx.page, ctx.graph, ctx.page_id, call)
        except Exception:  # noqa: BLE001
            continue
        return True
    return False
