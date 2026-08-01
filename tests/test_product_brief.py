"""Product brief loader — ResilioHub expert knowledge for demos."""

from navigator.config.product_brief import load_product_brief


def test_load_resiliohub_brief():
    text = load_product_brief("resiliohub")
    assert "ResilioHub" in text
    assert "WhatsApp" in text
    assert "₹1,499" in text or "1499" in text
    assert "ResilienceSoft" in text


def test_missing_brief_is_empty():
    assert load_product_brief("no-such-product-xyz") == ""
