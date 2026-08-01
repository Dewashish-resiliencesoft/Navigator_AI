"""Local operator console — loopback-only SPA + API proxy.

The UI is a Vite/React app in `web/`; this module only guards access and serves
the build. Run `npm run build` in web/ to refresh what's served here.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]", "testserver"})

WEB_DIST = Path(__file__).parent / "web" / "dist"
WEB_ASSETS = WEB_DIST / "assets"

_MISSING_BUILD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<title>Navigator AI — client console</title>
<style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#09090b;
color:#fafafa;font:15px/1.6 ui-sans-serif,system-ui,sans-serif}
div{max-width:34rem;padding:2rem}
code{background:#27272a;padding:.15rem .4rem;border-radius:.3rem;font-size:.85em}
p{color:#a1a1aa}
</style></head><body><div>
<h1 style="letter-spacing:-.02em">Console not built</h1>
<p>The client console is a Vite app. Build it once, then reload:</p>
<p><code>cd navigator/client/web &amp;&amp; npm install &amp;&amp; npm run build</code></p>
</div></body></html>
"""


def client_index_html() -> str:
    index = WEB_DIST / "index.html"
    try:
        return index.read_text(encoding="utf-8")
    except OSError:
        return _MISSING_BUILD_HTML


def is_local_ops_host(request: Request) -> bool:
    """Allow only localhost Host — blocks public tunnel Hostnames."""
    raw = (request.headers.get("host") or "").strip().lower()
    host = raw.split("%", 1)[0].split(":", 1)[0]
    return host in _LOCAL_HOSTS


def require_local_ops(request: Request) -> None:
    if not is_local_ops_host(request):
        raise HTTPException(403, "client dashboard is local-only (open via localhost)")
