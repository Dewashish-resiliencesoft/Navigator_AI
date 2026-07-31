# Phase 2 Planner + Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire Chroma retrieval and a Groq flow-id picker so PLANNING can choose an authored flow when `scripted_flow` is unset, without inventing tool calls.

**Architecture:** `memory/` owns PersistentClient + namespaced collections + retrieval/seed. `agent/planner.py` owns `FlowChoice` and Groq JSON pick. `planning` node stays a thin orchestrator: scripted path unchanged; LLM path retrieves → choose → validate → expand via `graph.flow`.

**Tech Stack:** Python 3.11+, chromadb, groq (`llama-3.3-70b-versatile`), Pydantic, existing `CallDeps`/`Plan`/`SiteGraph`.

**Spec:** `docs/superpowers/specs/2026-07-31-phase2-planner-memory-design.md`

**Commits:** Only create git commits when the user explicitly asks. Otherwise leave changes uncommitted after each task’s tests pass.

---

## File map

| Path | Role |
|---|---|
| `navigator/memory/collections.py` | Implement `get_client` / `get_collection` |
| `navigator/memory/seed.py` | **Create** — test/dev upsert helpers (not HTTP) |
| `navigator/memory/retrieval.py` | Implement retrieve_* |
| `navigator/agent/planner.py` | **Create** — `FlowChoice`, `choose_flow`, prompt + retry |
| `navigator/agent/nodes/planning.py` | Scripted vs LLM branch |
| `navigator/agent/state.py` | Extend `CallDeps` |
| `pyproject.toml` | Add `chromadb`, `groq` to `dev` |
| `tests/test_memory.py` | **Create** |
| `tests/test_planner.py` | **Create** |

---

### Task 1: Dev deps for Chroma + Groq

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add packages to the `dev` extra**

In `pyproject.toml`, change the `dev` list to include chromadb and groq (keep existing entries):

```toml
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "httpx>=0.27",
    "chromadb>=0.5",
    "groq>=0.11",
]
```

Leave the standalone `memory = ["chromadb>=0.5"]` and `voice = [...]` extras as they are.

- [ ] **Step 2: Install**

Run: `.venv/bin/pip install -e ".[dev]"`

Expected: installs succeed; `.venv/bin/python -c "import chromadb, groq"` prints nothing and exits 0.

- [ ] **Step 3: Commit only if user asked**

```bash
git add pyproject.toml
git commit -m "$(cat <<'EOF'
Add chromadb and groq to the dev extra for Phase 2 tests.

EOF
)"
```

---

### Task 2: Collection naming tests + Chroma client

**Files:**
- Modify: `navigator/memory/collections.py`
- Create: `tests/test_memory.py`

- [ ] **Step 1: Write failing/covering tests for naming + client**

Create `tests/test_memory.py`:

```python
"""Chroma collections and retrieval, namespaced per product."""

from __future__ import annotations

import pytest

from navigator.memory.collections import collection_name, get_client, get_collection
from navigator.memory.retrieval import retrieve_corrections, retrieve_product_knowledge
from navigator.memory.seed import seed_correction, seed_knowledge


def test_collection_name_short_product():
    assert collection_name("acme", "corrections") == "acme_corr"
    assert collection_name("acme", "product_knowledge") == "acme_kb"


def test_collection_name_long_product_stays_under_63_and_stable():
    long_id = "a" * 80
    name = collection_name(long_id, "corrections")
    assert len(name) <= 63
    assert name.endswith("_corr")
    assert collection_name(long_id, "corrections") == name


def test_get_collection_creates_namespaced_collection(tmp_path):
    path = tmp_path / "chroma"
    coll = get_collection(path, "acme", "corrections")
    assert coll.name == "acme_corr"
    # Second call is the same collection, not a collision with another product.
    other = get_collection(path, "beta", "corrections")
    assert other.name == "beta_corr"
```

- [ ] **Step 2: Run naming + get_collection tests**

Run: `.venv/bin/python -m pytest tests/test_memory.py::test_collection_name_short_product tests/test_memory.py::test_collection_name_long_product_stays_under_63_and_stable tests/test_memory.py::test_get_collection_creates_namespaced_collection -v`

Expected: naming tests PASS; `test_get_collection_creates_namespaced_collection` FAIL with `NotImplementedError`.

