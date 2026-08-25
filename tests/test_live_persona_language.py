"""Tests: live persona builds correct language instruction."""
from __future__ import annotations

from unittest.mock import MagicMock


def _make_graph(name: str = "TestProduct"):
    from navigator.core.schemas import Persona

    persona = Persona(
        agent_name="Navigator",
        product_name=name,
        one_liner=f"{name} helps teams collaborate.",
    )
    graph = MagicMock()
    graph.pages = {}
    graph.site = "test"
    graph.effective_persona = MagicMock(return_value=persona)
    graph.base_url = "https://example.com"
    return graph


def test_hindi_instruction_does_not_ask_language_preference():
    from navigator.voice.live_persona import build_live_instruction

    instr = build_live_instruction(graph=_make_graph(), language="hi", gender="female")
    # Must NOT contain language preference question
    assert "prefer" not in instr.lower() or "hindi" in instr.lower()
    assert "Do NOT ask" in instr or "not ask" in instr.lower() or "default language" in instr.lower()
    # Must say Hindi is the default
    assert "hindi" in instr.lower()


def test_english_instruction_does_not_ask_language_preference():
    from navigator.voice.live_persona import build_live_instruction

    instr = build_live_instruction(graph=_make_graph(), language="en", gender="female")
    assert "Never ask which language" in instr or "not ask" in instr.lower()


def test_hindi_instruction_says_start_in_hindi():
    from navigator.voice.live_persona import build_live_instruction

    instr = build_live_instruction(graph=_make_graph(), language="hi", gender="female")
    assert "hindi" in instr.lower()
    # Must tell model to start in Hindi
    assert "start" in instr.lower() or "default" in instr.lower()
