"""Is this a login step? One definition, used by three callers.

The recorder uses it to keep login out of a recorded flow, save/activate
validation uses it to reject a flow that snuck one in anyway, and the runtime
uses it to tell "the session expired" apart from "this step is broken".

Those three must agree, so they share this module. A product's login page can
change between a recording and a demo, so callers pass the *current* config --
never a value cached when the flow was recorded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from navigator.knowledge.site_graph import SiteGraph

#: Written into a recorded fill_field instead of the typed secret. EXECUTING
#: swaps this for the vault password at the moment Playwright needs it.
VAULT_PASSWORD_SENTINEL = "__NAV_VAULT_PASSWORD__"

_PASSWORD_AUTOCOMPLETE = frozenset({"current-password", "new-password"})

_SELECTOR_HINT = re.compile(r"passw(or)?d|pwd|sign[-_]?in|log[-_]?in\b", re.I)
"""Deliberately narrow. `email`, `user`, and a bare `login` are excluded: an
email input inside a legitimate flow (invite a teammate) is ordinary, and
`login` shows up in plenty of non-auth class names. The password-type check
below is the real catcher; this only backstops products that never set
type="password"."""

_DENIED_HINT = re.compile(
    r"access denied|permission denied|forbidden|not authori[sz]ed|"
    r"you (do not|don't) have (access|permission)",
    re.I,
)

_LOGIN_FLOW_NAME = re.compile(
    r"(login|log_in|sign_in|signin|auth|authentication|signup|sign_up|onboard)",
    re.I,
)


def playlist_has_login_flow(graph: "SiteGraph") -> bool:
    """True when demo_playlist already includes a recorded sign-in walkthrough.

    When True, the runtime must not pre-authenticate — the recorded flow is what
    the prospect sees, and narration must match the login UI on screen.
    """
    for item in sorted(graph.demo_playlist or [], key=lambda x: x.order):
        if flow_is_login_walkthrough(graph, item.page_id, item.flow_id, item.name):
            return True
    return False


def demo_playlist_for_toggle(graph: "SiteGraph", *, include_login: bool):
    """Playlist the live demo should run, honoring Show-login toggle.

    Off → drop recorded login/onboarding rows (silent vault sign-in instead).
    On → keep them so the prospect sees sign-in on screenshare.
    """
    items = sorted(graph.demo_playlist or [], key=lambda x: x.order)
    if include_login:
        return tuple(items)
    kept = [
        it
        for it in items
        if not flow_is_login_walkthrough(graph, it.page_id, it.flow_id, it.name)
    ]
    return tuple(it.model_copy(update={"order": i}) for i, it in enumerate(kept, start=1))


def login_flow_hidden_from_demo(graph: "SiteGraph", page_id: str, flow_id: str) -> bool:
    """True when this is a login/onboarding walkthrough dropped from the live playlist."""
    fid = (flow_id or "").strip()
    if not fid:
        return False
    if not flow_is_login_walkthrough(graph, page_id, fid):
        return False
    return not graph.flow_in_playlist(page_id, fid)


def live_start_flow(
    graph: "SiteGraph",
    page_id: str,
    flow_id: str,
    *,
    include_login: bool,
) -> tuple[str, str]:
    """Where auto-play should start after the login toggle is applied.

    Filtered playlist wins. Never auto-start a dropped login/onboarding flow.
    """
    first = graph.primary_flow()
    if first:
        return first
    pid, fid = page_id, (flow_id or "").strip()
    if fid and not (
        (not include_login) and flow_is_login_walkthrough(graph, pid, fid)
    ):
        return pid, fid
    return pid, ""


def name_suggests_login_walkthrough(
    flow_id: str = "",
    flow_name: str = "",
) -> bool:
    """True when flow id/name looks like an intentional sign-in recording."""
    fid = (flow_id or "").strip()
    if fid and _LOGIN_FLOW_NAME.search(fid):
        return True
    name = (flow_name or "").strip()
    return bool(name and _LOGIN_FLOW_NAME.search(name))


def flow_is_login_walkthrough(
    graph: "SiteGraph",
    page_id: str,
    flow_id: str,
    flow_name: str | None = None,
) -> bool:
    """True when this playlist entry is an intentional recorded sign-in demo."""
    fid = (flow_id or "").strip()
    if not fid:
        return False
    if name_suggests_login_walkthrough(fid, flow_name):
        return True
    # Match playlist row by id even when only the name was set at record time.
    for item in graph.demo_playlist or []:
        if item.flow_id != fid or item.page_id != page_id:
            continue
        if _LOGIN_FLOW_NAME.search(item.name or ""):
            return True
    return False


@dataclass(frozen=True)
class LoginConfig:
    """What counts as this product's login, right now.

    Read fresh at each call. `login_url` is optional -- a product may have no
    configured login at all, in which case only the field-shape rules apply.
    """

    login_url: str = ""


def _path_of(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    path = (parsed.path or "/").rstrip("/")
    return (path or "/").lower()


def is_password_field(el: dict) -> bool:
    """A credential input, by its own declaration.

    Highest-confidence signal and the only unconditional one: a browser only
    reports type="password" for a field the product itself marked secret.
    """
    if str(el.get("type") or "").strip().lower() == "password":
        return True
    autocomplete = str(el.get("autocomplete") or "").strip().lower()
    return autocomplete in _PASSWORD_AUTOCOMPLETE


def is_login_url(url: str, config: LoginConfig) -> bool:
    """Compare paths only -- a product's login lives on its own host."""
    if not config.login_url or not url:
        return False
    return _path_of(url) == _path_of(config.login_url)


