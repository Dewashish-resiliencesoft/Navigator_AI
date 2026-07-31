"""Call state.

Explicit and inspectable by design: at any point in a call you can dump this and
see exactly what the agent believes, what it is about to do, and what has already
failed. Nothing important lives implicitly inside an LLM's context.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, TypedDict
from uuid import UUID

from playwright.sync_api import Page

from navigator.config.site_graph import SiteGraph
from navigator.logs.store import ActionLog
from navigator.schemas import ActionLogEntry, Plan, ToolCall, ToolResult
from navigator.voice.tts import Speaker

State = Literal[
    "joining",
    "introducing",
    "listening",
    "planning",
    "executing",
    "verifying",
    "speaking",
    "reflecting",
    "ending",
]


@dataclass
class CallDeps:
    """Everything a node needs from the outside world.

    Passed once at graph construction rather than smuggled through state, so state
    stays serialisable and nodes stay unit-testable with a fake.
    """

    graph: SiteGraph
    page: Page
    log: ActionLog
    speaker: Speaker
    #: Phase 1: which flow PLANNING replays. Phase 2 replaces this with the LLM.
    scripted_flow: tuple[str, str] | None = None
    #: Which product is being demoed. Single-tenant in Phase 1; the wrapper API
    #: sets this per demo so one deployment can serve many products.
    product_id: str = "default"
    #: Where ENDING writes transcripts and action dumps. Namespaced by product_id
    #: underneath, so tenants never share a directory.
    archive_dir: Path = Path("archives")


def append_only(existing: list, new: list) -> list:
    """LangGraph reducer: nodes contribute to these lists, never replace them."""
    return [*existing, *new]


#: Sentinel a node returns to empty a queue that otherwise only accumulates.
CLEAR: list = []


def queue(existing: list, new: list) -> list:
    """Reducer for a work queue: append, or clear on the CLEAR sentinel.

    Needed because more than one node upstream of SPEAKING queues narration --
    without this, whichever ran last would silently drop the others' lines.
    """
    return [] if new is CLEAR else [*existing, *new]


class CallState(TypedDict, total=False):
    session_id: UUID
    page_id: str
    """Where in the site graph the agent currently is."""
    plan: Plan | None
    pending_calls: list[ToolCall]
    """Plan steps not yet executed. EXECUTING pops one from the front per pass."""
    last_call: ToolCall | None
    """The call EXECUTING just ran, so VERIFYING knows what to check."""
    last_result: ToolResult | None
    last_page_id: str
    """page_id the call ran *on*, which differs from page_id after a navigate."""
    transcript: Annotated[list[str], append_only]
    """Everything said, by anyone, in order. Archived by ENDING."""
    narration: Annotated[list[str], queue]
    """What SPEAKING should say next. Accumulates across nodes, cleared once spoken."""
    entries: Annotated[list[ActionLogEntry], append_only]
    """This call's action log, in memory as well as in SQLite."""
    failures: Annotated[list[ActionLogEntry], append_only]
    """Subset of entries that failed. The REFLECTING input."""
    turns: int
    """Completed listen->speak cycles, so the graph can bound a scripted run."""
    max_turns: int
    finished: bool


def initial_state(session_id: UUID, page_id: str, max_turns: int = 1) -> CallState:
    return CallState(
        session_id=session_id,
        page_id=page_id,
        plan=None,
        pending_calls=[],
        last_call=None,
        last_result=None,
        last_page_id=page_id,
        transcript=[],
        narration=[],
        entries=[],
        failures=[],
        turns=0,
        max_turns=max_turns,
        finished=False,
    )