- [ ] **Step 3: Implement `get_client` and `get_collection`**

Replace the stub bodies in `navigator/memory/collections.py`:

```python
def get_client(path: str | Path):
    import chromadb

    return chromadb.PersistentClient(path=str(path))


def get_collection(path: str | Path, product_id: str, kind: Kind):
    """One product's collection, created if absent."""
    return get_client(path).get_or_create_collection(
        name=collection_name(product_id, kind)
    )
```

Update the module docstring: remove “STUB. Phase 2 fills in…” and the claim that naming is already tested via the API layer (it was not). Keep the tenant-isolation rationale.

- [ ] **Step 4: Re-run**

Run: `.venv/bin/python -m pytest tests/test_memory.py::test_get_collection_creates_namespaced_collection -v`

Expected: PASS.

- [ ] **Step 5: Commit only if user asked**

---

### Task 3: Seed helpers + retrieval

**Files:**
- Create: `navigator/memory/seed.py`
- Modify: `navigator/memory/retrieval.py`
- Modify: `tests/test_memory.py`

- [ ] **Step 1: Append retrieval tests to `tests/test_memory.py`**

```python
def test_retrieve_corrections_filters_by_page_and_tenant(tmp_path, monkeypatch):
    path = tmp_path / "chroma"
    monkeypatch.setenv("NAVIGATOR_CHROMA_PATH", str(path))

    seed_correction(
        path,
        product_id="acme",
        rule="Click send only after the composer is focused",
        page="inbox",
        tool_call_type="click_element",
        source_call_id="call-1",
    )
    seed_correction(
        path,
        product_id="acme",
        rule="Settings save needs a wait_for on toast",
        page="settings",
        tool_call_type="click_element",
        source_call_id="call-2",
    )
    seed_correction(
        path,
        product_id="other",
        rule="SECRET other tenant rule",
        page="inbox",
        tool_call_type="click_element",
        source_call_id="call-3",
    )

    hits = retrieve_corrections(
        "acme",
        query="send message",
        page="inbox",
        tool_call_type="click_element",
        k=5,
        path=path,
    )
    assert len(hits) == 1
    assert hits[0].rule.startswith("Click send")
    assert hits[0].product_id == "acme"
    assert all(h.product_id == "acme" for h in hits)


def test_retrieve_product_knowledge_returns_docs(tmp_path):
    path = tmp_path / "chroma"
    seed_knowledge(path, product_id="acme", text="Inbox is the shared WhatsApp thread list")
    docs = retrieve_product_knowledge(
        "acme", query="whatsapp inbox", k=3, path=path
    )
    assert docs
    assert "Inbox" in docs[0]


def test_retrieve_empty_collection_returns_empty(tmp_path):
    path = tmp_path / "chroma"
    assert retrieve_corrections("acme", "q", page="inbox", path=path) == []
    assert retrieve_product_knowledge("acme", "q", path=path) == []
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_memory.py::test_retrieve_corrections_filters_by_page_and_tenant -v`

Expected: FAIL (`seed` / `path` / NotImplementedError).

- [ ] **Step 3: Implement `navigator/memory/seed.py`**

```python
"""Upsert helpers for tests and local seeding. Not an HTTP API."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from navigator.memory.collections import get_collection


def seed_correction(
    path: str | Path,
    *,
    product_id: str,
    rule: str,
    page: str,
    tool_call_type: str,
    source_call_id: str,
    doc_id: str | None = None,
) -> str:
    coll = get_collection(path, product_id, "corrections")
    doc_id = doc_id or str(uuid4())
    coll.upsert(
        ids=[doc_id],
        documents=[rule],
        metadatas=[
            {
                "product_id": product_id,
                "page": page,
                "tool_call_type": tool_call_type,
                "source_call_id": source_call_id,
            }
        ],
    )
    return doc_id


def seed_knowledge(
    path: str | Path,
    *,
    product_id: str,
    text: str,
    doc_id: str | None = None,
) -> str:
    coll = get_collection(path, product_id, "product_knowledge")
    doc_id = doc_id or str(uuid4())
    coll.upsert(
        ids=[doc_id],
        documents=[text],
        metadatas=[{"product_id": product_id}],
    )
    return doc_id
```

