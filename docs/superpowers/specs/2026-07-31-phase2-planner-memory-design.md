# Phase 2 slice: LLM flow picker + Chroma retrieval

**Date:** 2026-07-31  
**Status:** approved in dialogue (scope B, memory A, gating A, planner approach 1)  
**Out of scope:** STT / Silero / Whisper, Attendee, reflection, vision verify, ingest API, recorder

## Goal

When `CallDeps.scripted_flow` is unset, PLANNING asks Groq to pick one **named flow** already present on the current page, then expands that flow into a `Plan` exactly as Phase 1 does. Corrections and product knowledge are retrieved from per-product Chroma collections and passed into the prompt. Demo/CI keep passing `scripted_flow` and never call Groq.

## Decisions locked

| Topic | Choice |
|---|---|
| Phase 2 slice | Planner + memory only (STT later) |
| Memory writes | Read-only this slice; test helpers upsert for tests |
| LLM vs scripted | `scripted_flow` wins if set; else Groq |
| LLM output | `{flow_id, spoken_response}` only — never invents `tool_calls` |

## Architecture

```
listening (still SCRIPTED_UTTERANCE this slice)
    → planning:
         if deps.scripted_flow is not None:
             expand graph.flow(*scripted_flow)  # unchanged; no Chroma/Groq
         else:
             query = last user transcript line (or joined recent lines)
             corrections = retrieve_corrections(product_id, query, page=page_id)
                 # tool_call_type=None at plan time — flow not chosen yet
             knowledge = retrieve_product_knowledge(product_id, query)
             FlowChoice = deps.choose_flow(...) or planner.choose_flow(...)
             validate flow_id ∈ page.flows (always, even for injectable)
             Plan(spoken_response, tool_calls=graph.flow(page_id, flow_id))
    → executing → verifying → speaking (unchanged)
```

Hard rules preserved:

- Site graph YAML is the only product-specific artifact for selectors/postconditions.
- Model never invents selectors or postconditions; it only picks among authored flows.
- One site-graph validator (`parse_site_graph`); unchanged.
- Chroma collection names stay namespaced by `product_id`.

## Components

### 1. `navigator/agent/planner.py` (new)

- Pydantic `FlowChoice(flow_id: str, spoken_response: str)`.
- `choose_flow(*, api_key, page_id, flow_ids, transcript, corrections, knowledge, persona) -> FlowChoice`.
- Calls Groq `llama-3.3-70b-versatile` with JSON response format; parse into `FlowChoice`.
- If `flow_id` not in `flow_ids`: one retry whose prompt restates the allowed set; still invalid → raise.
- Network/HTTP failures raise (no silent empty plan).

### 2. `navigator/agent/nodes/planning.py` (modify)

- If `deps.scripted_flow` set: keep current replay path (no retrieval, no Groq).
- Else LLM path:
  1. If `deps.choose_flow` is set, call it (tests). Else require a non-empty key (`deps.groq_api_key` or `settings.groq_api_key`); missing key → `RuntimeError` naming both fixes. Then call `planner.choose_flow`.
  2. Retrieve with `deps.chroma_path or settings.chroma_path`. Empty collections → `[]`. Client hard failures raise.
  3. After `FlowChoice`, if `flow_id` not in `page.flows` → raise (injectable fakes get no silent pass).
  4. Build `Plan` from `FlowChoice` + `graph.flow`; set `pending_calls`, `narration`, `transcript` as today.

### 3. `CallDeps` extensions

```python
scripted_flow: tuple[str, str] | None = None  # existing; wins if set
groq_api_key: str | None = None               # None → settings.groq_api_key
chroma_path: Path | None = None               # None → settings.chroma_path
choose_flow: Callable[..., FlowChoice] | None = None
# ponytail: injectable choose_flow for unit tests without network.
# Ceiling: one CallDeps field. Upgrade: fold into LLMProvider when Phase 4 lands.
```

### 4. Memory (`collections.py`, `retrieval.py`)

- `get_client(path)` → `chromadb.PersistentClient(path=str(path))`.
- `get_collection(path, product_id, kind)` → `get_or_create_collection(collection_name(...))`.
- `retrieve_corrections`: query with `where` on `page` and optionally `tool_call_type`; assert every result’s stored `product_id` matches the argument before return.
- `retrieve_product_knowledge`: query with no metadata filter; return document strings.
- Test-only upsert helpers live in `navigator/memory/seed.py`: write correction / knowledge docs with required metadata (`product_id`, `page`, `tool_call_type`, `source_call_id` for corrections). **Not** exposed on the HTTP API this slice.

### 5. Dependencies

Add `chromadb` and `groq` to the `dev` optional extra so CI `pytest` exercises real Chroma without a separate extras matrix. Keep `voice` / `memory` / `llm` extras for lean production installs.

No new FastAPI routes → committed OpenAPI / Fern / HTML docs stay valid; no `docs build` required for this slice.

## Error table

| Case | Behavior |
|---|---|
| `scripted_flow` set | No Groq, no Chroma; Phase 1 path |
| `choose_flow` injectable set | No Groq key required; still validate `flow_id` |
| No key, no injectable, no `scripted_flow` | `RuntimeError` naming both fixes |
| Unknown `flow_id` from model/injectable | `choose_flow` retries once (Groq only); planning always re-validates then raises |
| Empty `page.flows` | Raise before calling Groq |
| Groq HTTP / timeout | Raise |
| Empty Chroma collections | Empty retrieval lists; planning continues |

## Testing

| File | Covers |
|---|---|
| `tests/test_memory.py` | `collection_name` truncation/hash; upsert→retrieve; tenant assert; metadata filter |
| `tests/test_planner.py` | injectable `choose_flow` expands correct flow; unknown id rejected; `scripted_flow` wins; missing key raises |
| `tests/test_graph.py` | Existing scripted tests unchanged |

All nodes remain plain functions over `CallState` / `CallDeps`; no graph required in unit tests.

## Non-goals (explicit)

- Listening still emits `SCRIPTED_UTTERANCE`.
- No Attendee audio stream.
- No pending-review table / correction promotion.
- No product-knowledge ingest HTTP endpoint.
- No change to DemoRunner’s always-passing `scripted_flow` (live LLM path is available to any caller that omits it).

## Success criteria

1. `pytest -q` green with new memory + planner tests.
2. `python -m navigator.docs check` still up to date (no API drift).
3. `navigator.demo` / CI scripted path unchanged (no Groq key required).
4. With `scripted_flow=None`, fake `choose_flow`, and seeded Chroma, planning returns a valid `Plan` whose `tool_calls` equal `graph.flow(...)`.
