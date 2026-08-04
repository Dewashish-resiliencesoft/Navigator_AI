"""Exploration run orchestration: login, explore, log failures, draft narration.

Owns the module-level active session, mirroring how `client.content` owns the
recorder job -- one exploration per process, same as one recording per process.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from navigator.automation.explore.episode import EpisodeStore, StopReason
from navigator.automation.explore.explorer import EXPLORE_PAGE_ID, ExplorerDeps, explore
from navigator.automation.explore.session import ExplorationBudget, ExplorationSession
from navigator.automation.record import RecordedStep
from navigator.core.schemas import ActionLogEntry, ToolResult
from navigator.core.settings import settings
from navigator.logs.store import ActionLog, utcnow

_lock = threading.Lock()
_active: ExplorationSession | None = None


def active_session() -> ExplorationSession | None:
    with _lock:
        return _active


def explore_status() -> dict[str, Any]:
    session = active_session()
    if session is None:
        return {"active": False}
    return session.status()


# -- prior-run knowledge ------------------------------------------------------


def prior_corrections(product_id: str, *, path: str | Path | None = None) -> tuple[str, ...]:
    """Approved rules learned from earlier exploration or live-call failures.

    Feeding these into REASON is what stops a second run from walking into the
    same broken control the first run already reported.
    """
    from navigator.knowledge.memory.retrieval import retrieve_corrections

    rules: list[str] = []
    for tool in ("click_element", "fill_field"):
        try:
            found = retrieve_corrections(
                product_id,
                query="what is broken or should be avoided when exploring this product",
                page=EXPLORE_PAGE_ID,
                tool_call_type=tool,
                k=5,
                path=path,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[explore] correction retrieval failed: {exc}", flush=True)
            continue
        rules.extend(c.rule for c in found)
    # dict.fromkeys: dedupe while keeping retrieval's relevance order
    return tuple(dict.fromkeys(rules))


def log_failure(
    log: ActionLog,
    *,
    session: ExplorationSession,
    step: RecordedStep,
    result: ToolResult,
    verify_result: Any,
) -> None:
    """Record an exploration failure in the same pipeline as live-call failures.

    Same ActionLog schema, same product_id scoping, so
    `ActionLog.product_failures` and the reflection pass treat exploration
    failures and live-call failures identically.
    """
    from navigator.core.schemas import ClickElement, FillField, Postcondition

    expects = Postcondition(**step.postcondition) if step.postcondition else Postcondition(
        check="visible", selector=step.alias
    )
    if step.tool == "fill_field":
        call: Any = FillField(selector=step.alias, value=step.value or "", expects=expects)
    else:
        call = ClickElement(selector=step.alias, expects=expects)

    log.append(
        ActionLogEntry(
            session_id=session.session_id,
            product_id=session.product_id,
            page=EXPLORE_PAGE_ID,
            tool_call=call,
            expected_postcondition=expects,
            actual_result=result,
            verify=verify_result,
            source="agent",
            timestamp=utcnow(),
        )
    )


# -- narration drafting -------------------------------------------------------

_NARRATION_PROMPT = """You are writing narration for an automated product demo.

Below are the steps an explorer performed on {product}. For each step, write one
short sentence a demo host would say out loud while doing it. Be concrete about
what the viewer sees. No marketing language, no invented features.

Steps:
{steps}

