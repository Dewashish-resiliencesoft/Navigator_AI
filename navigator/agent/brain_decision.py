"""Unified decision schema for live demo brain routing."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from navigator.core.schemas import ToolCall

BrainIntent = Literal[
    "continue",
    "answer",
    "run_flow",
    "detour",
    "handoff",
    "end",
    "clarify",
    "goodbye",
    "affirm",
    "negate",
    "correction",
    "unknown",
]


class BrainDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    intent: BrainIntent
    spoken: str = ""
    flow_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    confidence: float = 0.0
    branch: str = ""
    detail: str = ""
    router: str = Field(default="", description="Which router stage produced this.")
