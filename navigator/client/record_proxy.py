"""Manual-record window in the dashboard user's browser.

The product page is fetched here and shown under /client/record-surf so click
and fill events can be posted back. Live demos do not use this path — they
still launch Chrome on the API host.
"""

from __future__ import annotations

import ipaddress
import json
import re
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException, Request
from starlette.responses import Response

from navigator.client.content import record_job_for_token, recording_base_url

_HOP = {
    "host",
    "content-length",
    "accept-encoding",
    "connection",
    "keep-alive",
    "transfer-encoding",
    "te",
    "trailer",
    "upgrade",
    "proxy-connection",
}
_DROP_RESP = {
    "content-length",
    "content-encoding",
    "transfer-encoding",
    "content-security-policy",
    "content-security-policy-report-only",
    "x-frame-options",
    "strict-transport-security",
}

_INJECT = """<script data-nav-record="1">
(function(){
  var TOKEN = __TOKEN__;
  var HOST = __HOST__;
  var PREFIX = "/client/record-surf/" + TOKEN;
  function rewrite(url) {
    try {
      if (url == null) return url;
      var s = String(url).trim();
      if (!s) return url;
      var low = s.slice(0, 12).toLowerCase();
      if (s.charAt(0) === "#" || low.indexOf("data:") === 0 || low.indexOf("blob:") === 0 ||
          low.indexOf("javascript:") === 0 || low.indexOf("mailto:") === 0 || low.indexOf("tel:") === 0)
        return url;
      var absolute = s.indexOf("://") > 0 || s.indexOf("//") === 0;
      var rooted = s.charAt(0) === "/";
      if (!absolute && !rooted) return url;
      var u = new URL(s, location.href);
      if (u.protocol !== "http:" && u.protocol !== "https:") return url;
      var sameProduct = u.host === HOST;
      var samePage = u.origin === location.origin;
      if (!sameProduct && !samePage) return url;
      if (samePage && (u.pathname.indexOf("/client/") === 0 || u.pathname.indexOf("/v1/") === 0))
        return url;
      return location.origin + PREFIX + u.pathname + u.search + u.hash;
    } catch (e) { return url; }
  }
  function patchURLProp(proto, prop) {
    var d = Object.getOwnPropertyDescriptor(proto, prop);
    if (!d || !d.set || !d.get) return;
    Object.defineProperty(proto, prop, {
      configurable: true,
      enumerable: d.enumerable,
      get: function() { return d.get.call(this); },
      set: function(v) { d.set.call(this, rewrite(v)); }
    });
  }
  if (window.HTMLScriptElement) patchURLProp(HTMLScriptElement.prototype, "src");
  if (window.HTMLLinkElement) patchURLProp(HTMLLinkElement.prototype, "href");
  if (window.HTMLImageElement) patchURLProp(HTMLImageElement.prototype, "src");
  if (window.HTMLSourceElement) patchURLProp(HTMLSourceElement.prototype, "src");
  var origSetAttr = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function(name, value) {
    if (typeof value === "string" && /^(href|src|action|poster|formaction)$/i.test(name))
      value = rewrite(value);
    return origSetAttr.call(this, name, value);
  };
  var origFetch = window.fetch;
  if (origFetch) {
    window.fetch = function(input, init) {
      if (typeof input === "string") return origFetch.call(this, rewrite(input), init);
      if (input && input.url) {
        var next = rewrite(input.url);
        if (next !== input.url) input = new Request(next, input);
      }
      return origFetch.call(this, input, init);
    };
  }
  var origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url) {
    var args = Array.prototype.slice.call(arguments);
    args[1] = rewrite(url);
    return origOpen.apply(this, args);
  };
  function send(payload) {
    try {
      origFetch.call(window, "/client/api/record/event", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({token: TOKEN, payload: payload})
      });
    } catch (e) {}
  }
  function info(node, tool) {
    var t = node && node.closest ? (node.closest("a,button,input,textarea,select,[role=button]") || node) : node;
    if (!t || !t.tagName) return null;
    if (t.closest && t.closest("#nav-record-banner")) return null;
    return {
      tool: tool,
      tag: (t.tagName || "").toLowerCase(),
      id: t.id || "",
      testid: t.getAttribute ? (t.getAttribute("data-testid") || "") : "",
      name: t.getAttribute ? (t.getAttribute("name") || "") : "",
      text: ((t.innerText || (t.getAttribute && t.getAttribute("aria-label")) || "") + "").trim().slice(0, 40),
      type: t.getAttribute ? (t.getAttribute("type") || "") : "",
      autocomplete: t.getAttribute ? (t.getAttribute("autocomplete") || "") : "",
      role: t.getAttribute ? (t.getAttribute("role") || "") : "",
      value: ""
    };
  }
  document.addEventListener("click", function(e) {
    var a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
    if (a) {
      var href = a.getAttribute("href") || "";
      if (href && href.indexOf("javascript:") !== 0 && href.charAt(0) !== "#") {
        var next = rewrite(a.href);
        if (next !== a.href) { e.preventDefault(); location.href = next; }
      }
    }
    var row = info(e.target, "click_element");
    if (row) send(row);
  }, true);
  document.addEventListener("submit", function(e) {
    var f = e.target;
    if (f && f.action) {
      var action = rewrite(f.action);
      if (action !== f.action) f.action = action;
    }
  }, true);
  document.addEventListener("change", function(e) {
    var t = e.target;
    if (!t || !t.tagName) return;
    var tag = t.tagName.toLowerCase();
    if (tag !== "input" && tag !== "textarea" && tag !== "select") return;
    var row = info(t, "fill_field");
    if (!row) return;
    var typ = (t.getAttribute("type") || "").toLowerCase();
    row.value = typ === "password" ? "" : (t.value || "");
    send(row);
  }, true);
})();
</script>
<div id="nav-record-banner" class="navigator-chrome" style="position:fixed;top:0;left:0;right:0;z-index:2147483646;background:#111;color:#fff;font:13px/1.4 system-ui,sans-serif;padding:8px 12px">Recording in this browser. Log in here, then press Start capturing on the dashboard.</div>
<style data-nav-record="1">body{margin-top:36px !important}</style>
"""


