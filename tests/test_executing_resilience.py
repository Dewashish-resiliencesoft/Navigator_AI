"""A missing element must never crash the live demo graph."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from navigator.agent.nodes import executing as ex


def _call(sel, tool="click_element"):
    return SimpleNamespace(selector=sel, tool=tool)


def test_login_detection():
    assert ex._is_login_call(_call("text=Login"))
    assert ex._is_login_call(_call("password", "fill_field"))
    assert not ex._is_login_call(_call("go_to_home"))


def _page(url, pw_visible=False):
    pg = MagicMock()
    pg.url = url
    # _already_authenticated treats a truthy evaluate() as a visible Log in
    # button. A bare MagicMock is truthy, so every page looked signed out.
    pg.evaluate.return_value = False
    loc = MagicMock(); loc.count.return_value = 1 if pw_visible else 0
    loc.nth.return_value.is_visible.return_value = pw_visible
    pg.locator.return_value = loc
    return pg


def test_authenticated_heuristic():
    assert ex._already_authenticated(_page("https://x.com/dashboard/"))
    assert not ex._already_authenticated(_page("https://x.com/login/"))
    assert not ex._already_authenticated(_page("https://x.com/", pw_visible=True))


def test_recover_skips_login_when_signed_in():
    deps = SimpleNamespace(page=_page("https://x.com/dashboard/"), graph=MagicMock(),
                           speaker=MagicMock(), push_frame=None)
    res, pid = ex._recover_failed_step(deps, "home", _call("text=Login"),
                                       RuntimeError("no bounding box for text=Login"))
    assert res.ok and "already signed in" in res.detail and pid == "home"


def test_recover_skips_unknown_with_bridge_never_raises():
    pg = _page("https://x.com/dashboard/")
    pg.get_by_role.return_value.count.return_value = 0
    pg.get_by_text.return_value.count.return_value = 0
    deps = SimpleNamespace(page=pg, graph=MagicMock(), speaker=MagicMock(spec=["say"]),
                           push_frame=None)
    res, _ = ex._recover_failed_step(deps, "home", _call("text=Reports"), RuntimeError("no bounding box"))
    assert res.ok and res.detail.startswith("skipped")
    deps.speaker.say.assert_called_once()
