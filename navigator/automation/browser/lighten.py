"""Block third-party trackers/analytics in the demo browser (lighter, faster loads).

Only well-known tracking hosts are blocked — never the product's own assets,
fonts or APIs, so the UI renders exactly as the client sees it.
"""

from __future__ import annotations

import re

_TRACKERS = re.compile(
    r"(google-analytics\.com|googletagmanager\.com|doubleclick\.net|"
    r"connect\.facebook\.net|facebook\.com/tr|hotjar\.com|clarity\.ms|"
    r"segment\.(io|com)|mixpanel\.com|fullstory\.com|intercom\.io|"
    r"crisp\.chat|tawk\.to|hs-analytics\.net|hs-scripts\.com|"
    r"sentry\.io|newrelic\.com|nr-data\.net|bat\.bing\.com|"
    r"static\.ads-twitter\.com|snap\.licdn\.com|amplitude\.com)",
    re.I,
)


def block_trackers(context) -> None:
    def _route(route) -> None:
        try:
            if _TRACKERS.search(route.request.url):
                route.abort()
            else:
                route.continue_()
        except Exception:  # noqa: BLE001
            pass

    try:
        context.route("**/*", _route)
        print("[browser] tracker blocking on", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[browser] tracker blocking skipped: {exc}", flush=True)
