"""Compose and patch Client-facing demo scripts from a draft site graph revision.

Builds a beat timeline: intake → optional login → playlist flows → wrap-up.
Sources: flow steps (`spoken`, `live_question`), `_meta.semantics`,
`_meta.narration_suggestions`, Knowledge RAG, persona/bio. Manual edits live
under `_meta.demo_script` and sync back to flow steps on PATCH.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

import yaml

from navigator.agent.live_input import live_prompt, needs_live_input
from navigator.agent.playback_schedule import build_schedule
from navigator.core.schemas import (
    ClickElement,
    FillField,
    Navigate,
    ToolCall,
    WaitFor,
    tool_selector,
)
from navigator.knowledge.site_graph import SiteGraph, SiteGraphError, parse_site_graph
from navigator.meeting.intake import (
    ProspectIntake,
    greet_line,
    intake_questions,
    name_ack_line,
    pitch_line,
)

BeatKind = Literal[
    "intake", "flow_step", "live_input", "login", "speak_only", "wrap_up"
]

WRAP_UP_SPOKEN = (
    "That covers the walkthrough. What would you like to explore next, "
    "or shall we wrap up?"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _beat_id(*parts: str) -> str:
    return "_".join(p for p in parts if p)


def _semantics_step_labels(graph: SiteGraph, flow_id: str) -> dict[int, str]:
    sem = graph.flow_semantics(flow_id)
    steps = sem.get("steps")
    if not isinstance(steps, list):
        return {}
    out: dict[int, str] = {}
    for item in steps:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("idx", -1))
        except (TypeError, ValueError):
            continue
        if idx < 0:
            continue
        desc = str(item.get("description") or "").strip()
        if desc:
            out[idx] = desc
    return out


def _manual_beats_by_id(stored: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not stored:
        return {}
    full = stored.get("full_demo")
    if not isinstance(full, dict):
        return {}
    beats = full.get("beats")
    if not isinstance(beats, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for beat in beats:
        if isinstance(beat, dict) and beat.get("id"):
            out[str(beat["id"])] = beat
    return out


def _humanize_alias(alias: str, *, max_len: int = 52) -> str:
    text = " ".join(str(alias).replace("-", " ").split("_")).strip()
    if not text:
        return "this"
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1].rsplit(" ", 1)[0]
    return f"{cut}…" if cut else text[: max_len - 1] + "…"


def _describe_action(graph: SiteGraph, page_id: str, call: ToolCall) -> tuple[str, dict[str, Any]]:
    """Human on-screen label + machine action payload."""
    try:
        page_name = graph.page(page_id).name
    except SiteGraphError:
        page_name = page_id

    if isinstance(call, Navigate):
        try:
            dest = graph.page(call.page_id).name
        except SiteGraphError:
            dest = _humanize_alias(call.page_id)
        label = f"Navigate → {dest}"
        return label, {"tool": "navigate", "target": call.page_id, "page": page_name}

    if isinstance(call, ClickElement):
        alias = call.selector
        label = f"Click {_humanize_alias(alias)}"
        return label, {"tool": "click_element", "selector": alias, "page": page_name}

    if isinstance(call, FillField):
        alias = call.selector
        label = f"Fill {_humanize_alias(alias)}"
        payload: dict[str, Any] = {
            "tool": "fill_field",
            "selector": alias,
            "page": page_name,
            "source": call.source,
        }
        if call.value and call.source != "user":
            payload["value"] = call.value
        return label, payload

    if isinstance(call, WaitFor):
        alias = call.selector
        label = f"Wait for {_humanize_alias(alias)}"
        return label, {"tool": "wait_for", "selector": alias, "page": page_name}

    tool = getattr(call, "tool", type(call).__name__)
    return f"{tool} on {page_name}", {"tool": tool, "page": page_name}


def spoken_from_action(graph: SiteGraph, page_id: str, call: ToolCall) -> str:
    """Tenant-neutral narration from tool + selector when explore copy is missing."""
    if isinstance(call, Navigate):
        try:
            dest = graph.page(call.page_id).name
        except SiteGraphError:
            dest = _humanize_alias(call.page_id)
        return f"Opening {dest}."
    if isinstance(call, ClickElement):
        return f"Opening {_humanize_alias(call.selector)}."
    if isinstance(call, FillField):
        label = _humanize_alias(call.selector)
        if call.source == "user":
            return f"Next I'll ask for {label}."
        return f"Entering {label}."
    if isinstance(call, WaitFor):
        return ""
    return ""


def resolve_flow_step_spoken(
    *,
    graph: SiteGraph,
    flow_id: str,
    step_index: int,
    step_count: int,
    page_id: str,
    page_name: str,
    call: ToolCall,
    manual: dict[str, dict[str, Any]] | None = None,
    beat_id: str = "",
) -> tuple[str, str]:
    """Return (spoken, spoken_source) for one flow step."""
    manual = manual or {}

    override = graph.script_spoken_override(flow_id=flow_id, step_index=step_index)
    if override:
        return override, "manual"

    if beat_id and beat_id in manual:
        m = manual[beat_id]
        if m.get("spoken_source") == "manual":
            spoken = str(m.get("spoken") or "").strip()
            if spoken:
                return spoken, "manual"

    spoken = (getattr(call, "spoken", None) or "").strip()
    if spoken:
        return spoken, "yaml"

    # A human narrated this flow while recording it. Same _meta sections as
    # explore writes, so timing is what tells the two apart -- and the Client
    # should see that this line is their own words, not a model's.
    narrated = step_index in graph.flow_step_timing(flow_id)

    sem_labels = _semantics_step_labels(graph, flow_id)
    if step_index in sem_labels:
        return sem_labels[step_index], "recorded" if narrated else "semantics"

    narr = graph.flow_narration_suggestions(flow_id)
    # ponytail: explore narration only when length matches — merged flows drift indices
    if (
        narr
        and len(narr) == step_count
        and step_index < len(narr)
        and narr[step_index]
    ):
        return narr[step_index], "recorded" if narrated else "explore"

    derived = spoken_from_action(graph, page_id, call)
    if derived:
        return derived, "generated"

    if step_index == 0:
        sem = graph.flow_semantics(flow_id)
        purpose = str(sem.get("purpose") or "").strip()
        if purpose:
            return purpose, "semantics"
        return f"Starting on {page_name} — {flow_id.replace('_', ' ')}.", "generated"
    return f"Continuing on {page_name}.", "generated"


def _section_knowledge(
    *,
    product_id: str,
    page_name: str,
    flow_id: str,
    step_action: str,
    chroma_path: Any = None,
) -> list[str]:
    if not product_id:
        return []
    query = " ".join(
        part for part in (page_name, flow_id.replace("_", " "), step_action) if part
    ).strip()
    if not query:
        return []
    try:
        from navigator.core.settings import settings
        from navigator.knowledge.memory.retrieval import retrieve_product_knowledge

        path = chroma_path if chroma_path is not None else settings.chroma_path
        chunks = retrieve_product_knowledge(product_id, query, k=3, path=path)
    except Exception:  # noqa: BLE001
        return []
    needle = page_name.lower()
    ranked = sorted(
        chunks,
        key=lambda c: (0 if needle and needle in c.lower() else 1, -len(c)),
    )
    return [c.strip() for c in ranked[:3] if c.strip()]


def compose_intake_beats(
    persona: Any,
    *,
    lang: str = "en",
    agent_gender: str = "female",
) -> list[dict[str, Any]]:
    """Intake prelude beats with placeholder tokens for preview."""
    from navigator.core.agent_settings import AgentGender, SpokenLanguage

    spoken_lang: SpokenLanguage = "hi" if lang == "hi" else "en"
    gender: AgentGender = "male" if agent_gender == "male" else "female"
    beats: list[dict[str, Any]] = [
        {
            "id": "intake_greet",
            "kind": "intake",
            "phase": "greet",
            "spoken": greet_line(persona, lang=spoken_lang, agent_gender=gender),
            "asks_visitor": False,
            "spoken_source": "intake",
        }
    ]
    for key, question, _default in intake_questions(
        product_name=getattr(persona, "product_name", "") or "the product"
    ):
        beats.append(
            {
                "id": _beat_id("intake", key),
                "kind": "intake",
                "phase": "question",
                "field": key,
                "spoken": question,
                "asks_visitor": True,
                "spoken_source": "intake",
            }
        )
        if key == "name":
            beats.append(
                {
                    "id": "intake_name_ack",
                    "kind": "intake",
                    "phase": "ack",
                    "spoken": name_ack_line("{name}", lang=spoken_lang),
                    "asks_visitor": False,
                    "spoken_source": "intake",
                    "uses_intake_tokens": True,
                }
            )
    placeholder = ProspectIntake(
        name="{name}",
        company="{company}",
        business_type="{business}",
        looking_for="{need}",
    )
    beats.append(
        {
            "id": "intake_pitch",
            "kind": "intake",
            "phase": "pitch",
            "spoken": pitch_line(
                persona,
                placeholder,
                lang=spoken_lang,
                agent_gender=gender,
                will_share_screen=True,
            ),
            "asks_visitor": False,
            "spoken_source": "intake",
            "uses_intake_tokens": True,
        }
    )
    return beats


def compose_full_demo_script(
    graph: SiteGraph,
    *,
    product_id: str = "",
    knowledge_md: str = "",
    bio_fields: dict[str, str] | None = None,
    include_login: bool = False,
    intake_enabled: bool = True,
    include_step_knowledge: bool = False,
    chroma_path: Any = None,
    stored_script: dict[str, Any] | None = None,
    flow_id_filter: str | None = None,
    spoken_language: str = "en",
    agent_gender: str = "female",
    humanize: bool = False,
) -> dict[str, Any]:
    """Build full-demo beat list for one draft revision.

    Per-step Chroma lookups are off by default — 100+ steps × embed query hangs
    the dashboard compose endpoint for tens of seconds.
    """
    persona = graph.effective_persona()
    manual = _manual_beats_by_id(stored_script)
    beats: list[dict[str, Any]] = []
    sources_used: set[str] = set()

    if intake_enabled and not flow_id_filter:
        beats.extend(
            compose_intake_beats(
                persona,
                lang=spoken_language,
                agent_gender=agent_gender,
            )
        )
        sources_used.add("intake")

    from navigator.automation.login_match import (
        demo_playlist_for_toggle,
        playlist_has_login_flow,
    )

    if (
        include_login
        and not flow_id_filter
        and not playlist_has_login_flow(graph)
    ):
        beats.append(
            {
                "id": "login_gate",
                "kind": "login",
                "spoken": "Signing into your product with the saved demo credentials.",
                "on_screen": "Automated login (Playwright)",
                "asks_visitor": False,
                "spoken_source": "generated",
            }
        )
        sources_used.add("login")

    playlist = list(
        demo_playlist_for_toggle(graph, include_login=include_login)
    )

    context_bits: list[str] = []
    bio = bio_fields or {}
    if persona.one_liner:
        context_bits.append(persona.one_liner)
    for key in ("about", "usp", "products"):
        if bio.get(key):
            context_bits.append(bio[key])
    if knowledge_md.strip() and not flow_id_filter:
        context_bits.append(knowledge_md.strip()[:400])

    flow_total_ms: dict[str, int] = {}
    flow_index = 0
    for item in playlist:
        if flow_id_filter and item.flow_id != flow_id_filter:
            continue
        flow_index += 1
        try:
            calls = graph.flow(item.page_id, item.flow_id)
        except SiteGraphError:
            continue

        sem = graph.flow_semantics(item.flow_id)
        flow_title = item.name or sem.get("auto_name") or item.flow_id.replace("_", " ")
        purpose = str(sem.get("purpose") or "").strip()
        generic = f"starting {flow_title.lower()}."
        if purpose and purpose.lower().rstrip(".") != generic.rstrip("."):
            beats.append(
                {
                    "id": _beat_id("flow", str(flow_index), "header"),
                    "kind": "speak_only",
                    "flow_id": item.flow_id,
                    "page_id": item.page_id,
                    "flow_title": flow_title,
                    "spoken": purpose,
                    "on_screen": f"Flow {flow_index}: {flow_title}",
                    "asks_visitor": False,
                    "spoken_source": "semantics",
                }
            )
            sources_used.add("semantics")

        try:
            page_name = graph.page(item.page_id).name
        except SiteGraphError:
            page_name = item.page_id

        step_count = len(calls)
        timing = graph.flow_step_timing(item.flow_id)
        # Recorded schedule, so the Client sees the timing they performed. TTS
        # durations are a playback-time concern and unavailable here.
        cues, flow_len_ms = build_schedule(
            n_steps=step_count,
            clicks=graph.flow_step_clicks(item.flow_id),
            speech=graph.flow_step_speech(item.flow_id),
            lines=graph.flow_narration_lines(item.flow_id),
            timing=timing,
            tts_ms=lambda _text: None,
        )
        speak_at = {c.idx: c.at_ms for c in cues if c.kind == "speak"}
        act_at = {c.idx: c.at_ms for c in cues if c.kind == "act"}
        # How long the host actually talked, where we know it. `step_timing` is
        # the click-to-click gap, which overstates a short line before a pause.
        spoken_len = {
            idx: end - start
            for idx, (start, end) in graph.flow_step_speech(item.flow_id).items()
        }
        if flow_len_ms:
            flow_total_ms[item.flow_id] = flow_len_ms
        approvals = graph.flow_pending_approvals(item.flow_id)
        if approvals:
            sources_used.add("approvals")
        for step_index, call in enumerate(calls):
            on_screen, action = _describe_action(graph, item.page_id, call)
            step_action = f"{action.get('tool', '')} {tool_selector(call) or action.get('target', '')}".strip()
            beat_id = _beat_id("flow", item.flow_id, str(step_index))

            knowledge_refs: list[str] = []
            if include_step_knowledge:
                knowledge_refs = _section_knowledge(
                    product_id=product_id or graph.site,
                    page_name=page_name,
                    flow_id=item.flow_id,
                    step_action=step_action,
                    chroma_path=chroma_path,
                )
                if knowledge_refs:
                    sources_used.add("knowledge")

            spoken, spoken_source = resolve_flow_step_spoken(
                graph=graph,
                flow_id=item.flow_id,
                step_index=step_index,
                step_count=step_count,
                page_id=item.page_id,
                page_name=page_name,
                call=call,
                manual=manual,
                beat_id=beat_id,
            )
            if isinstance(call, WaitFor) and not spoken:
                continue
            if spoken_source in {"yaml", "explore", "manual", "recorded"}:
                sources_used.add(spoken_source)

            if isinstance(call, FillField) and needs_live_input(call):
                example = str(call.value or "").strip()
                if beat_id in manual and manual[beat_id].get("example_value"):
                    example = str(manual[beat_id]["example_value"]).strip()
                q = (call.live_question or "").strip()
                if beat_id in manual and manual[beat_id].get("live_question"):
                    q = str(manual[beat_id]["live_question"]).strip()
                if not q:
                    q = live_prompt(call)
                beats.append(
                    {
                        "id": beat_id,
                        "kind": "live_input",
                        "flow_id": item.flow_id,
                        "page_id": item.page_id,
                        "step_index": step_index,
                        "field_alias": call.selector,
                        "action": action,
                        "on_screen": on_screen,
                        "spoken": spoken or q,
                        "live_question": q,
                        "example_value": example,
                        "asks_visitor": True,
                        "spoken_source": spoken_source,
                        "knowledge_refs": knowledge_refs,
                    }
                )
                continue

            beat: dict[str, Any] = {
                "id": beat_id,
                "kind": "flow_step",
                "flow_id": item.flow_id,
                "page_id": item.page_id,
                "step_index": step_index,
                "action": action,
                "on_screen": on_screen,
                "spoken": spoken,
                "asks_visitor": False,
                "spoken_source": spoken_source,
                "knowledge_refs": knowledge_refs,
            }
            if step_index in spoken_len:
                beat["speak_ms"] = spoken_len[step_index]
            elif step_index in timing:
                beat["speak_ms"] = timing[step_index]
            # When each cue fires on the flow clock, so the script can show
            # timecodes matching what playback will do.
            if step_index in speak_at:
                beat["speak_at_ms"] = speak_at[step_index]
            if step_index in act_at:
                beat["act_at_ms"] = act_at[step_index]
            approval = approvals.get(step_index)
            if approval is not None:
                # Recorded but never run. Rendered read-only until the Client
                # approves it in the Execution tab -- a live demo must not click
                # Save on a prospect's real data by accident.
                beat["kind"] = "pending_approval"
                beat["needs_approval"] = not bool(approval.get("approved"))
                beat["approval_reason"] = str(approval.get("reason") or "")
            beats.append(beat)

    if not flow_id_filter:
        beats.append(
            {
                "id": "wrap_up",
                "kind": "wrap_up",
                "spoken": WRAP_UP_SPOKEN,
                "asks_visitor": False,
                "spoken_source": "generated",
            }
        )

    # Merge extra manual-only beats (speak_only additions)
    composed_ids = {b["id"] for b in beats}
    for bid, mbeat in manual.items():
        if bid in composed_ids:
            for beat in beats:
                if beat.get("id") == bid:
                    if mbeat.get("spoken_source") == "manual" and mbeat.get("spoken"):
                        beat["spoken"] = mbeat["spoken"]
                        beat["spoken_source"] = "manual"
                    if mbeat.get("example_value") is not None:
                        beat["example_value"] = mbeat["example_value"]
                    if mbeat.get("live_question"):
                        beat["live_question"] = mbeat["live_question"]
                    break
        elif mbeat.get("kind") == "speak_only" and mbeat.get("spoken_source") == "manual":
            beats.append(dict(mbeat))
            sources_used.add("manual")

    if context_bits and not flow_id_filter:
        sources_used.add("bio")

    if humanize:
        beats = _humanize_beats(
            beats,
            lang=spoken_language,
            agent_gender=agent_gender,
            tone=getattr(persona, "tone", "") or "",
        )
        sources_used.add("phrasing")

    return {
        "version": 1,
        "beats": beats,
        "context": "\n".join(context_bits[:4]),
        "sources_used": sorted(sources_used),
        "flow_total_ms": flow_total_ms,
        "stats": {
            "beat_count": len(beats),
            "asks_visitor_count": sum(1 for b in beats if b.get("asks_visitor")),
            "spoken_count": sum(1 for b in beats if (b.get("spoken") or "").strip()),
        },
    }


def _humanize_beats(
    beats: list[dict[str, Any]],
    *,
    lang: str,
    agent_gender: str,
    tone: str,
) -> list[dict[str, Any]]:
    """Rewrite non-manual/recorded lines to sound less template-y."""
    try:
        from navigator.agent.phrasing import phrase_turn
    except Exception:  # noqa: BLE001
        return beats
    out: list[dict[str, Any]] = []
    for beat in beats:
        b = dict(beat)
        src = str(b.get("spoken_source") or "")
        spoken = str(b.get("spoken") or "").strip()
        if not spoken or src in {"manual", "recorded"}:
            out.append(b)
            continue
        try:
            rewritten = phrase_turn(
                intent="continue",
                fallback=spoken,
                spoken_language=lang if lang in {"en", "hi"} else "en",
                agent_gender=agent_gender if agent_gender in {"female", "male"} else "female",
            )
            if rewritten and rewritten.strip():
                b["spoken"] = rewritten.strip()
                b["spoken_source"] = "phrased"
        except Exception:  # noqa: BLE001
            pass
        out.append(b)
    return out


def merge_manual_overrides(
    composed: dict[str, Any], stored: dict[str, Any] | None
) -> dict[str, Any]:
    """Apply stored manual beats onto a freshly composed script."""
    if not stored:
        return composed
    manual = _manual_beats_by_id(stored)
    beats = composed.get("beats")
    if not isinstance(beats, list):
        return composed
    for beat in beats:
        if not isinstance(beat, dict):
            continue
        bid = beat.get("id")
        if not bid or bid not in manual:
            continue
        m = manual[bid]
        if m.get("spoken_source") == "manual" and m.get("spoken"):
            beat["spoken"] = m["spoken"]
            beat["spoken_source"] = "manual"
        for key in ("example_value", "live_question"):
            if key in m and m[key] is not None:
                beat[key] = m[key]
    return composed


def apply_script_patch(
    yaml_text: str,
    *,
    beats: list[dict[str, Any]],
    sync_flow_steps: bool = True,
) -> str:
    """Persist beats under `_meta.demo_script` and optionally sync flow YAML."""
    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        raise SiteGraphError("site graph must be a mapping")

    meta = raw.setdefault("_meta", {})
    if not isinstance(meta, dict):
        raise SiteGraphError("_meta must be a mapping")

    stored_beats: list[dict[str, Any]] = []
    for beat in beats:
        if not isinstance(beat, dict):
            continue
        entry = {k: v for k, v in beat.items() if k != "knowledge_refs"}
        stored_beats.append(entry)

    meta["demo_script"] = {
        "version": 1,
        "updated_at": _utc_now(),
        "full_demo": {"beats": stored_beats},
    }

    if sync_flow_steps:
        pages = raw.get("pages") or {}
        for beat in beats:
            if not isinstance(beat, dict):
                continue
            if beat.get("spoken_source") != "manual":
                continue
            kind = beat.get("kind")
            if kind not in {"flow_step", "live_input"}:
                continue
            flow_id = str(beat.get("flow_id") or "")
            page_id = str(beat.get("page_id") or "")
            step_index = beat.get("step_index")
            if not flow_id or not page_id or step_index is None:
                continue
            page = pages.get(page_id)
            if not isinstance(page, dict):
                continue
            flows = page.get("flows")
            if not isinstance(flows, dict):
                continue
            steps = flows.get(flow_id)
            if not isinstance(steps, list) or step_index >= len(steps):
                continue
            step = steps[step_index]
            if not isinstance(step, dict):
                continue
            spoken = str(beat.get("spoken") or "").strip()
            if spoken:
                step["spoken"] = spoken
            if kind == "live_input":
                lq = str(beat.get("live_question") or "").strip()
                if lq:
                    step["live_question"] = lq
                ex = beat.get("example_value")
                if ex is not None:
                    step["value"] = str(ex)
                step["source"] = "user"

    new_yaml = yaml.safe_dump(raw, sort_keys=False)
    parse_site_graph(new_yaml, origin="<demo-script-patch>")
    return new_yaml


def regenerate_demo_script(
    graph: SiteGraph,
    *,
    product_id: str = "",
    knowledge_md: str = "",
    bio_fields: dict[str, str] | None = None,
    include_login: bool = False,
    intake_enabled: bool = True,
    chroma_path: Any = None,
    stored_script: dict[str, Any] | None = None,
    flow_id_filter: str | None = None,
    spoken_language: str = "en",
    agent_gender: str = "female",
) -> dict[str, Any]:
    """Re-compose and preserve beats marked manual in stored script."""
    composed = compose_full_demo_script(
        graph,
        product_id=product_id,
        knowledge_md=knowledge_md,
        bio_fields=bio_fields,
        include_login=include_login,
        intake_enabled=intake_enabled,
        chroma_path=chroma_path,
        stored_script=stored_script,
        flow_id_filter=flow_id_filter,
        spoken_language=spoken_language,
        agent_gender=agent_gender,
        humanize=True,
    )
    if not stored_script:
        return composed
    manual = _manual_beats_by_id(stored_script)
    manual_only = {
        bid: b
        for bid, b in manual.items()
        if b.get("spoken_source") == "manual"
    }
    merged = merge_manual_overrides(composed, {"full_demo": {"beats": list(manual_only.values())}})
    return merged
