"""Company bio structured fields."""

from navigator.knowledge import company_bio as cb


def test_default_bio_has_core_fields():
    bio = cb.default_bio()
    keys = [f["key"] for f in bio["fields"]]
    assert "company_name" in keys
    assert "about" in keys
    assert "products" in keys


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(cb, "_ROOT", tmp_path)
    saved = cb.save_bio(
        "acme",
        {
            "fields": [
                {"key": "company_name", "label": "Company name", "value": "Acme"},
                {"key": "owner", "label": "Owner", "value": "Ada"},
                {"key": "custom_x", "label": "Region", "value": "EU"},
            ]
        },
    )
    assert saved["fields"][0]["value"] == "Acme"
    loaded = cb.load_bio("acme")
    assert loaded["fields"][2]["label"] == "Region"
    assert loaded["fields"][2]["value"] == "EU"


def test_format_skips_empty_values():
    md = cb.format_bio_markdown(
        {
            "fields": [
                {"key": "company_name", "label": "Company name", "value": "Acme"},
                {"key": "about", "label": "About", "value": ""},
            ]
        }
    )
    assert "Company name" in md
    assert "Acme" in md
    assert "About" not in md
