"""Retrieval at planning time.

STUB. Phase 2 fills this in.

Two things matter more than the similarity search:

  product_id -- every function takes it, and it is not optional. A correction
    retrieved from the wrong product's collection would be silently injected into
    a live sales call, so this is not a parameter to default.

  metadata filters -- a correction about the inbox page's send button is noise
    when the agent is on a settings page. `page` and `tool_call_type` are applied
    before ranking, not after.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Correction(BaseModel):
    """An approved corrective rule, as stored in a product's corrections
    collection."""

    model_config = ConfigDict(frozen=True)

    rule: str
    product_id: str
    page: str
    tool_call_type: str
    """One of the ToolCall `tool` literals."""
    source_call_id: str
    """The ActionLog entry this was learned from -- keeps rules traceable back to
    the failure that produced them."""


def retrieve_corrections(
    product_id: str,
    query: str,
    page: str,
    tool_call_type: str | None = None,
    k: int = 5,
) -> list[Correction]:
    # TODO(phase 2): query collection_name(product_id, "corrections") with
    # where={"page": page, "tool_call_type": tool_call_type} (drop the second key
    # when None), n_results=k. Assert every result's product_id matches before
    # returning -- belt and braces on the tenant boundary.
    raise NotImplementedError("correction retrieval lands in Phase 2")


def retrieve_product_knowledge(product_id: str, query: str, k: int = 5) -> list[str]:
    # TODO(phase 2): query collection_name(product_id, "product_knowledge"),
    # no metadata filter.
    raise NotImplementedError("product knowledge retrieval lands in Phase 2")
