from navigator.automation.external_links import (
    explore_path_label,
    is_product_surface,
)


def test_about_blank_is_not_product_surface():
    assert not is_product_surface("about:blank", "https://app.example.com/")


def test_explore_path_label_drops_about_blank():
    assert explore_path_label("about:blank") == ""


def test_in_product_url_is_surface():
    assert is_product_surface(
        "https://app.example.com/dashboard/", "https://app.example.com/"
    )