Reply with JSON only: {{"narration": ["<line for step 1>", "<line for step 2>", ...]}}"""


def draft_narration(
    steps: list[RecordedStep],
    *,
    product_name: str,
    ask_text: Callable[[str], str] | None,
) -> list[str]:
    """One LLM pass over the candidate flow. Empty list on any failure."""
    if not steps or ask_text is None:
        return []
    listing = "\n".join(
        f"{i + 1}. {s.tool} {s.alias}" + (f" = {s.value!r}" if s.value else "")
        for i, s in enumerate(steps)
    )
    try:
        raw = ask_text(_NARRATION_PROMPT.format(product=product_name, steps=listing))
    except Exception as exc:  # noqa: BLE001
        print(f"[explore] narration failed: {exc}", flush=True)
        return []

    import json
    import re

    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    lines = data.get("narration") if isinstance(data, dict) else None
    if not isinstance(lines, list):
        return []
    return [str(x).strip() for x in lines][: len(steps)]


# -- providers ----------------------------------------------------------------


def groq_asker(
    api_key: str,
    stop_event: threading.Event | None = None,
) -> Callable[[str], str] | None:
    """Text reasoning via Groq — same model the live planner uses.

    Rate-limit (429 / TPD) gets a short backoff retry instead of instantly
    fail-closing every guardrail / reason call for the rest of the run.
    Sleep is interruptible so Stop exploring does not wait out the backoff.
    """
    if not api_key.strip():
        return None

    def ask(prompt: str) -> str:
        import time

        from groq import Groq

        from navigator.automation.explore.reason import MODEL

        client = Groq(api_key=api_key)
        last: Exception | None = None
        for attempt in range(4):
            if stop_event is not None and stop_event.is_set():
                raise RuntimeError("stopped by client")
            try:
                resp = client.chat.completions.create(
                    model=MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                )
                return resp.choices[0].message.content or ""
            except Exception as exc:  # noqa: BLE001
                if stop_event is not None and stop_event.is_set():
                    raise RuntimeError("stopped by client") from exc
                last = exc
                msg = str(exc).lower()
                if "429" not in msg and "rate_limit" not in msg:
                    raise
                # Groq often says "try again in XmYs" — parse lightly, else exponential.
                wait_s = _groq_retry_wait_s(str(exc), attempt)
                print(
                    f"[explore] groq rate-limited; retry in {wait_s:.0f}s "
                    f"(attempt {attempt + 1}/4)",
                    flush=True,
                )
                _sleep_interruptible(wait_s, stop_event)
        assert last is not None
        raise last

    return ask


def _sleep_interruptible(
    seconds: float, stop_event: threading.Event | None
) -> None:
    """Sleep in short slices so a client Stop can cut the wait short."""
    import time

    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("stopped by client")
        left = deadline - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(0.4, left))


def _groq_retry_wait_s(message: str, attempt: int) -> float:
    """Seconds to sleep before the next Groq attempt."""
    import re

    m = re.search(r"try again in (\d+)m([\d.]+)s", message, re.I)
    if m:
        return min(90.0, int(m.group(1)) * 60 + float(m.group(2)) + 1.0)
    m = re.search(r"try again in ([\d.]+)s", message, re.I)
    if m:
        return min(90.0, float(m.group(1)) + 1.0)
    return min(90.0, 15.0 * (2**attempt))


_VISION_SYSTEM = (
    "You choose the next element for an automated product-demo explorer. "
    "Reply with JSON only."
)


def vision_asker() -> Callable[[str, str], str] | None:
    """Escalation path — the configured reflect provider's vision model."""
    try:
        from navigator.agent.providers import get_provider

        provider = get_provider()
    except Exception as exc:  # noqa: BLE001
        print(f"[explore] vision provider unavailable: {exc}", flush=True)
        return None

    def ask(prompt: str, screenshot_b64: str) -> str:
        import base64

        return provider.complete_with_image(
            _VISION_SYSTEM, prompt, base64.b64decode(screenshot_b64)
        )

    return ask


# -- run ----------------------------------------------------------------------


