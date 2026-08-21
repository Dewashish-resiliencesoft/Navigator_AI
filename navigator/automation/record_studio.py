"""Manual-record studio helpers: field ask, variables, next-prompt agent."""

from __future__ import annotations

import json
import re
from typing import Any

from navigator.automation.record import RecordedStep, _slug


def demo_variables_from_steps(steps: list[RecordedStep]) -> list[dict[str, str]]:
    """Unique visitor variables created by Ask-visitor marks."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for step in steps:
        if step.tool != "fill_field" or step.source != "user":
            continue
        alias = (step.alias or "").strip()
        if not alias or alias in seen:
            continue
        seen.add(alias)
        out.append(
            {
                "alias": alias,
                "label": alias.replace("_", " "),
                "live_question": (step.live_question or "").strip()
                or f"Could you share your {alias.replace('_', ' ')}?",
            }
        )
    return out


def mark_step_ask_visitor(
    steps: list[RecordedStep],
    *,
    step_index: int | None = None,
    var_alias: str = "",
    live_question: str = "",
    page: Any = None,
) -> RecordedStep:
    """Rewrite a recorded fill as source=user; clear typed demo value."""
    if not steps:
        raise RuntimeError("no recorded steps yet")
    idx = step_index if step_index is not None else _last_fill_index(steps)
    if idx is None:
        raise RuntimeError("click or fill a field first")
    step = steps[idx]
    if step.tool != "fill_field":
        # Promote a click-on-input into a fill ask.
        step = RecordedStep(
            tool="fill_field",
            alias=step.alias,
            selector=step.selector,
            value="",
            page_id=step.page_id,
            at_ms=step.at_ms,
            mouse_path=list(step.mouse_path),
        )
        steps[idx] = step

    alias = _slug(var_alias or step.alias, "field")[:40]
    question = (live_question or "").strip()
    if not question and page is not None:
        from navigator.automation.guided_task.ask_visitor import propose_live_question

        question = propose_live_question(
            page, f"Ask the visitor to fill {alias.replace('_', ' ')}"
        )
    if not question:
        question = f"Could you share your {alias.replace('_', ' ')}?"

    step.alias = alias
    step.source = "user"
    step.live_question = question
    step.value = ""
    step.value_ref = None
    steps[idx] = step
    return step


def bind_value_ref(
    steps: list[RecordedStep],
    *,
    step_index: int | None = None,
    value_ref: str,
) -> RecordedStep:
    """Point a fill step at a prior visitor variable (no live ask on this step)."""
    if not steps:
        raise RuntimeError("no recorded steps yet")
    ref = _slug(value_ref, "var")[:40]
    if not ref:
        raise RuntimeError("variable required")
    idx = step_index if step_index is not None else _last_fill_index(steps)
    if idx is None:
        raise RuntimeError("click or fill a field first")
    step = steps[idx]
    if step.tool != "fill_field":
        step = RecordedStep(
            tool="fill_field",
            alias=step.alias,
            selector=step.selector,
            value="",
            page_id=step.page_id,
            at_ms=step.at_ms,
            mouse_path=list(step.mouse_path),
        )
        steps[idx] = step
    step.source = "agent"
    step.live_question = None
    step.value = ""
    step.value_ref = ref
    steps[idx] = step
    return step


def _last_fill_index(steps: list[RecordedStep]) -> int | None:
    for i in range(len(steps) - 1, -1, -1):
        if steps[i].tool == "fill_field":
            return i
        tag_hint = (steps[i].alias or "").lower()
        if steps[i].tool == "click_element" and any(
            x in tag_hint for x in ("input", "email", "phone", "name", "field", "text")
        ):
            return i
    # Last step if any
    return len(steps) - 1 if steps else None


_NEXT_SYSTEM = """You author the next beats of a recorded product demo script.
Return JSON only:
{"steps":[{"tool":"click_element"|"fill_field","alias":"...","selector_hint":"...","value_ref":null|"var_alias","spoken":"..."}],"note":"..."}
Rules:
- Prefer fill_field with value_ref when reusing a visitor variable the Client named.
- Never invent visitor data; use value_ref for reuse.
- Keep spoken lines natural, 1-2 sentences, not generic "now I click".
- Max 6 steps.
"""


def propose_next_steps(
    *,
    page: Any,
    client_prompt: str,
    variables: list[dict[str, str]],
    graph_snippet: str = "",
) -> list[RecordedStep]:
    """Screenshot + prompt → appendable RecordedSteps (draft only)."""
    prompt = " ".join((client_prompt or "").split()).strip()
    if not prompt:
        raise RuntimeError("describe what to do next")

    png = b""
    try:
        png = page.screenshot(type="png", full_page=False)
    except Exception as exc:  # noqa: BLE001
        print(f"[record-studio] screenshot failed: {exc}", flush=True)

    inventory_txt = ""
    try:
        from navigator.automation.explore.perceive import inventory

        els = inventory(page)[:40]
        inventory_txt = json.dumps(
            [
                {
                    "alias": (e.get("label") or e.get("text") or e.get("tag") or "")[:40],
                    "tag": e.get("tag"),
                    "testid": e.get("testid"),
                }
                for e in els
                if isinstance(e, dict)
            ]
        )[:3000]
    except Exception as exc:  # noqa: BLE001
        print(f"[record-studio] inventory failed: {exc}", flush=True)

    vars_txt = json.dumps(variables)[:1500]
    user = (
        f"Client next-step prompt:\n{prompt}\n\n"
        f"Visitor variables available:\n{vars_txt}\n\n"
        f"Visible controls:\n{inventory_txt}\n\n"
        f"Site graph snippet:\n{(graph_snippet or '')[:2000]}\n"
    )

    raw = ""
    try:
        from navigator.agent.providers import get_provider

        provider = get_provider()
        if png:
            raw = provider.complete_with_image(_NEXT_SYSTEM, user, png)
        else:
            raw = provider.complete(system=_NEXT_SYSTEM, user=user)
    except Exception as exc:  # noqa: BLE001
        print(f"[record-studio] next-prompt LLM failed: {exc}", flush=True)
        raise RuntimeError(f"could not plan next steps: {exc}") from exc

    return _parse_next_steps(raw)


def _parse_next_steps(raw: str) -> list[RecordedStep]:
    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        raise RuntimeError("agent returned no steps")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise RuntimeError("agent returned invalid JSON") from exc
    items = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(items, list) or not items:
        raise RuntimeError("agent returned empty steps")
    out: list[RecordedStep] = []
    for i, item in enumerate(items[:6]):
        if not isinstance(item, dict):
            continue
        tool = str(item.get("tool") or "click_element").strip()
        if tool not in {"click_element", "fill_field"}:
            tool = "click_element"
        alias = _slug(str(item.get("alias") or f"step_{i+1}"), f"step_{i+1}")[:40]
        hint = str(item.get("selector_hint") or alias).strip() or alias
        ref = item.get("value_ref")
        value_ref = _slug(str(ref), "")[:40] if ref else None
        spoken = str(item.get("spoken") or "").strip() or None
        out.append(
            RecordedStep(
                tool=tool,
                alias=alias,
                selector=hint if hint.startswith(("#", "[", "text=")) else f"text={hint[:40]}",
                value="",
                source="agent",
                value_ref=value_ref or None,
                live_question=None,
                spoken=spoken,
            )
        )
    if not out:
        raise RuntimeError("agent returned empty steps")
    return out


def apply_confirmed_agent_task(
    steps: list[RecordedStep],
    task: Any,
    *,
    page: Any = None,
) -> list[RecordedStep]:
    """Apply a confirmed AgentTask onto recorded steps (ask + fill/use).

    Does not drive the browser — only rewrites fill steps for live demo.
    """
    from navigator.automation.prompt_command import AgentTask

    if not isinstance(task, AgentTask):
        raise TypeError("AgentTask required")
    touched: list[RecordedStep] = []
    attach_idx = task.step_index
    for st in task.steps:
        if st.op == "ask_user":
            idx = st.step_index if st.step_index is not None else attach_idx
            step = mark_step_ask_visitor(
                steps,
                step_index=idx,
                var_alias=st.variable,
                live_question=st.question,
                page=page,
            )
            touched.append(step)
        elif st.op in {"fill_field", "use_variable"}:
            idx = st.step_index if st.step_index is not None else attach_idx
            if idx is None:
                continue
            if 0 <= idx < len(steps) and steps[idx].source == "user" and st.op == "fill_field":
                # Ask already owns this fill.
                continue
            step = bind_value_ref(
                steps,
                step_index=idx,
                value_ref=st.variable,
            )
            touched.append(step)
        elif st.op == "save_variable":
            continue
    return touched
