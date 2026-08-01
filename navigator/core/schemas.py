"""Structured data that crosses a module boundary.

Nothing in here imports another navigator module. Every other package depends on
this one, so it stays dependency-free on purpose.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

Source = Literal["agent", "user"]

CheckKind = Literal[
    "visible",  # selector present and visible
    "hidden",  # selector absent or present-but-not-visible
    "text_contains",  # inner_text of selector contains `expected`
    "value_equals",  # input value == expected
    "url_matches",  # page URL contains `expected`; selector unused
    "element_count",  # number of matching elements == int(expected)
]

#: Checks that need `expected` to mean anything.
_NEEDS_EXPECTED = frozenset(
    {"text_contains", "value_equals", "url_matches", "element_count"}
)


class Postcondition(BaseModel):
    """What must hold after a tool call.

    Declared at call time by whoever issues the call, checked in VERIFYING against
    real DOM state. `selector` is a SiteGraph alias, never raw CSS -- the alias is
    resolved through the site graph so a DOM change is a config edit.
    """

    model_config = ConfigDict(frozen=True)

    check: CheckKind
    selector: str | None = None
    expected: str | None = None
    timeout_ms: int = Field(default=15000, gt=0)

    @model_validator(mode="after")
    def _check_shape(self) -> Postcondition:
        if self.check == "url_matches":
            if self.expected is None:
                raise ValueError("url_matches requires `expected`")
        elif self.selector is None:
            raise ValueError(f"{self.check} requires `selector`")

        if self.check in _NEEDS_EXPECTED and self.expected is None:
            raise ValueError(f"{self.check} requires `expected`")

        if self.check == "element_count":
            try:
                int(self.expected)  # type: ignore[arg-type]
            except ValueError:
                raise ValueError(
                    f"element_count expects an integer, got {self.expected!r}"
                ) from None
        return self


# --- Tool calls --------------------------------------------------------------
# One model per tool, discriminated on `tool`, so a malformed call is rejected at
# parse time rather than blowing up inside Playwright. This is also the schema the
# Phase 2 LLM planner is constrained to.


class _ToolCallBase(BaseModel):
    model_config = ConfigDict(frozen=True)

    expects: Postcondition
    #: Optional line SPEAKING says when this step runs (live walkthrough guide).
    spoken: str | None = None


class ClickElement(_ToolCallBase):
    tool: Literal["click_element"] = "click_element"
    selector: str


class FillField(_ToolCallBase):
    tool: Literal["fill_field"] = "fill_field"
    selector: str
    value: str
    source: Source = "agent"
    """"user" marks live prospect-supplied data typed into the product mid-call."""


class Navigate(_ToolCallBase):
    tool: Literal["navigate"] = "navigate"
    page_id: str
    """Key in SiteGraph.pages, not a URL."""


class WaitFor(_ToolCallBase):
    tool: Literal["wait_for"] = "wait_for"
    selector: str
    timeout_ms: int = Field(default=15000, gt=0)


ToolCall = Annotated[
    ClickElement | FillField | Navigate | WaitFor,
    Field(discriminator="tool"),
]

class _ToolCallEnvelope(BaseModel):
    """Internal: lets `parse_tool_call` reuse the discriminated union."""

    call: ToolCall


def parse_tool_call(raw: dict) -> ToolCall:
    """Parse an untrusted dict into a ToolCall, discriminating on `tool`."""
    return _ToolCallEnvelope(call=raw).call


def tool_selector(call: ToolCall) -> str | None:
    """The selector alias a call targets, or None for calls that don't target one."""
    return getattr(call, "selector", None)


# --- Results -----------------------------------------------------------------


class ToolResult(BaseModel):
    """Whether the Playwright action itself succeeded.

    Says nothing about whether the postcondition holds -- that is VerifyResult.
    A tool that raises is still a ToolResult with ok=False, never an exception
    escaping into the state machine.
    """

    ok: bool
    tool: str
    detail: str = ""
    """Exception text on failure, or the observed value on success."""
    duration_ms: int


class VerifyResult(BaseModel):
    passed: bool
    actual: str
    """What the DOM actually reported, for the failure record."""
    ambiguous: bool = False
    """Element present but its state is unreadable -> escalate to vision (Phase 4)."""


# --- Log ---------------------------------------------------------------------


class ActionLogEntry(BaseModel):
    """One tool call, what it expected, and what actually happened.

    This is the unit reflection reads from. `expected_postcondition` duplicates
    `tool_call.expects` so the log can be queried on it without deserializing the
    whole call.
    """

    call_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    product_id: str = "default"
    """Which product was being demoed. Single-tenant in Phase 1; this is the
    registry key once Navigator becomes a wrapper API over many products."""
    page: str
    tool_call: ToolCall
    expected_postcondition: Postcondition
    actual_result: ToolResult
    verify: VerifyResult | None = None
    """None when the tool errored before a postcondition check was possible."""
    source: Source = "agent"
    timestamp: datetime
    """UTC, caller-supplied so replays and tests are deterministic."""

    @property
    def failed(self) -> bool:
        """True if the action failed or its postcondition did. Drives REFLECTING."""
        return not self.actual_result.ok or (
            self.verify is not None and not self.verify.passed
        )


# --- Planning ----------------------------------------------------------------


class Plan(BaseModel):
    """PLANNING's output. In Phase 1 this is replayed from the site graph;
    in Phase 2 the Groq LLM is constrained to emit exactly this shape.
    """

    spoken_response: str
    """What SPEAKING narrates."""
    tool_calls: list[ToolCall] = Field(default_factory=list)


# --- Product identity --------------------------------------------------------


class Persona(BaseModel):
    """How the agent presents a given product.

    Stored with the site graph so one deployment can demo many products without
    any narration text living in code.
    """

    model_config = ConfigDict(frozen=True)

    product_name: str
    one_liner: str = ""
    """Short positioning line, spoken in the intro."""
    agent_name: str = "Navigator AI"
    tone: str = "friendly, concise, technical when asked"
    """Guidance for the Phase 2 LLM planner. Unused by the scripted intro."""
