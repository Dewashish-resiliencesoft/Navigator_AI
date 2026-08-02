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
    include_login_in_default_flow: bool = False,
) -> None:
    """Reject flows that still target login / credential fields.

    Raises SiteGraphError with a Client-facing message: re-record after login,
    not a generic validation failure. Topic flows never get the Default-flow
    exception -- a mid-demo Topic must not log the End User out.
    """
    from navigator.core.schemas import FillField, tool_selector
    from navigator.knowledge.site_graph import SiteGraphError

    default = graph.primary_flow()
    for page_id, page in graph.pages.items():
        page_url = graph.url_for(page_id)
        for flow_id, calls in page.flows.items():
            is_default = default == (page_id, flow_id)
            allow = include_login_in_default_flow and is_default
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
                    kind = "Default" if is_default else "Topic"
                    raise SiteGraphError(
                        f"{kind} flow {flow_id!r} on page {page_id!r} step {i} "
                        f"{reason}. Re-record starting after login — login belongs "
                        "in Product Login, not in the flow. "
                        "(Default flow only: turn on 'Include login as part of the "
                        "Default flow's demo' if you intentionally want to show it.)"
                    )