- [ ] **Step 4: Implement `navigator/memory/retrieval.py`**

Replace stub bodies. Keep the existing `Correction` model. Add an optional `path` parameter (required for tests; production callers pass `settings.chroma_path`):

```python
from pathlib import Path

from navigator.memory.collections import get_collection
from navigator.settings import settings


def retrieve_corrections(
    product_id: str,
    query: str,
    page: str,
    tool_call_type: str | None = None,
    k: int = 5,
    path: str | Path | None = None,
) -> list[Correction]:
    chroma_path = path if path is not None else settings.chroma_path
    coll = get_collection(chroma_path, product_id, "corrections")
    if coll.count() == 0:
        return []

    where: dict
    if tool_call_type is None:
        where = {"page": page}
    else:
        where = {
            "$and": [
                {"page": page},
                {"tool_call_type": tool_call_type},
            ]
        }

    result = coll.query(query_texts=[query], n_results=min(k, coll.count()), where=where)
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    out: list[Correction] = []
    for doc, meta in zip(docs, metas, strict=True):
        meta = meta or {}
        pid = meta.get("product_id", "")
        if pid != product_id:
            raise AssertionError(
                f"tenant leak: expected product_id={product_id!r}, got {pid!r}"
            )
        out.append(
            Correction(
                rule=doc,
                product_id=pid,
                page=meta["page"],
                tool_call_type=meta["tool_call_type"],
                source_call_id=meta["source_call_id"],
            )
        )
    return out


def retrieve_product_knowledge(
    product_id: str,
    query: str,
    k: int = 5,
    path: str | Path | None = None,
) -> list[str]:
    chroma_path = path if path is not None else settings.chroma_path
    coll = get_collection(chroma_path, product_id, "product_knowledge")
    if coll.count() == 0:
        return []
    result = coll.query(query_texts=[query], n_results=min(k, coll.count()))
    docs = (result.get("documents") or [[]])[0]
    metas = (result.get("metadatas") or [[]])[0]
    out: list[str] = []
    for doc, meta in zip(docs, metas, strict=True):
        meta = meta or {}
        if meta.get("product_id") != product_id:
            raise AssertionError(
                f"tenant leak: expected product_id={product_id!r}, got {meta.get('product_id')!r}"
            )
        out.append(doc)
    return out
```

Remove the `NotImplementedError` stubs and the “STUB. Phase 2” header line (keep the product_id / metadata-filter rationale).

- [ ] **Step 5: Run all memory tests**

Run: `.venv/bin/python -m pytest tests/test_memory.py -v`

Expected: all PASS.

- [ ] **Step 6: Commit only if user asked**

---

### Task 4: Extend `CallDeps`

**Files:**
- Modify: `navigator/agent/state.py`
- Create: `tests/test_planner.py` (first tests that only need deps fields)

- [ ] **Step 1: Write a small deps-shape test**

Create `tests/test_planner.py` with:

```python
"""LLM flow picker: injectable choose_flow + planning orchestration."""

from __future__ import annotations

from uuid import uuid4

import pytest

from navigator.agent.nodes.planning import planning
from navigator.agent.planner import FlowChoice
from navigator.agent.state import CallDeps, initial_state
from navigator.schemas import Persona
from navigator.voice.tts import PrintSpeaker


def test_calldeps_accepts_planner_fields(site_graph, page, log, tmp_path):
    def fake(**kwargs) -> FlowChoice:
        return FlowChoice(flow_id="send_test_message", spoken_response="ok")

    deps = CallDeps(
        graph=site_graph,
        page=page,
        log=log,
        speaker=PrintSpeaker(),
        scripted_flow=None,
        product_id="acme",
        archive_dir=tmp_path / "archives",
        groq_api_key=None,
        chroma_path=tmp_path / "chroma",
        choose_flow=fake,
    )
    assert deps.choose_flow is fake
    assert deps.chroma_path == tmp_path / "chroma"
```

- [ ] **Step 2: Run — expect fail on unknown kwargs / missing fields**

Run: `.venv/bin/python -m pytest tests/test_planner.py::test_calldeps_accepts_planner_fields -v`

Expected: FAIL (`TypeError: unexpected keyword` or similar).