def same_page_path(a: str, b: str) -> bool:
    """True when two absolute URLs share a path (host ignored)."""
    return bool(a and b) and _path_of(a) == _path_of(b)


def is_sub_route(current: str, base: str) -> bool:
    """True when ``current`` is a deeper route under ``base`` (host ignored).

    A demo click that drills from /inbox into /inbox/message/7 has not left the
    page the flow declared, so it must not be treated as a detour to undo.
    """
    cur, root = _path_of(current), _path_of(base)
    if not cur or not root or root == "/":
        return False
    return cur.startswith(root + "/")


def looks_like_login(
    *,
    config: LoginConfig,
    element: dict | None = None,
    url: str = "",
    selector: str = "",
) -> str | None:
    """Why this looks like a login step, or None if it does not.

    The string is shown to the Client, so it says what matched rather than just
    that something did.
    """
    if element is not None and is_password_field(element):
        return "targets a password field"
    if is_login_url(url, config):
        return f"runs on the product's login page ({config.login_url})"
    if selector and _SELECTOR_HINT.search(selector):
        return f"targets {selector!r}, which looks like a credential field"
    return None


def looks_like_permission_denied(*, page_text: str = "", url: str = "") -> bool:
    """An access-denied page is not an expired session.

    Retrying login here would loop: the credentials are fine, the account just
    cannot see this page. Callers must fail the postcondition normally instead.
    """
    if _DENIED_HINT.search(page_text or ""):
        return True
    return bool(re.search(r"/(403|forbidden|access-denied|no-access)\b", url or "", re.I))


def assert_no_login_in_graph(
    graph: SiteGraph,
    config: LoginConfig,
    *,
    allow_flows: frozenset[tuple[str, str]] | None = None,
) -> None:
    """Reject flows that still target login / credential fields.

    Raises SiteGraphError with a Client-facing message: re-record after login,
    not a generic validation failure. Playlist flows may include sign-in when
    recorded intentionally; topic flows (not in demo_playlist) must not.
    """
    from navigator.core.schemas import FillField, tool_selector
    from navigator.knowledge.site_graph import SiteGraphError

    for page_id, page in graph.pages.items():
        page_url = graph.url_for(page_id)
        for flow_id, calls in page.flows.items():
            playlist_name = next(
                (
                    item.name
                    for item in (graph.demo_playlist or [])
                    if item.page_id == page_id and item.flow_id == flow_id
                ),
                None,
            )
            in_playlist = any(
                item.page_id == page_id and item.flow_id == flow_id
                for item in (graph.demo_playlist or [])
            )
            allow = (
                flow_is_login_walkthrough(graph, page_id, flow_id, playlist_name)
                or in_playlist
                or (
                    allow_flows is not None
                    and (page_id, flow_id) in allow_flows
                )
            )
            for i, call in enumerate(calls):
                sel = tool_selector(call) or ""
                reason = looks_like_login(
                    config=config, url=page_url, selector=sel
                )
                # Navigate to a page whose own URL is the login path.
                if call.tool == "navigate":
                    reason = reason or looks_like_login(
                        config=config, url=graph.url_for(call.page_id)
                    )
                if isinstance(call, FillField):
                    # Literal secrets never belong in YAML — even with the
                    # Default-flow toggle on. Sentinel is the only allowed value.
                    if (
                        call.value
                        and call.value != VAULT_PASSWORD_SENTINEL
                        and sel
                        and _SELECTOR_HINT.search(sel)
                    ):
                        raise SiteGraphError(
                            f"page {page_id!r} flow {flow_id!r} step {i}: "
                            "credential values must never be stored in a site "
                            "graph. Re-record the flow starting after login; "
                            "Navigator types the password from the Product "
                            "Login vault at demo time."
                        )
                    if call.value == VAULT_PASSWORD_SENTINEL and not allow:
                        reason = reason or "targets a password field"
                if reason and not allow:
                    kind = "Playlist" if in_playlist else "Topic"
                    hint = (
                        " This flow is a recorded sign-in walkthrough — keep it "
                        "in the demo playlist with a login/auth name."
                        if _LOGIN_FLOW_NAME.search(flow_id or "")
                        or _LOGIN_FLOW_NAME.search(playlist_name or "")
                        else " Re-record starting after login — login belongs "
                        "in Product Login, not in a topic flow. Add the flow to "
                        "the demo playlist if sign-in should be part of the demo."
                    )
                    raise SiteGraphError(
                        f"{kind} flow {flow_id!r} on page {page_id!r} step {i} "
                        f"{reason}.{hint}"
                    )
