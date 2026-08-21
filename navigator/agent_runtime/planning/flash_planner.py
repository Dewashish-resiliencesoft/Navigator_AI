"""Gemini Flash deep reasoning — structured AgentPlan output."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from navigator.agent_runtime.models import AgentAction, AgentPlan, AgentWorldState, SemanticTarget, SemanticVerification
from navigator.core.gemini_keys import gemini_key_candidates, is_gemini_quota_error
from navigator.core.settings import settings


_PLANNER_SYSTEM = """You are the deep reasoning brain for a live product demo agent.
Given user goal, world state, and DOM, output ONLY valid JSON matching this schema:
{
  "goal": "string",
  "escalation": "dom" | "screenshot" | "none",
  "steps": [
    {
      "tool": "click" | "type" | "navigate" | "scroll" | "hover" | "wait" | "select",
      "target": {"semantic_id": "...", "label": "...", "page_id": "..."},
      "value": "",
      "reason": "snake_case_reason",
      "spoken": "optional short narration",
      "verification": {"check": "url_contains"|"visible"|"text_contains", "expected": "...", "selector": null},
      "non_interruptible": false
    }
  ]
}
Rules:
- Never emit raw CSS selectors.
- Prefer semantic_id from the provided DOM elements list.
- navigate uses target.page_id (site graph page key).
- Keep plans short (1-6 steps).
- Include verification expectations for navigation and major clicks.
"""


class FlashPlanner:
    def __init__(self, *, model: str | None = None) -> None:
        from navigator.core.gemini_keys import normalize_gemini_model

        self.model = normalize_gemini_model(model or settings.brain_reasoning_model)

    def plan(self, *, task_id: UUID, goal: str, world: AgentWorldState) -> AgentPlan | None:
        keys = gemini_key_candidates()
        if not keys:
            print("[runtime] Flash planner: no Gemini key", flush=True)
            return None

        user_payload = {
            "goal": goal,
            "page_id": world.browser.page_id,
            "url": world.browser.url,
            "dom": world.browser.live_context,
            "elements": world.browser.semantic_elements[:40],
            "memory": world.memory.relevant_context,
            "failures": world.memory.previous_failures[-3:],
        }
        user = json.dumps(user_payload, ensure_ascii=False)

        last_exc: Exception | None = None
        for i, key in enumerate(keys):
            try:
                raw = self._call(key, user)
                return self._parse(task_id, goal, raw)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if is_gemini_quota_error(exc) and i + 1 < len(keys):
                    continue
                print(f"[runtime] Flash planner failed: {exc}", flush=True)
                return None
        if last_exc:
            print(f"[runtime] Flash planner failed: {last_exc}", flush=True)
        return None

    def _call(self, api_key: str, user: str) -> str:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=self.model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=_PLANNER_SYSTEM,
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )
        from navigator.core.usage_context import record_gemini_generate

        record_gemini_generate(resp, purpose="runtime_plan", model=self.model)
        text = (resp.text or "").strip()
        if not text:
            raise RuntimeError("Flash planner returned empty")
        return text

    def _parse(self, task_id: UUID, goal: str, raw: str) -> AgentPlan:
        data: dict[str, Any] = json.loads(raw)
        steps: list[AgentAction] = []
        for item in data.get("steps") or []:
            target_raw = item.get("target") or {}
            target = SemanticTarget(
                semantic_id=str(target_raw.get("semantic_id") or ""),
                label=str(target_raw.get("label") or ""),
                page_id=str(target_raw.get("page_id") or ""),
            )
            ver_raw = item.get("verification")
            verification = None
            if isinstance(ver_raw, dict) and ver_raw.get("expected"):
                verification = SemanticVerification(
                    check=ver_raw.get("check") or "url_contains",
                    expected=str(ver_raw.get("expected") or ""),
                    selector=ver_raw.get("selector"),
                )
            tool = item.get("tool") or "click"
            if tool not in {"click", "type", "navigate", "scroll", "hover", "wait", "select"}:
                tool = "click"
            steps.append(
                AgentAction(
                    tool=tool,
                    target=target,
                    value=str(item.get("value") or ""),
                    reason=str(item.get("reason") or ""),
                    spoken=item.get("spoken"),
                    verification=verification,
                    non_interruptible=bool(item.get("non_interruptible")),
                )
            )
        escalation = data.get("escalation") or "none"
        if escalation not in {"dom", "screenshot", "none"}:
            escalation = "none"
        return AgentPlan(
            task_id=task_id,
            goal=data.get("goal") or goal,
            steps=steps,
            escalation=escalation,
        )