- [ ] **Step 3: Extend `CallDeps` in `navigator/agent/state.py`**

Add imports and fields:

```python
from collections.abc import Callable
from pathlib import Path  # already present
```

At top-level (avoid circular import of FlowChoice at runtime if needed — use string annotation or TYPE_CHECKING):

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navigator.agent.planner import FlowChoice
```

On the dataclass:

```python
    groq_api_key: str | None = None
    chroma_path: Path | None = None
    choose_flow: Callable[..., FlowChoice] | None = None
    # ponytail: injectable choose_flow for unit tests without network.
    # Ceiling: one CallDeps field. Upgrade: fold into LLMProvider when Phase 4 lands.
```

If `TYPE_CHECKING` makes runtime annotation fail under `from __future__ import annotations`, `Callable[..., FlowChoice]` is fine as a string-evaluated annotation. Prefer:

```python
choose_flow: Callable[..., "FlowChoice"] | None = None
```

without importing FlowChoice at runtime.

- [ ] **Step 4: Re-run**

Run: `.venv/bin/python -m pytest tests/test_planner.py::test_calldeps_accepts_planner_fields -v`

Expected: PASS.

- [ ] **Step 5: Commit only if user asked**

---

### Task 5: `FlowChoice` + `choose_flow` (pure validation path)

**Files:**
- Create: `navigator/agent/planner.py`
- Modify: `tests/test_planner.py`

Groq network calls are covered by a thin wrapper that tests mock; unit-test the retry/validate logic with a fake chat completer.

- [ ] **Step 1: Add unit tests for planner validation**

Append to `tests/test_planner.py`:

```python
from navigator.agent.planner import FlowChoice, choose_flow, parse_flow_choice


def test_parse_flow_choice_accepts_valid_json():
    choice = parse_flow_choice(
        '{"flow_id": "send_test_message", "spoken_response": "Let me show send."}',
        allowed={"send_test_message", "search_contact"},
    )
    assert choice.flow_id == "send_test_message"


def test_parse_flow_choice_rejects_unknown_flow():
    with pytest.raises(ValueError, match="not in allowed"):
        parse_flow_choice(
            '{"flow_id": "nope", "spoken_response": "x"}',
            allowed={"send_test_message"},
        )


def test_choose_flow_retries_once_then_raises():
    calls: list[str] = []

    def fake_complete(prompt: str) -> str:
        calls.append(prompt)
        return '{"flow_id": "nope", "spoken_response": "x"}'

    with pytest.raises(ValueError, match="not in allowed"):
        choose_flow(
            api_key="unused",
            page_id="inbox",
            flow_ids=["send_test_message"],
            transcript=["user: show send"],
            corrections=[],
            knowledge=[],
            persona=Persona(product_name="Demo"),
            complete=fake_complete,
        )
    assert len(calls) == 2  # initial + one retry
