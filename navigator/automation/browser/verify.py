"""Postcondition checking: declared expectation vs actual DOM state.

No LLM here, ever. A postcondition is a mechanical assertion about the DOM, and
keeping it mechanical is what makes VERIFYING cheap enough to run after every
single tool call.

When the DOM answer is genuinely unreadable -- element exists but the property
being asserted can't be obtained -- the result is marked `ambiguous` and a later
phase escalates that one case to a vision model. Ambiguous should be rare; if it
is firing often, the site graph's postconditions are wrong.
"""

from __future__ import annotations

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from navigator.knowledge.site_graph import SiteGraph, SiteGraphError
from navigator.core.schemas import Postcondition, VerifyResult


def check(
    page: Page, graph: SiteGraph, page_id: str, expects: Postcondition
) -> VerifyResult:
    """Check one postcondition. Never raises."""
    try:
        return _dispatch(page, graph, page_id, expects)
    except SiteGraphError as exc:
        # An unresolvable alias is a config bug, not an ambiguous page.
        return VerifyResult(passed=False, actual=f"site graph error: {exc}")
    except PlaywrightError as exc:
        line = str(exc).strip().splitlines()[0]
        return VerifyResult(passed=False, actual=f"playwright error: {line}")


def _dispatch(
    page: Page, graph: SiteGraph, page_id: str, expects: Postcondition
) -> VerifyResult:
    if expects.check == "url_matches":
        actual = page.url
        return VerifyResult(passed=expects.expected in actual, actual=actual)

    css = graph.selector(page_id, expects.selector)  # type: ignore[arg-type]

    if expects.check == "element_count":
        # Count only what a user could actually see.
        count = sum(
            1 for el in page.query_selector_all(css) if el.is_visible()
        )
        return VerifyResult(
            passed=count == int(expects.expected),  # type: ignore[arg-type]
            actual=f"{count} visible element(s) match {css}",
        )

    if expects.check == "hidden":
        try:
            page.wait_for_selector(css, state="hidden", timeout=expects.timeout_ms)
            return VerifyResult(passed=True, actual=f"{css} is hidden or absent")
        except PlaywrightError:
            return VerifyResult(passed=False, actual=f"{css} is still visible")

    # Remaining checks all need the element to be there first.
    try:
        page.wait_for_selector(css, state="visible", timeout=expects.timeout_ms)
    except PlaywrightError:
        present = page.query_selector(css) is not None
        return VerifyResult(
            passed=False,
            actual=(
                f"{css} exists but never became visible"
                if present
                else f"{css} not found"
            ),
            # Present-but-not-visible is the ambiguous case: something is there,
            # and only a look at the rendered page can say what.
            ambiguous=present,
        )

    if expects.check == "visible":
        return VerifyResult(passed=True, actual=f"{css} is visible")

    element = page.query_selector(css)
    if element is None:  # raced away between wait and query
        return VerifyResult(
            passed=False, actual=f"{css} disappeared after becoming visible"
        )

    if expects.check == "text_contains":
        text = element.inner_text()
        if not text.strip():
            return VerifyResult(
                passed=False,
                actual=f"{css} has no text",
                # Empty text on a visible element: could be an icon, an image, or
                # a genuinely blank state. Not decidable from the DOM.
                ambiguous=True,
            )
        return VerifyResult(passed=expects.expected in text, actual=text)

    if expects.check == "value_equals":
        value = element.input_value()
        return VerifyResult(passed=value == expects.expected, actual=value)

    raise AssertionError(f"unhandled check kind: {expects.check}")


def check_with_vision(
    page: Page, graph: SiteGraph, page_id: str, expects: Postcondition
) -> VerifyResult:
    """Escalation path for VerifyResult.ambiguous: screenshot -> vision model.

    Deliberately not wired into `check`. It costs an API call and must stay the
    exception, not the default.
    """
    from navigator.agent.providers import get_provider

    css = None
    if expects.selector is not None:
        try:
            css = graph.selector(page_id, expects.selector)
        except SiteGraphError as exc:
            return VerifyResult(passed=False, actual=f"site graph error: {exc}")

    try:
        if css:
            loc = page.locator(css).first
            png = loc.screenshot(type="png")
        else:
            png = page.screenshot(type="png", full_page=False)
    except PlaywrightError as exc:
        return VerifyResult(
            passed=False, actual=f"screenshot failed: {exc}", ambiguous=True
        )

    system = (
        "You verify a UI postcondition from a screenshot. "
        "Reply with exactly PASSED or FAILED on the first line, then one short reason."
    )
    user = (
        f"check={expects.check}\n"
        f"selector_alias={expects.selector}\n"
        f"expected={expects.expected}\n"
        "Does the screenshot show the expected state?"
    )
    try:
        raw = get_provider().complete_with_image(system, user, png)
    except Exception as exc:  # noqa: BLE001
        return VerifyResult(
            passed=False, actual=f"vision provider error: {exc}", ambiguous=True
        )
    first = (raw.strip().splitlines() or [""])[0].upper()
    passed = first.startswith("PASSED") or first.startswith("YES")
    return VerifyResult(passed=passed, actual=raw.strip()[:500], ambiguous=False)
