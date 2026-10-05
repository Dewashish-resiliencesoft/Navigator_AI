"""Local / LAN operator console — SPA + API proxy.

The UI is a Vite/React app in `web/`; this module only guards access and serves
the build. Run `npm run build` in web/ to refresh what's served here.

Access: loopback, RFC1918 private LAN Hosts, and the hostname of
``settings.public_base_url`` (the quick tunnel this process opened). Any other
public Host stays blocked.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path
from urllib.parse import urlparse

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


def _is_private_lan_host(host: str) -> bool:
    """True for RFC1918 / link-local / ULA — LAN VPS Host headers."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
    )


def _active_tunnel_host() -> str:
    """Hostname of the public origin this process is serving, if any."""
    from navigator.core.settings import settings

    base = (settings.public_base_url or "").strip()
    if not base:
        return ""
    return (urlparse(base).hostname or "").strip().lower()


def is_local_ops_host(request: Request) -> bool:
    """Allow localhost, private LAN, the active tunnel host, or a loopback peer.

    Peer-loopback covers same-machine opens via hostname (e.g.
    ``http://resiliencesoft:8000/client``) that would 403 on Host-only checks.
    A quick-tunnel Host is allowed only when it is this process's
    ``public_base_url``. Other public Hosts stay blocked.
    """
    raw = (request.headers.get("host") or "").strip().lower()
    host = raw.split("%", 1)[0].split(":", 1)[0]
    if host in _LOCAL_HOSTS:
        return True
    if _is_private_lan_host(host):
        return True
    if host and host == _active_tunnel_host():
        return True
    peer = (request.client.host if request.client else "") or ""
    return peer in {"127.0.0.1", "::1"}


def require_local_ops(request: Request) -> None:
    if not is_local_ops_host(request):
        raise HTTPException(
            403,
            "client dashboard is local/LAN-only "
            "(open via localhost or a private LAN IP, not a public tunnel)",
        )