```

- [ ] **Step 2: Run — expect import fail**

Run: `.venv/bin/python -m pytest tests/test_planner.py::test_parse_flow_choice_accepts_valid_json tests/test_planner.py::test_choose_flow_retries_once_then_raises -v`

Expected: FAIL (`ModuleNotFoundError` or import error).

- [ ] **Step 3: Implement `navigator/agent/planner.py`**

```python
"""Groq flow picker: chooses a named flow; never invents tool calls."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict, ValidationError

from navigator.memory.retrieval import Correction
from navigator.schemas import Persona

MODEL = "llama-3.3-70b-versatile"


class FlowChoice(BaseModel):
    model_config = ConfigDict(frozen=True)

    flow_id: str
    spoken_response: str


def parse_flow_choice(raw: str, *, allowed: set[str]) -> FlowChoice:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"planner returned non-JSON: {raw!r}") from e
    try:
        choice = FlowChoice.model_validate(data)
    except ValidationError as e:
        raise ValueError(f"planner returned invalid FlowChoice: {raw!r}") from e
    if choice.flow_id not in allowed:
        raise ValueError(
            f"flow_id {choice.flow_id!r} not in allowed {sorted(allowed)}"
        )
    return choice


def build_prompt(
    *,
    page_id: str,
    flow_ids: Sequence[str],
    transcript: Sequence[str],
    corrections: Sequence[Correction],
    knowledge: Sequence[str],
    persona: Persona,
    retry_hint: str | None = None,
) -> str:
    lines = [
        f"You are {persona.agent_name}, demoing {persona.product_name}.",
        f"Tone: {persona.tone}",
        f"Current page_id: {page_id}",
        f"Allowed flow_ids (pick exactly one): {', '.join(flow_ids)}",
        "Return ONLY JSON: {\"flow_id\": \"...\", \"spoken_response\": \"...\"}",
        "Do not invent steps, selectors, or flows outside the allowed list.",
        "Transcript:",
        *transcript,
        "Corrections:",
        *(c.rule for c in corrections) or ("(none)",),
        "Product knowledge:",
        *knowledge or ("(none)",),
    ]
    if retry_hint:
        lines.append(retry_hint)
    return "\n".join(lines)


def _groq_complete(api_key: str, prompt: str) -> str:
    from groq import Groq

    client = Groq(api_key=api_key)
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
    )
    content = resp.choices[0].message.content
    if not content:
        raise ValueError("Groq returned empty content")
    return content


def choose_flow(
    *,
    api_key: str,
    page_id: str,
    flow_ids: Sequence[str],
    transcript: Sequence[str],
    corrections: Sequence[Correction],
    knowledge: Sequence[str],
    persona: Persona,
    complete: Callable[[str], str] | None = None,
) -> FlowChoice:
    if not flow_ids:
        raise RuntimeError(f"page {page_id!r} has no flows to choose from")
    allowed = set(flow_ids)
    completer = complete or (lambda prompt: _groq_complete(api_key, prompt))

    prompt = build_prompt(
        page_id=page_id,
        flow_ids=flow_ids,
        transcript=transcript,
        corrections=corrections,
        knowledge=knowledge,
        persona=persona,
    )
    raw = completer(prompt)
    try:
        return parse_flow_choice(raw, allowed=allowed)
    except ValueError:
        retry = build_prompt(
            page_id=page_id,
            flow_ids=flow_ids,
            transcript=transcript,
            corrections=corrections,
            knowledge=knowledge,
            persona=persona,
            retry_hint=(
                f"Previous answer was invalid. flow_id MUST be one of: "
                f"{', '.join(sorted(allowed))}"
            ),
        )
        raw2 = completer(retry)
        return parse_flow_choice(raw2, allowed=allowed)
```

- [ ] **Step 4: Re-run planner unit tests**

Run: `.venv/bin/python -m pytest tests/test_planner.py::test_parse_flow_choice_accepts_valid_json tests/test_planner.py::test_parse_flow_choice_rejects_unknown_flow tests/test_planner.py::test_choose_flow_retries_once_then_raises -v`

Expected: PASS.

- [ ] **Step 5: Commit only if user asked**

---

### Task 6: Wire `planning` node

**Files:**
- Modify: `navigator/agent/nodes/planning.py`
- Modify: `tests/test_planner.py`

- [ ] **Step 1: Write planning orchestration tests**

Append:

```python
def _llm_deps(site_graph, page, log, tmp_path, choose_flow, product_id="acme"):
    return CallDeps(
        graph=site_graph,
        page=page,
        log=log,
        speaker=PrintSpeaker(),
        scripted_flow=None,
        product_id=product_id,
        archive_dir=tmp_path / "archives",
        chroma_path=tmp_path / "chroma",
        choose_flow=choose_flow,
    )


def test_planning_scripted_flow_still_wins(state, deps):
    """Existing conftest deps set scripted_flow — unchanged behavior."""
    out = planning(state, deps)
    assert [c.tool for c in out["pending_calls"]] == [
        "navigate",
        "wait_for",
        "fill_field",
        "click_element",
    ]


def test_planning_uses_choose_flow_and_expands_graph_flow(
    site_graph, page, log, tmp_path, state
):
    def fake(**kwargs) -> FlowChoice:
        assert kwargs["page_id"] == "inbox"
        assert "send_test_message" in kwargs["flow_ids"]
        return FlowChoice(
            flow_id="search_contact",
            spoken_response="I'll search for a contact.",
        )

    deps = _llm_deps(site_graph, page, log, tmp_path, fake)
    out = planning(state, deps)
    assert out["plan"].spoken_response == "I'll search for a contact."
    expected = list(site_graph.flow("inbox", "search_contact"))
    assert out["plan"].tool_calls == expected
    assert [c.tool for c in out["pending_calls"]] == ["fill_field", "click_element"]


def test_planning_rejects_unknown_flow_from_chooser(
    site_graph, page, log, tmp_path, state
):
    def fake(**kwargs) -> FlowChoice:
        return FlowChoice(flow_id="does_not_exist", spoken_response="x")

    deps = _llm_deps(site_graph, page, log, tmp_path, fake)
    with pytest.raises(ValueError, match="does_not_exist"):
        planning(state, deps)


def test_planning_requires_key_without_scripted_or_chooser(
    site_graph, page, log, tmp_path, state, monkeypatch
):
    monkeypatch.setattr("navigator.agent.nodes.planning.settings.groq_api_key", "")
    deps = CallDeps(
        graph=site_graph,
        page=page,
        log=log,
        speaker=PrintSpeaker(),
        scripted_flow=None,
        archive_dir=tmp_path / "archives",
        chroma_path=tmp_path / "chroma",
        groq_api_key=None,
        choose_flow=None,
    )
    with pytest.raises(RuntimeError, match="scripted_flow"):
        planning(state, deps)


def test_planning_passes_retrieved_corrections_into_chooser(
    site_graph, page, log, tmp_path, state
):
    from navigator.memory.seed import seed_correction

    path = tmp_path / "chroma"
    seed_correction(
        path,
        product_id="acme",
        rule="Always wait for composer before send",
        page="inbox",
        tool_call_type="click_element",
        source_call_id="c1",
    )
    seen: dict = {}

    def fake(**kwargs) -> FlowChoice:
        seen.update(kwargs)
        return FlowChoice(
            flow_id="send_test_message",
            spoken_response="Sending a message.",
        )

    state = initial_state(uuid4(), "inbox")
    state["transcript"] = ["user: Can you show me how sending a message works?"]
    deps = _llm_deps(site_graph, page, log, tmp_path, fake)
    planning(state, deps)
    assert any(
        "composer" in c.rule for c in seen.get("corrections", [])
    ), seen.get("corrections")
```

Note: `search_contact` in `whatsapp_crm.yaml` is `fill_field` then `click_element` — assert against `site_graph.flow(...)` as source of truth.

- [ ] **Step 2: Run — expect failures on LLM path**

Run: `.venv/bin/python -m pytest tests/test_planner.py -v`

Expected: `test_calldeps_*` and parse/choose tests PASS; planning LLM tests FAIL (still Phase 1 RuntimeError or ignore chooser).

- [ ] **Step 3: Rewrite `navigator/agent/nodes/planning.py`**

```python
"""PLANNING: decide what to do and what to say.

