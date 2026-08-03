"""Call-scoped memory and a coarse pacing signal.

Lives on CallDeps rather than CallState: one instance per call, mutated in place,
so there is no reducer to get wrong and nothing extra to serialise. A call is
already the natural lifetime -- the object is constructed when the demo starts
and discarded when it ends.

Two consumers:
  * the decision step, so it doesn't re-run a flow the prospect already saw
  * phrasing, so it can refer back to earlier moments instead of repeating itself
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Coarse per-turn read on how the call is going.
Pacing = str  # engaged | neutral | rushed | confused

_HESITATION = re.compile(
    r"\b(um+|uh+|erm+|hmm+|wait|sorry|huh|what\?|confus\w*|"
    r"don'?t (?:get|understand|follow)|not sure what|lost)\b",
    re.I,
)
_RUSHED = re.compile(
    r"\b(skip|next|move on|faster|hurry|quick(?:ly)?|get to|"
    r"fast forward|already know|yeah yeah|go ahead)\b",
    re.I,
)
_ENGAGED = re.compile(
    r"\b(how|why|what if|can (?:i|we|you)|does it|show me|tell me more|"
    r"interesting|nice|great|love|curious|and then)\b",
    re.I,
)


@dataclass
class CallMemory:
    """What has already happened on this call."""

    flows_executed: list[str] = field(default_factory=list)
    topics_covered: list[str] = field(default_factory=list)
    facts_stated: list[str] = field(default_factory=list)
    spoken_lines: list[str] = field(default_factory=list)
    pacing_history: list[Pacing] = field(default_factory=list)
    #: Turns where the prospect spoke instead of letting the walkthrough advance.
    interruptions: int = 0
    turns: int = 0

    # -- recording ------------------------------------------------------------

    def note_flow(self, flow_id: str) -> None:
        if flow_id and flow_id not in self.flows_executed:
            self.flows_executed.append(flow_id)

    def note_topic(self, topic: str) -> None:
        key = topic.strip().lower()
        if key and key not in self.topics_covered:
            self.topics_covered.append(key)

    def note_fact(self, fact: str) -> None:
        text = fact.strip()
        if text and text not in self.facts_stated:
            self.facts_stated.append(text)

    def note_spoken(self, line: str) -> None:
        text = line.strip()
        if text:
            self.spoken_lines.append(text)

    def note_turn(self, utterance: str) -> Pacing:
        """Record one turn and return its pacing read."""
        self.turns += 1
        if utterance.strip():
            self.interruptions += 1
        signal = self.classify_pacing(utterance)
        self.pacing_history.append(signal)
        return signal

    # -- reading --------------------------------------------------------------

    def has_covered_flow(self, flow_id: str) -> bool:
        return flow_id in self.flows_executed

    def has_covered_topic(self, topic: str) -> bool:
        return topic.strip().lower() in self.topics_covered

    def recent_spoken(self, n: int = 3) -> list[str]:
        return self.spoken_lines[-n:]

    def classify_pacing(self, utterance: str) -> Pacing:
        """Cheap per-turn read: engaged | neutral | rushed | confused.

        Heuristics on purpose. A sentiment model here would be a second thing to
        debug when narration paces badly, and the only decisions downstream are
        "offer to slow down" and "offer to skip ahead" -- both recoverable, and
        both something the prospect can override by saying so.
        """
        text = (utterance or "").strip()
        if not text:
            return "neutral"
        if _HESITATION.search(text):
            return "confused"
        if _RUSHED.search(text):
            return "rushed"
        words = len(text.split())
        if _ENGAGED.search(text) or words >= 8:
            return "engaged"
        # Terse and featureless: several in a row is what "rushed" looks like
        # when nobody says "skip" out loud.
        if words <= 3 and self.pacing_history[-2:] == ["rushed", "rushed"]:
            return "rushed"
        return "neutral"

    def summary(self) -> str:
        """One compact block for an LLM prompt. Empty when nothing has happened."""
        parts: list[str] = []
        if self.flows_executed:
            parts.append(f"Already demoed: {', '.join(self.flows_executed)}")
        if self.topics_covered:
            parts.append(f"Already discussed: {', '.join(self.topics_covered)}")
        if self.facts_stated:
            parts.append("Already told them: " + " | ".join(self.facts_stated[-5:]))
        if self.spoken_lines:
            parts.append(
                "Your last lines (do NOT repeat these near-verbatim): "
                + " | ".join(self.recent_spoken())
            )
        return "\n".join(parts)
