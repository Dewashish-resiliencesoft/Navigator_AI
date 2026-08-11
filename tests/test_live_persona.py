"""build_live_instruction must scope the Live agent to the Client's product only."""

from __future__ import annotations

from navigator.knowledge.site_graph import PageSpec, Persona, SiteGraph
from navigator.voice.live_persona import build_live_instruction


def _graph(*, site: str = "acme-crm", persona: Persona | None = None) -> SiteGraph:
    return SiteGraph(
        version=1,
        site=site,
        base_url="https://example.test",
        pages={
            "home": PageSpec(name="Dashboard", url="/", selectors={}),
            "deals": PageSpec(name="Deals", url="/deals", selectors={}),
        },
        persona=persona,
    )


def test_uses_client_product_name_and_pages():
    text = build_live_instruction(
        graph=_graph(persona=Persona(product_name="Acme CRM")),
        product_brief="Acme CRM tracks deals.",
    )
    assert "Acme CRM" in text
    assert "Dashboard" in text and "Deals" in text
    assert "Acme CRM tracks deals." in text


def test_no_platform_or_tenant_names_leak_in():
    text = build_live_instruction(graph=_graph(), product_brief="")
    lowered = text.lower()
    for banned in ("navigator", "resiliencesoft", "resiliohub", "gemini", "google"):
        assert banned not in lowered, f"{banned!r} leaked into the instruction"


def test_empty_brief_restricts_to_what_is_on_screen():
    text = build_live_instruction(graph=_graph(), product_brief="")
    assert "visibly on screen" in text


def test_off_topic_must_be_declined_not_answered():
    text = build_live_instruction(graph=_graph(), product_brief="A CRM.")
    assert "Do not answer" in text
    assert "back to the demo" in text


def test_hindi_switches_spoken_language():
    text = build_live_instruction(graph=_graph(), product_brief="A CRM.", language="hi")
    assert "Hindi" in text


def test_placeholder_product_name_falls_back_to_site_slug():
    text = build_live_instruction(graph=_graph(site="acme-crm"), product_brief="")
    assert "Acme Crm" in text or "acme crm" in text.lower()