Scripted path (CallDeps.scripted_flow set): replay a named flow — deterministic,
used by demo/CI. LLM path: retrieve memory, pick a flow_id via Groq or an
injectable chooser, expand tool_calls from the site graph. The model never
invents selectors or postconditions.
"""

from __future__ import annotations

from navigator.agent.planner import FlowChoice, choose_flow
from navigator.agent.state import CallDeps, CallState
from navigator.memory.retrieval import retrieve_corrections, retrieve_product_knowledge
from navigator.schemas import Plan
from navigator.settings import settings


def planning(state: CallState, deps: CallDeps) -> CallState:
    if deps.scripted_flow is not None:
        page_id, flow_id = deps.scripted_flow
        return _plan_from_flow(
            deps,
            page_id,
            flow_id,
            spoken=_describe(deps.graph.page(page_id).name, flow_id),
        )

    page_id = state.get("page_id") or ""
    page = deps.graph.page(page_id)
    flow_ids = sorted(page.flows)
    if not flow_ids:
        raise RuntimeError(f"page {page_id!r} has no flows to choose from")

    chroma_path = deps.chroma_path if deps.chroma_path is not None else settings.chroma_path
    transcript = list(state.get("transcript") or [])
    query = _query_from_transcript(transcript)

    corrections = retrieve_corrections(
        deps.product_id,
        query,
        page=page_id,
        tool_call_type=None,
        path=chroma_path,
    )
    knowledge = retrieve_product_knowledge(
        deps.product_id, query, path=chroma_path
    )
    persona = deps.graph.effective_persona()

    chooser_kwargs = dict(
        page_id=page_id,
        flow_ids=flow_ids,
        transcript=transcript,
        corrections=corrections,
        knowledge=knowledge,
        persona=persona,
    )

    if deps.choose_flow is not None:
        choice = deps.choose_flow(**chooser_kwargs)
    else:
        api_key = deps.groq_api_key if deps.groq_api_key is not None else settings.groq_api_key
        if not api_key:
            raise RuntimeError(
                "PLANNING needs CallDeps.scripted_flow=(page_id, flow_id) "
                "or a Groq API key (CallDeps.groq_api_key / NAVIGATOR_GROQ_API_KEY)"
            )
        choice = choose_flow(api_key=api_key, **chooser_kwargs)

    if not isinstance(choice, FlowChoice):
        choice = FlowChoice.model_validate(choice)

    if choice.flow_id not in page.flows:
        raise ValueError(
            f"flow_id {choice.flow_id!r} not in allowed {flow_ids}"
        )

    return _plan_from_flow(
        deps, page_id, choice.flow_id, spoken=choice.spoken_response
    )


def _plan_from_flow(
    deps: CallDeps, page_id: str, flow_id: str, *, spoken: str
) -> CallState:
    calls = deps.graph.flow(page_id, flow_id)
    plan = Plan(spoken_response=spoken, tool_calls=list(calls))
    return CallState(
        plan=plan,
        pending_calls=list(plan.tool_calls),
        narration=[plan.spoken_response],
        transcript=[f"agent: {plan.spoken_response}"],
    )


def _query_from_transcript(transcript: list[str]) -> str:
    for line in reversed(transcript):
        if line.startswith("user:"):
            return line.removeprefix("user:").strip()
    return " ".join(transcript[-5:]) if transcript else ""


def _describe(page_name: str, flow_id: str) -> str:
    flow_name = flow_id.replace("_", " ").replace("-", " ")
    return (
        f"Sure, let me show you. I'll walk through {flow_name} on the "
        f"{page_name} page, step by step."
    )
```

- [ ] **Step 4: Run planner + graph tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_planner.py tests/test_graph.py tests/test_memory.py -v
```

Expected: all PASS. Fix `search_contact` assertion if YAML has more than one step.

- [ ] **Step 5: Commit only if user asked**

---

### Task 7: Full suite + docs check

- [ ] **Step 1: Full pytest**

Run: `.venv/bin/python -m pytest -q`

Expected: all previous tests still green + new ones (count ≥ 139 + new).

- [ ] **Step 2: Docs drift check**

Run: `.venv/bin/python -m navigator.docs check`

Expected: up to date (no API changes this slice). If red, stop and investigate — do not hand-edit `docs/` or `fern/`; only run `python -m navigator.docs build` if an API surface actually changed (it should not).

- [ ] **Step 3: Scripted demo still works without Groq**

Run: `.venv/bin/python -m navigator.demo --headless --mute`

Expected: exit 0; action log shows send flow steps.

- [ ] **Step 4: Final commit only if user asked** (include spec + plan + code together or as user prefers)

---

## Spec coverage checklist

| Spec requirement | Task |
|---|---|
| Chroma `PersistentClient` + `get_or_create_collection` | 2 |
| `collection_name` tenant namespacing | 2 |
| `retrieve_corrections` / `retrieve_product_knowledge` + tenant assert | 3 |
| `seed.py` test upserts, no HTTP | 3 |
| `FlowChoice` + Groq JSON + one retry | 5 |
| `scripted_flow` wins | 6 |
| Injectable `choose_flow` skips key | 4, 6 |
| Always re-validate `flow_id` before expand | 6 |
| Empty flows / missing key errors | 5, 6 |
| `chromadb`+`groq` on `dev` extra | 1 |
| No STT / Attendee / ingest API / docs rebuild | — out of scope |
| Existing `test_graph` scripted path | 6, 7 |

## Placeholder scan

None intentional. If Chroma’s `where` with a single key rejects `$and` form when `tool_call_type` is set, use the two-branch `where` already shown. If `coll.query` errors on `n_results > count`, the `min(k, coll.count())` guard handles it.