def assert_public_product_url(url: str) -> str:
    """Reject targets that would turn the record proxy into an SSRF hop."""
    parts = urlsplit((url or "").strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("start_url must be http(s)")
    host = parts.hostname.lower().rstrip(".")
    if host in {"metadata.google.internal", "metadata"}:
        raise ValueError("start_url host is blocked")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    ):
        raise ValueError("start_url host is blocked")
    return url.strip()


def surf_open_path(token: str, start_url: str) -> str:
    parts = urlsplit(start_url.strip())
    path = parts.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    query = f"?{parts.query}" if parts.query else ""
    return f"/client/record-surf/{token}{path}{query}"


def upstream_url(start_url: str, path: str, query: str) -> str:
    assert_public_product_url(start_url)
    base = recording_base_url(start_url)
    host = (urlsplit(base).hostname or "").lower()
    rel = (path or "").lstrip("/")
    if "\\" in rel or "://" in rel:
        raise ValueError("bad record path")
    for piece in rel.split("/"):
        if piece == "..":
            raise ValueError("bad record path")
    url = base.rstrip("/") + "/" + rel
    if query:
        url += "?" + query
    got = (urlsplit(url).hostname or "").lower()
    if got != host:
        raise ValueError("bad record path")
    return url


def rewrite_location(location: str, start_url: str, token: str) -> str:
    base = recording_base_url(start_url)
    host = (urlsplit(base).hostname or "").lower()
    if "://" not in location:
        path = location if location.startswith("/") else "/" + location
        return f"/client/record-surf/{token}{path}"
    parts = urlsplit(location)
    if (parts.hostname or "").lower() != host:
        return location
    query = f"?{parts.query}" if parts.query else ""
    return f"/client/record-surf/{token}{parts.path or '/'}{query}"


_SKIP_URL_PREFIXES = ("#", "data:", "blob:", "javascript:", "mailto:", "tel:")
_ATTR_URL = re.compile(
    r"""(?P<prefix>\s(?:href|src|action|poster|formaction|data-src)\s*=\s*)"""
    r"""(?P<q>["'])(?P<url>.*?)(?P=q)""",
    re.IGNORECASE | re.DOTALL,
)
_SRCSET = re.compile(
    r"""(?P<prefix>\s(?:srcset|imagesrcset)\s*=\s*)(?P<q>["'])(?P<url>.*?)(?P=q)""",
    re.IGNORECASE | re.DOTALL,
)


