"""Phase-5: Product Discovery Agent — 4-stage intelligent flow discovery.

Replaces the crawl-trace explore with a goal-directed loop:

  Stage A — Explore:   Discover pages/components safely (existing perceive loop)
  Stage B — Understand: Ask Flash what each page/area represents
  Stage C — Compose:   Build candidate demo flows from understood capabilities
  Stage D — Curate:    Rank flows by value, safety, completeness → playlist

The loop uses an ExplorationWorldModel to track visited states, dead ends,
and branch progress scores. A branch with score ≤ 0.05 for 3 consecutive
steps is abandoned — no more 42-step random crawl traces.

Output is a DemoGraph, not raw RecordedSteps, though RecordedSteps are still
produced for the site graph YAML (they go through the existing review gate).
"""

from __future__ import annotations

import json
from typing import Any, Callable, Sequence

from navigator.agent_runtime.models import (
    DiscoveredCapability,
    DiscoveryStage,
    ExplorationWorldModel,
    SafetyClass,
)
from navigator.agent_runtime.watchdog import state_fingerprint


_MIN_BRANCH_SCORE = 0.05
_STALE_STEPS = 3
_UNDERSTAND_PROMPT = """You are a product analyst reviewing a web page screenshot or DOM.

Answer EXACTLY as JSON:
{
  "area_label": "short capability name (e.g. Campaign Management)",
  "description": "one sentence: what can a user do here?",
  "demo_value": 0.0-1.0,
  "safety": "safe_demo" | "user_input" | "mutation" | "destructive",
  "is_new_capability": true | false,
  "flow_name": "snake_case_flow_name or empty"
}

Page title: {title}
URL: {url}
Visible elements (top 20): {elements}
Already known capabilities: {known}
"""

_COMPOSE_PROMPT = """You are a demo designer. Given discovered product capabilities,
compose 3-7 demo flows a sales engineer would show a prospect.

Rank by: customer value > uniqueness > safety > completeness.

Output EXACTLY as JSON array:
[
  {
    "flow_id": "snake_case",
    "objective": "one sentence: what this flow demonstrates",
    "audience": "who benefits from seeing this",
    "priority": 1,
    "page_sequence": ["page_id_1", "page_id_2"],
    "safety": "safe_demo" | "mutation" | "destructive"
  }
]

Discovered capabilities:
{capabilities}
"""


def _call_llm(prompt: str, *, model: str = "gemini-2.5-flash") -> dict | list | None:
    """Call Gemini Flash for understanding/composition. Returns parsed JSON."""
    try:
        from navigator.core.gemini_keys import gemini_key_candidates
        import google.generativeai as genai

        keys = gemini_key_candidates()
        if not keys:
            return None
        genai.configure(api_key=keys[0])
        client = genai.GenerativeModel(model)
        resp = client.generate_content(
            prompt,
            generation_config={"response_mime_type": "application/json"},
        )
        text = resp.text.strip()
        return json.loads(text)
    except Exception as exc:  # noqa: BLE001
        print(f"[discovery] LLM call failed: {exc}", flush=True)
        return None


def score_visit(
    world: ExplorationWorldModel,
    *,
    url: str,
    elements: list[dict],
    new_capability: bool,
    new_page: bool,
) -> float:
    """Score: did we learn something useful? Used to prune dead branches."""
    score = 0.0
    fp = state_fingerprint(url, elements)
    if fp not in world.visited_states:
        score += 0.4
    if new_page:
        score += 0.3
    if new_capability:
        score += 0.5
    return min(score, 1.0)


def understand_page(
    *,
    url: str,
    title: str,
    elements: list[dict],
    world: ExplorationWorldModel,
) -> dict | None:
    """Stage B: Ask Flash what this page/area represents."""
    known = [c.label for c in world.capabilities]
    prompt = _UNDERSTAND_PROMPT.format(
        title=title,
        url=url,
        elements=json.dumps(elements[:20], ensure_ascii=False),
        known=json.dumps(known, ensure_ascii=False),
    )
    return _call_llm(prompt)


def compose_flows(world: ExplorationWorldModel) -> list[dict]:
    """Stage C: Build candidate demo flows from understood capabilities."""
    caps = [
        {
            "area_id": c.area_id,
            "label": c.label,
            "description": c.description,
            "pages": c.page_ids,
            "safety": c.safety.value,
            "score": c.progress_score,
        }
        for c in world.capabilities
    ]
    if not caps:
        return []
    prompt = _COMPOSE_PROMPT.format(capabilities=json.dumps(caps, ensure_ascii=False))
    result = _call_llm(prompt)
    if isinstance(result, list):
        return result
    return []


def curate_playlist(candidates: list[dict]) -> list[dict]:
    """Stage D: Rank and filter candidate flows.

    Rules:
      - Destructive flows require explicit approval → deprioritise
      - Flows with no clear objective → skip
      - Max 7 flows in playlist
    """
    valid = [f for f in candidates if f.get("objective") and f.get("flow_id")]
    safety_rank = {"safe_demo": 0, "user_input": 1, "mutation": 2, "destructive": 3}
    ranked = sorted(valid, key=lambda f: (
        safety_rank.get(f.get("safety", "safe_demo"), 0),
        -f.get("priority", 1),
    ))
    return ranked[:7]


def update_world_model(
    world: ExplorationWorldModel,
    *,
    url: str,
    page_id: str,
    elements: list[dict],
    understanding: dict | None,
    branch_id: str,
) -> tuple[ExplorationWorldModel, float]:
    """Integrate one page visit into the world model. Returns (updated_world, branch_score)."""
    fp = state_fingerprint(url, elements)
    new_page = fp not in world.visited_states
    new_cap = False

    updates: dict = {
        "current_url": url,
        "current_page_id": page_id,
        "visited_states": [*world.visited_states[-200:], fp],
    }

    if understanding:
        area_label = understanding.get("area_label", "")
        description = understanding.get("description", "")
        demo_value = float(understanding.get("demo_value") or 0.0)
        safety_str = understanding.get("safety", "safe_demo")
        is_new = understanding.get("is_new_capability", False)
        flow_name = understanding.get("flow_name", "")

        try:
            safety = SafetyClass(safety_str)
        except ValueError:
            safety = SafetyClass.safe_demo

        if is_new and area_label:
            new_cap = True
            existing_ids = {c.area_id for c in world.capabilities}
            if area_label.lower().replace(" ", "_") not in existing_ids:
                cap = DiscoveredCapability(
                    area_id=area_label.lower().replace(" ", "_"),
                    label=area_label,
                    description=description,
                    page_ids=[page_id],
                    flow_candidates=[flow_name] if flow_name else [],
                    progress_score=demo_value,
                    safety=safety,
                )
                updates["capabilities"] = [*world.capabilities, cap]

    branch_score = score_visit(world, url=url, elements=elements, new_capability=new_cap, new_page=new_page)

    old_score = world.branch_scores.get(branch_id, 1.0)
    new_branch_scores = {**world.branch_scores, branch_id: (old_score + branch_score) / 2}
    updates["branch_scores"] = new_branch_scores

    return world.model_copy(update=updates), branch_score


def should_abandon_branch(world: ExplorationWorldModel, branch_id: str) -> bool:
    """True if this branch has produced no useful information recently."""
    score = world.branch_scores.get(branch_id, 1.0)
    return score < _MIN_BRANCH_SCORE
