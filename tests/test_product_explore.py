"""Product Explore + dual knowledge merge (non-demo topology)."""

from __future__ import annotations

from pathlib import Path

import yaml

from navigator.automation.product_explore import (
    _is_noise_result_host,
    _same_origin,
    _unwrap_search_url,
)
from navigator.knowledge.company_bio import load_bio, save_bio
from navigator.knowledge.knowledge_merge import (
    auto_merge_knowledge,
    load_knowledge_bundle,
    save_explore_markdown,
    save_user_markdown,
)
from navigator.knowledge.topology import load_topology, save_topology


def test_unwrap_google_url():
    wrapped = "https://www.google.com/url?q=https%3A%2F%2Fexample.com%2Fabout&sa=U"
    assert _unwrap_search_url(wrapped) == "https://example.com/about"
    ddg = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Facme.io%2Fpricing"
    assert _unwrap_search_url(ddg) == "https://acme.io/pricing"
    plain = "https://acme.io/docs"
    assert _unwrap_search_url(plain) == plain


def test_noise_and_same_origin_skip():
    assert _is_noise_result_host("www.google.com")
    assert _is_noise_result_host("youtube.com")
    assert _is_noise_result_host("acme.example", product_host="acme.example")
    assert not _is_noise_result_host("linkedin.com")
    assert not _is_noise_result_host("crunchbase.com")
    assert _same_origin("https://acme.io/a", "https://acme.io/b")
    assert not _same_origin("https://acme.io/", "https://other.io/")


def test_dual_knowledge_auto_merge_without_llm(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "navigator.knowledge.knowledge_merge._ROOT", tmp_path
    )
    save_user_markdown("acme", "# User\n\nWe sell widgets.")
    save_explore_markdown("acme", "# Explore\n\nDashboard has Inbox.")
    bundle = auto_merge_knowledge("acme")
    assert "widgets" in (bundle["markdown"] or "")
    assert "Inbox" in (bundle["markdown"] or "")
    assert bundle["merged_at"]


def test_topology_save_load_no_playlist(tmp_path, monkeypatch):
    monkeypatch.setattr("navigator.knowledge.topology._ROOT", tmp_path)
    raw = {
        "site": "acme",
        "base_url": "https://example.com/",
        "pages": {"home": {"url": "https://example.com/", "selectors": {}, "flows": {}}},
        "demo_playlist": [],
        "_meta": {"non_demo": True},
    }
    save_topology("acme", yaml.safe_dump(raw), page_count=1)
    loaded = load_topology("acme")
    assert loaded["page_count"] == 1
    assert "home" in loaded["yaml"]
    assert "demo_playlist: []" in loaded["yaml"] or "demo_playlist:\n" in loaded["yaml"]


def test_bio_fill_empty_only(tmp_path, monkeypatch):
    monkeypatch.setattr("navigator.knowledge.company_bio._ROOT", tmp_path)
    save_bio(
        "acme",
        {
            "fields": [
                {"key": "company_name", "label": "Company name", "value": "Acme"},
                {"key": "website", "label": "Website", "value": ""},
            ]
        },
    )
    bio = load_bio("acme")
    fields = bio["fields"]
    by_key = {f["key"]: f for f in fields}
    # simulate explore fill
    if not by_key["website"]["value"]:
        by_key["website"]["value"] = "https://example.com"
    save_bio("acme", {"fields": list(by_key.values())})
    again = load_bio("acme")
    vals = {f["key"]: f["value"] for f in again["fields"]}
    assert vals["company_name"] == "Acme"
    assert vals["website"] == "https://example.com"


def test_bio_value_is_weak():
    from navigator.automation.product_explore import _bio_value_is_weak

    assert _bio_value_is_weak("about", "")
    assert _bio_value_is_weak("about", "Acme", company="Acme")
    assert _bio_value_is_weak("about", "short blurb")
    assert not _bio_value_is_weak(
        "about",
        "We help mid-market teams automate customer onboarding end to end.",
        company="Acme",
    )
    assert not _bio_value_is_weak("website", "https://acme.io")  # non-enrichable nonempty


def test_fill_bio_enriches_weak_leaves_long(tmp_path, monkeypatch):
    from navigator.automation.product_explore import (
        ProductExploreJob,
        _fill_bio_from_explore,
    )

    monkeypatch.setattr("navigator.knowledge.company_bio._ROOT", tmp_path)
    long_about = (
        "We help mid-market teams automate customer onboarding with AI "
        "workflows that connect CRM and support tools."
    )
    save_bio(
        "acme",
        {
            "fields": [
                {"key": "company_name", "label": "Company name", "value": "Acme"},
                {"key": "about", "label": "About", "value": "Acme"},  # weak
                {"key": "industry", "label": "Industry", "value": ""},
                {
                    "key": "usp",
                    "label": "USP",
                    "value": long_about,  # long user text — leave alone
                },
            ]
        },
    )

    def fake_brain(system: str, user: str) -> str:
        return '{"about": "Acme builds CRM automation for SMBs.", "industry": "SaaS"}'

    monkeypatch.setattr(
        "navigator.automation.product_explore._brain_complete", fake_brain
    )
    job = ProductExploreJob(
        job_id="j1", product_id="acme", start_url="https://acme.example"
    )
    _fill_bio_from_explore(
        "acme",
        start_url="https://acme.example",
        company="Acme",
        corpus="Acme dashboard Inbox reports",
        web_notes=["### Acme\nSaaS CRM platform"],
        page_ids=["home", "inbox"],
        job=job,
    )
    vals = {f["key"]: f["value"] for f in load_bio("acme")["fields"]}
    assert "CRM" in vals["about"] or "automate" in vals["about"].lower()
    assert vals["about"] != "Acme"
    assert vals["industry"] == "SaaS"
    assert vals["usp"] == long_about


def test_ack_clears_done_job():
    from navigator.automation import product_explore as pe

    prev = pe._active
    job = pe.ProductExploreJob(
        job_id="j2", product_id="acme", start_url="https://acme.example"
    )
    job.done = True
    job.phase = "done"
    pe._active = job
    try:
        out = pe.ack_job(product_id="acme")
        assert pe._active is None
        assert out.get("active") is False
    finally:
        pe._active = prev


def test_knowledge_bundle_migrates_canonical_to_user(tmp_path, monkeypatch):
    monkeypatch.setattr("navigator.knowledge.knowledge_merge._ROOT", tmp_path)
    (tmp_path / "acme.md").write_text("# Old brief\n", encoding="utf-8")
    bundle = load_knowledge_bundle("acme")
    assert "Old brief" in (bundle["user_markdown"] or "")