def rewrite_surf_url(url: str, token: str, host: str) -> str:
    """Point product-host and root-absolute asset URLs at the record proxy.

    The document is served on the Navigator host, so ``/app.css`` would
    otherwise request Navigator itself and the product renders unstyled.
    Relative URLs (no leading slash) stay put — the browser resolves them
    against the proxied document URL.
    """
    raw = url.strip()
    if not raw:
        return url
    low = raw.lower()
    if low.startswith(_SKIP_URL_PREFIXES):
        return url
    protocol_relative = raw.startswith("//")
    rooted = raw.startswith("/") and not protocol_relative
    if not rooted and "://" not in raw and not protocol_relative:
        return url
    parts = urlsplit("https:" + raw if protocol_relative else raw)
    if rooted:
        path = parts.path or "/"
        if path.startswith("/client/") or path.startswith("/v1/"):
            return url
        query = f"?{parts.query}" if parts.query else ""
        frag = f"#{parts.fragment}" if parts.fragment else ""
        return f"/client/record-surf/{token}{path}{query}{frag}"
    if (parts.hostname or "").lower() != host.lower():
        return url
    query = f"?{parts.query}" if parts.query else ""
    frag = f"#{parts.fragment}" if parts.fragment else ""
    return f"/client/record-surf/{token}{parts.path or '/'}{query}{frag}"


def _rewrite_srcset(value: str, token: str, host: str) -> str:
    out: list[str] = []
    for part in value.split(","):
        piece = part.strip()
        if not piece:
            continue
        bits = piece.split(None, 1)
        bits[0] = rewrite_surf_url(bits[0], token, host)
        out.append(" ".join(bits))
    return ", ".join(out)


def _rewrite_html_assets(text: str, token: str, host: str) -> str:
    def srcset(match: re.Match[str]) -> str:
        rewritten = _rewrite_srcset(match.group("url"), token, host)
        return f"{match.group('prefix')}{match.group('q')}{rewritten}{match.group('q')}"

    def attr(match: re.Match[str]) -> str:
        rewritten = rewrite_surf_url(match.group("url"), token, host)
        return f"{match.group('prefix')}{match.group('q')}{rewritten}{match.group('q')}"

    text = _SRCSET.sub(srcset, text)
    return _ATTR_URL.sub(attr, text)


def _inject_html(raw: bytes, token: str, upstream_host: str) -> bytes:
    text = _rewrite_html_assets(raw.decode("utf-8", "replace"), token, upstream_host)
    snippet = _INJECT.replace("__TOKEN__", json.dumps(token)).replace(
        "__HOST__", json.dumps(upstream_host)
    )
    lower = text.lower()
    idx = lower.find("<head")
    if idx >= 0:
        end = lower.find(">", idx)
        if end >= 0:
            return (text[: end + 1] + snippet + text[end + 1 :]).encode("utf-8")
    return (snippet + text).encode("utf-8")


def _rewrite_set_cookie(value: str) -> str:
    kept: list[str] = []
    for part in value.split(";"):
        name = part.strip().split("=", 1)[0].lower()
        if name == "domain":
            continue
        kept.append(part.strip())
    return "; ".join(p for p in kept if p)


def proxy_record_document(
    token: str,
    path: str,
    request: Request,
    body: bytes,
) -> Response:
    job = record_job_for_token(token)
    if job is None:
        raise HTTPException(404, "no active recording")
    try:
        target = upstream_url(job.start_url, path, request.url.query)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    host = (urlsplit(recording_base_url(job.start_url)).hostname or "").lower()
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        if key.lower() in _HOP:
            continue
        headers[key] = value
    try:
        with httpx.Client(follow_redirects=False, timeout=20.0) as client:
            upstream = client.request(
                request.method,
                target,
                headers=headers,
                content=body if request.method not in {"GET", "HEAD"} else None,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"product page unreachable: {exc}") from None

    raw = upstream.content
    media = upstream.headers.get("content-type", "")
    head = raw[:64].lstrip().lower()
    looks_html = head.startswith(b"<!doctype") or head.startswith(b"<html")
    if "text/html" in media.lower() or (not media and looks_html):
        raw = _inject_html(raw, token, host)
        if "text/html" not in media.lower():
            media = "text/html; charset=utf-8"

    out = Response(content=raw, status_code=upstream.status_code, media_type=None)
    for key, value in upstream.headers.multi_items():
        low = key.lower()
        if low in _DROP_RESP:
            continue
        if low == "set-cookie":
            out.headers.append("set-cookie", _rewrite_set_cookie(value))
            continue
        if low == "location":
            out.headers[key] = rewrite_location(value, job.start_url, token)
            continue
        if low == "content-type":
            out.headers[key] = media or value
            continue
        if low in out.headers:
            continue
        out.headers[key] = value
    if media and "content-type" not in {k.lower() for k in out.headers.keys()}:
        out.headers["content-type"] = media
    return out