def start_exploration(
    *,
    product_id: str,
    base_url: str,
    product_name: str,
    budget: ExplorationBudget | None = None,
    headful: bool | None = None,
    save_mode: str = "new",
    target_flow_id: str = "",
    target_flow_name: str = "",
    on_complete: Callable[[ExplorationSession], None] | None = None,
) -> ExplorationSession:
    """Launch a run on a daemon thread. Raises if one is already active."""
    global _active
    mode = (save_mode or "new").strip().lower()
    if mode not in {"new", "update"}:
        raise RuntimeError("save_mode must be 'new' or 'update'")
    flow_target = (target_flow_id or "").strip()
    if mode == "update" and not flow_target:
        raise RuntimeError("target_flow_id required when save_mode is update")
    with _lock:
        if _active is not None and _active.phase not in {"done", "failed", "stopped"}:
            raise RuntimeError("an exploration session is already running")
        session = ExplorationSession(
            product_id=product_id,
            base_url=base_url,
            budget=budget or ExplorationBudget(),
            phase="starting",
            save_mode=mode,
            target_flow_id=flow_target,
            target_flow_name=(target_flow_name or "").strip(),
        )
        if mode == "update":
            session.emit(
                {
                    "type": "log",
                    "level": "info",
                    "msg": (
                        f"plan: update existing flow "
                        f"“{session.target_flow_name or flow_target}” "
                        f"({flow_target})"
                    ),
                }
            )
        else:
            session.emit(
                {
                    "type": "log",
                    "level": "info",
                    "msg": "plan: create new flow (unpublished draft)",
                }
            )
        _active = session

    def _run() -> None:
        try:
            _run_exploration(session, product_name=product_name, headful=headful)
        except Exception as exc:  # noqa: BLE001
            session.error = str(exc)
            session.phase = "failed"
            session.emit({"type": "error", "msg": str(exc)})
        finally:
            if session.phase not in {"failed", "stopped"}:
                session.phase = "done"
            session.emit(
                {"type": "done", "flow_id": session.flow_id, "revision": session.revision,
                 "steps": len(session.steps), "flagged": len(session.flagged),
                 "phase": session.phase}
            )
            if on_complete is not None:
                try:
                    on_complete(session)
                except Exception as exc:  # noqa: BLE001
                    print(f"[explore] on_complete failed: {exc}", flush=True)

    threading.Thread(target=_run, name="ops-explorer", daemon=True).start()
    return session


def _run_exploration(
    session: ExplorationSession, *, product_name: str, headful: bool | None
) -> None:
    from playwright.sync_api import sync_playwright

    from navigator.app.credential_vault import CredentialVault
    from navigator.automation.browser.login_gate import LoginGateResult, run_login_gate
    from navigator.automation.browser.product_login import login_product

    ask_text = groq_asker(settings.groq_api_key, stop_event=session.stop_event)
    ask_vision = vision_asker()
    corrections = prior_corrections(session.product_id)
    if corrections:
        session.emit(
            {"type": "log", "level": "info",
             "msg": f"loaded {len(corrections)} prior correction(s)"}
        )

    log = ActionLog(settings.db_path)
    headful = settings.headful if headful is None else headful

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headful)
        context = browser.new_context()
        page = context.new_page()
        try:
            if session.stop_event.is_set():
                session.phase = "stopped"
                return
            session.phase = "logging_in"
            session.emit({"type": "status", **session.status()})
            try:
                with CredentialVault(settings.credential_db_path) as vault:
                    creds = vault.credentials_for(session.product_id)
            except Exception as exc:  # noqa: BLE001
                # An unusable vault means no login, not a dead run: the
                # signed-out surface is still worth exploring.
                session.emit(
                    {"type": "log", "level": "warn", "msg": f"vault unavailable: {exc}"}
                )
                creds = None
            if session.stop_event.is_set():
                session.phase = "stopped"
                return
            if creds:
                login_url, email, password = creds
                result = run_login_gate(
                    login_fn=lambda **kw: login_product(page, **kw),
                    url=login_url or session.base_url,
                    email=email,
                    password=password,
                )
                if session.stop_event.is_set():
                    session.phase = "stopped"
                    return
                if result is LoginGateResult.failed:
                    raise RuntimeError("product login failed — check stored credentials")
                session.emit(
                    {"type": "log", "level": "info", "msg": f"login {result.value}"}
                )
            else:
                session.emit(
                    {"type": "log", "level": "warn",
                     "msg": "no stored credentials — exploring signed-out surface only"}
                )
                page.goto(session.base_url, wait_until="domcontentloaded", timeout=60_000)

            if session.stop_event.is_set():
                session.phase = "stopped"
                return

            def _on_action(step: RecordedStep, result: ToolResult, verify_result: Any) -> None:
                failed = not result.ok or (
                    verify_result is not None and not verify_result.passed
                )
                if failed:
                    log_failure(
                        log, session=session, step=step,
                        result=result, verify_result=verify_result,
                    )

            episode = EpisodeStore(
                root=settings.explore_episodes_path,
                product_id=session.product_id,
                job_id=session.job_id,
            )
            explore(
                session,
                ExplorerDeps(
                    page=page,
                    ask_text=ask_text,
                    ask_vision=ask_vision,
                    guard_judge=ask_text,
                    field_judge=ask_text,
                    corrections=corrections,
                    on_action=_on_action,
                    episode=episode,
                ),
            )

            if session.stop_event.is_set():
                session.phase = "stopped"
                # Still draft what we have so Stop does not discard progress.
            session.phase = "drafting" if not session.stop_event.is_set() else "stopped"
            if session.phase == "drafting":
                session.emit({"type": "status", **session.status()})
            narration = (
                []
                if session.stop_event.is_set()
                else draft_narration(
                    session.steps, product_name=product_name, ask_text=ask_text
                )
            )
            _persist(session, product_name=product_name, narration=narration)
            try:
                episode.finalize(
                    stop_reason=StopReason.from_budget_text(session.stop_reason or "done"),
                    budget={
                        "max_pages": session.budget.max_pages,
                        "max_steps": session.budget.max_steps,
                        "max_repairs_per_step": session.budget.max_repairs_per_step,
                        "max_repairs_total": session.budget.max_repairs_total,
                    },
                    steps=len(session.steps),
                    actions_taken=session.actions_taken,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[explore] episode finalize failed: {exc}", flush=True)
            try:
                from navigator.automation.explore import learn as explore_learn

                explore_learn.draft_rules(
                    episode,
                    product_id=session.product_id,
                    session_id=str(session.session_id),
                    pending_db_path=settings.db_path,
                    ask_text=ask_text,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[explore] learn draft failed: {exc}", flush=True)
        finally:
            try:
                context.close()
                browser.close()
            except Exception:  # noqa: BLE001
                pass


def _persist(
    session: ExplorationSession, *, product_name: str, narration: list[str]
) -> None:
    """Merge the drafted flow into the site graph as an UNPUBLISHED revision.

    Reuses `merge_recorded_flow` and `save_revision(publish=False)` so an
    explored flow lands in exactly the same review-before-activate gate as a
    manually recorded one. Nothing here activates anything.
    """
    from navigator.app.main import get_registry
    from navigator.client.content import merge_recorded_flow

    if not session.steps:
        session.emit(
            {"type": "log", "level": "warn", "msg": "no steps captured — nothing to save"}
        )
        return

    update = session.save_mode == "update" and bool(session.target_flow_id)
    flow_id = session.target_flow_id if update else f"explored_{uuid4().hex[:8]}"
    flow_name = (
        session.target_flow_name
        if update and session.target_flow_name
        else f"Explored — {product_name}"
    )
    registry = get_registry()
    current = registry.latest_revision(session.product_id)
    new_yaml = merge_recorded_flow(
        current.yaml,
        flow_name=flow_name,
        flow_id=flow_id,
        page_id=EXPLORE_PAGE_ID,
        steps=session.steps,
        product_name=product_name,
        base_url=session.base_url,
        update_existing=update,
    )
    if narration:
        new_yaml = _attach_narration(new_yaml, flow_id, narration)
    rev = registry.put_site_graph(
        session.product_id, new_yaml, "explored", publish=False
    )
    session.flow_id = flow_id
    session.revision = rev.revision
    session.emit(
        {
            "type": "log",
            "level": "info",
            "msg": (
                f"{'updated' if update else 'created'} flow {flow_id} "
                f"({len(session.steps)} demo step(s), "
                f"{session.actions_taken} actions explored)"
            ),
        }
    )


def _attach_narration(yaml_text: str, flow_id: str, narration: list[str]) -> str:
    """Park narration suggestions under `_meta` for the review UI.

    Deliberately not inlined into the flow steps: narration is a suggestion the
    Client edits, and the flow schema validates strictly.
    """
    import yaml as _yaml

    raw = _yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        return yaml_text
    meta = raw.setdefault("_meta", {})
    if not isinstance(meta, dict):
        return yaml_text
    meta.setdefault("narration_suggestions", {})[flow_id] = narration
    return _yaml.safe_dump(raw, sort_keys=False)


def stop_exploration() -> dict[str, Any]:
    session = active_session()
    if session is None:
        raise RuntimeError("no active exploration")
    session.request_stop()
    return session.status()
