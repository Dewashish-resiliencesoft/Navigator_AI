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
  if (window.__navRecordInit) return;
  window.__navRecordInit = true;
  var TOKEN = __TOKEN__;
  var HOST = __HOST__;
  var PREFIX = "/client/record-surf/" + TOKEN;
  var REG = __REG__;
  function siblingHost(h) {
    h = (h || "").toLowerCase().split(":")[0];
    return !!REG && h !== HOST && (h === REG || h.slice(-(REG.length + 1)) === "." + REG);
  }
  // Cross-origin API calls (fetch/XHR/EventSource) to the product's own domain
  // go through Navigator so the product backend sees its real Origin, not ours.
  function rewriteApi(url) {
    var out = rewrite(url);
    if (out !== url) return out;
    try {
      if (url == null) return url;
      var u = new URL(String(url), location.href);
      if ((u.protocol === "http:" || u.protocol === "https:") && siblingHost(u.host))
        return location.origin + PREFIX + "/__xo/" + u.protocol.slice(0, -1) + "/" + u.host + u.pathname + u.search;
    } catch (e) {}
    return url;
  }
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
      if (typeof input === "string" || (window.URL && input instanceof URL))
        return origFetch.call(this, rewriteApi(String(input)), init);
      if (input && input.url) {
        var next = rewriteApi(input.url);
        if (next !== input.url) input = new Request(next, input);
      }
      return origFetch.call(this, input, init);
    };
  }
  var origOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url) {
    var args = Array.prototype.slice.call(arguments);
    args[1] = rewriteApi(url == null ? url : String(url));
    return origOpen.apply(this, args);
  };
  if (window.EventSource) {
    var OrigES = window.EventSource;
    var PatchedES = function(url, cfg) { return new OrigES(rewriteApi(url), cfg); };
    PatchedES.prototype = OrigES.prototype;
    window.EventSource = PatchedES;
  }
  // Keep full-page navigations to the product host inside the proxy. Scripts that
  // set location.href = "https://<product>/login" (or window.open) would leave
  // the record window and every later click would be lost.
  try {
    if (window.navigation && window.navigation.addEventListener) {
      window.navigation.addEventListener("navigate", function(e) {
        try {
          if (!e.destination || !e.destination.url) return;
          var dest = new URL(e.destination.url);
          var off = dest.host === HOST || siblingHost(dest.host) && dest.host.replace(/^www\./, "") === HOST.replace(/^www\./, "");
          if (!off) {
            // Same origin but outside the prefix (client router escaped).
            if (dest.origin === location.origin && dest.pathname.indexOf(PREFIX) !== 0 &&
                dest.pathname.indexOf("/client/") !== 0 && dest.pathname.indexOf("/v1/") !== 0 &&
                !e.canIntercept) {
              e.preventDefault(); location.href = location.origin + PREFIX + dest.pathname + dest.search + dest.hash;
            }
            return;
          }
          if (e.cancelable) {
            e.preventDefault();
            location.href = location.origin + PREFIX + dest.pathname + dest.search + dest.hash;
          }
        } catch (err) {}
      });
    }
  } catch (e) {}
  var origWinOpen = window.open;
  window.open = function(url, target, features) {
    var next = rewrite(url);
    if (next !== url && (!target || target === "_blank")) { location.href = next; return window; }
    return origWinOpen.call(window, next, target, features);
  };
  function send(payload) {
    if (!capturing()) return;
    try {
      // keepalive: clicks that navigate (links, login submit) unload the page
      // before a normal fetch completes — those steps were being lost.
      origFetch.call(window, "/client/api/record/event", {
        method: "POST", keepalive: true,
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({token: TOKEN, payload: payload})
      });
    } catch (e) {}
  }
  function info(node, tool) {
    var t = node && node.closest ? (node.closest("a,button,input,textarea,select,[role=button]") || node) : node;
    if (!t || !t.tagName) return null;
    if (t.closest && t.closest("#nav-record-banner,#nav-record-widget,[data-navigator-chrome]")) return null;
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
  // ---- Record controls (shadow DOM, attached to <html> after load) ----
  var NARRATE_DEFAULT = __NARRATE__;
  var state = { phase: "setup", paused: false, narrate: NARRATE_DEFAULT, done: false };
  var mic = { stream: null, rec: null, seg: Math.random().toString(36).slice(2), offset: 0 };
  var clockBase = { ms: 0, at: 0 };
  function capturing() { return state.phase === "capturing" && !state.paused && !state.done; }
  function control(action) {
    return origFetch.call(window, "/client/api/record/surf-control", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({token: TOKEN, action: action})
    }).then(function(r) {
        if (r.status === 404 && !state.stopping) { state.done = true; stopMic(); setNote("This recording has ended. Check the Flows screen for the saved steps."); render(); }
        if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function(s) {
        state.phase = s.phase; state.paused = !!s.paused; state.narrate = !!s.narrate;
        if (ui.tail) ui.tail.textContent = s.live_tail ? "“" + s.live_tail + "”" : "";
        clockBase = { ms: s.clock_ms || 0, at: performance.now() };
        return s;
      });
  }
  function postAudio(blob, keepalive) {
    if (!blob || !blob.size) return;
    var fr = new FileReader();
    fr.onload = function() {
      var b64 = String(fr.result || "").split(",")[1] || "";
      if (!b64) return;
      origFetch.call(window, "/client/api/record/surf-audio", {
        method: "POST", headers: {"Content-Type": "application/json"}, keepalive: !!keepalive && b64.length < 60000,
        body: JSON.stringify({token: TOKEN, payload: {b64: b64, mime: blob.type || "audio/webm", seg: mic.seg, seg_offset_ms: mic.offset}})
      }).catch(function() {});
    };
    fr.readAsDataURL(blob);
  }
  function startMic() {
    if (!state.narrate || mic.rec || !navigator.mediaDevices || !window.MediaRecorder) return Promise.resolve(false);
    // One recorder at a time: boot + status poll + Start used to race and run two
    // MediaRecorders under one segment id, interleaving two WebM streams into
    // undecodable audio (Whisper heard ~1 segment for minutes of speech).
    if (mic.starting) return mic.starting;
    mic.starting = navigator.mediaDevices.getUserMedia({audio: {
      echoCancellation: true, noiseSuppression: true, autoGainControl: true,
      channelCount: 1, sampleRate: 48000
    }}).then(function(stream) {
      var mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus") ? "audio/webm;codecs=opus" : "";
      var opts = {audioBitsPerSecond: 96000};
      if (mime) opts.mimeType = mime;
      var rec = new MediaRecorder(stream, opts);
      if (mic.rec) { stream.getTracks().forEach(function(t) { t.stop(); }); return true; }
      mic.stream = stream; mic.rec = rec;
      mic.seg = Math.random().toString(36).slice(2) + Date.now().toString(36);
      mic.offset = clockBase.ms + Math.round(performance.now() - clockBase.at);
      rec.ondataavailable = function(e) { postAudio(e.data, false); };
      rec.start(1000);
      if (state.paused) rec.pause();
      return true;
    }).catch(function(e) {
      setNote("Mic blocked — steps still record, narration won't (" + (e && e.name || "error") + ").");
      return false;
    }).then(function(v) { mic.starting = null; return v; });
    return mic.starting;
  }
  function stopMic() {
    return new Promise(function(done) {
      var rec = mic.rec;
      if (!rec || rec.state === "inactive") { done(); return; }
      rec.onstop = function() { setTimeout(done, 400); };
      try { rec.stop(); } catch (e) { done(); }
      if (mic.stream) mic.stream.getTracks().forEach(function(t) { t.stop(); });
      mic.rec = null; mic.stream = null;
    });
  }
  window.addEventListener("pagehide", function() {
    if (mic.rec && mic.rec.state === "recording") { try { mic.rec.requestData(); } catch (e) {} }
  });

  var root = null, ui = {};
  function setNote(t) { if (ui.note) ui.note.textContent = t || ""; }
  function render() {
    if (!root) return;
    var live = state.phase === "capturing" && !state.done;
    root.host.setAttribute("data-mode", state.done ? "done" : live ? (state.paused ? "paused" : "recording") : "idle");
    ui.status.textContent = state.discarded ? "Recording discarded" : state.done ? "Recording saved as draft" : live ? (state.paused ? "Paused" : "Recording…") : "Ready to record";
    ui.start.style.display = live || state.done ? "none" : "";
    ui.pause.style.display = live && !state.paused ? "" : "none";
    ui.resume.style.display = live && state.paused ? "" : "none";
    ui.stop.style.display = live ? "" : "none";
    ui.ppause.style.display = live && !state.paused ? "" : "none";
    ui.presume.style.display = live && state.paused ? "" : "none";
    ui.pstop.style.display = live ? "" : "none";
    ui.discard.style.display = live || (!state.done && state.phase === "setup") ? "" : "none";
    ui.pdiscard.style.display = live ? "" : "none";
  }
  function act(fn) { return function(e) { e.preventDefault(); e.stopPropagation(); fn(); }; }
  function doStart() {
    control("start").then(function() { render(); return startMic(); }).catch(function(e) { setNote("Could not start: " + e.message); });
  }
  function doPause() {
    control("pause").then(function() { if (mic.rec && mic.rec.state === "recording") mic.rec.pause(); render(); });
  }
  function doResume() {
    control("resume").then(function() {
      if (mic.rec && mic.rec.state === "paused") mic.rec.resume(); else startMic();
      render();
    });
  }
  function doDiscard() {
    if (state.stopping) return;
    if (!window.confirm("Discard this recording? Captured steps and narration are thrown away and nothing is saved.")) return;
    state.stopping = true;
    stopMic().then(function() {
      return origFetch.call(window, "/client/api/record/surf-discard", {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({token: TOKEN})
      });
    }).then(function(r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function() { state.done = true; state.discarded = true; setNote("Nothing was saved. Start a new recording from the Flows screen."); render(); })
      .catch(function(e) { state.stopping = false; setNote("Discard failed: " + e.message); });
  }
  function doStop() {
    if (state.stopping) return;
    state.stopping = true;
    setNote("Saving… transcribing narration, this can take a few seconds.");
    stopMic().then(function() {
      return origFetch.call(window, "/client/api/record/surf-stop", {
        method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({token: TOKEN})
      });
    }).then(function(r) { return r.json().then(function(j) { if (!r.ok) throw new Error(j.detail || r.status); return j; }); })
      .then(function(j) { state.done = true; setNote((j.steps || 0) + " steps saved as draft. Review the script on the Flows screen."); render(); })
      .catch(function(e) { state.stopping = false; setNote("Stop failed: " + e.message); });
  }
  function mount() {
    if (root && root.host.isConnected) return;
    if (root) { document.documentElement.appendChild(root.host); return; }
    if (!document.documentElement) return;
    var host = document.createElement("nav-record-widget");
    host.id = "nav-record-widget";
    host.className = "navigator-chrome";
    host.setAttribute("data-navigator-chrome", "1");
    host.style.cssText = "all:initial;position:fixed;inset:0 auto auto 0;width:0;height:0;z-index:2147483646;";
    root = host.attachShadow({mode: "open"});
    root.innerHTML = '<style>' +
      ':host{all:initial}*{box-sizing:border-box;font:12px/1.4 system-ui,-apple-system,sans-serif}' +
      '.bar{position:fixed;top:10px;left:50%;transform:translate(-50%,0);display:flex;gap:8px;align-items:center;' +
      'background:rgba(17,17,17,.92);color:#fff;padding:7px 10px;border-radius:12px;box-shadow:0 6px 24px rgba(0,0,0,.25);' +
      'transition:transform .35s ease,opacity .35s ease;pointer-events:auto;max-width:94vw}' +
      ':host([data-mode=recording]) .bar,:host([data-mode=paused]) .bar{transform:translate(-50%,-160%);opacity:0;pointer-events:none}' +
      'button{all:unset;cursor:pointer;padding:4px 10px;border-radius:8px;background:#2a2a2a;color:#fff}' +
      'button:hover{background:#3a3a3a}button.primary{background:#e11d48}button.primary:hover{background:#be123c}' +
      '.note{opacity:.75;max-width:40vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
      '.pill{position:fixed;right:14px;bottom:14px;display:none;align-items:center;gap:6px;padding:6px;border-radius:999px;' +
      'background:rgba(17,17,17,.55);box-shadow:0 2px 10px rgba(0,0,0,.2);pointer-events:auto;transition:background .2s}' +
      ':host([data-mode=recording]) .pill,:host([data-mode=paused]) .pill{display:flex}' +
      '.dot{width:10px;height:10px;border-radius:50%;background:#e11d48;animation:p 1.6s infinite}' +
      ':host([data-mode=paused]) .dot{background:#f59e0b;animation:none}' +
      '@keyframes p{0%,100%{opacity:1}50%{opacity:.35}}' +
      '.pill .ctl{display:none}.pill:hover{background:rgba(17,17,17,.9)}.pill:hover .ctl{display:inline-flex}' +
      '.pill button{padding:3px 8px;font-size:11px}' +
      '.tail{color:#fff;opacity:.85;font-size:11px;max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;align-self:center;margin-right:4px}' +
      '</style>' +
      '<div class="bar" part="bar"><strong>Navigator</strong><span class="status"></span>' +
      '<button class="primary start">● Start recording</button><button class="pause">❚❚ Pause</button>' +
      '<button class="resume">▶ Resume</button><button class="stop">■ Stop &amp; save</button><button class="discard">✕ Discard</button><span class="note"></span></div>' +
      '<div class="pill" title="Recording — hover for controls"><span class="dot"></span>' +
      '<span class="ctl"><span class="tail"></span><button class="ppause">❚❚</button><button class="presume">▶</button><button class="pstop">■</button><button class="pdiscard" title="Discard">✕</button></span></div>';
    ["status","start","pause","resume","stop","note","ppause","presume","pstop","tail","discard","pdiscard"].forEach(function(k) { ui[k] = root.querySelector("." + k); });
    ui.start.addEventListener("click", act(doStart));
    ui.pause.addEventListener("click", act(doPause)); ui.ppause.addEventListener("click", act(doPause));
    ui.resume.addEventListener("click", act(doResume)); ui.presume.addEventListener("click", act(doResume));
    ui.stop.addEventListener("click", act(doStop));
    ui.discard.addEventListener("click", act(doDiscard)); ui.pdiscard.addEventListener("click", act(doDiscard)); ui.pstop.addEventListener("click", act(doStop));
    document.documentElement.appendChild(host);
    render();
  }
  var booted = false;
  function boot() {
    if (booted) { mount(); return; }
    booted = true;
    mount();
    // Frameworks re-render <html>/<body> (hydration, client navigation) and may
    // drop foreign nodes — re-attach the host whenever it goes missing.
    try {
      new MutationObserver(function() { if (root && !root.host.isConnected) mount(); })
        .observe(document.documentElement, {childList: true, subtree: false});
      if (document.body) new MutationObserver(function() { if (root && !root.host.isConnected) mount(); })
        .observe(document.body, {childList: true});
    } catch (e) {}
    setInterval(function() { if (root && !root.host.isConnected) mount(); }, 1500);
    ["pushState", "replaceState"].forEach(function(k) {
      var orig = history[k];
      history[k] = function() { var out = orig.apply(this, arguments); setTimeout(mount, 0); return out; };
    });
    window.addEventListener("popstate", function() { setTimeout(mount, 0); });
    control("status").then(function() {
      render();
      if (state.phase === "capturing") startMic();
    }).catch(function() { state.done = true; setNote("Recording is not active."); render(); });
    // Dashboard "Start capturing" also flips the phase — follow it.
    setInterval(function() {
      if (state.done) return;
      var was = state.phase;
      control("status").then(function() {
        if (was !== "capturing" && state.phase === "capturing") startMic();
        if (state.phase === "stopping" || state.phase === "done") { state.done = true; stopMic(); }
        render();
      }).catch(function() {});
    }, 3000);
  }
  // DOMContentLoaded (not load: slow images/analytics delayed the toolbar), then
  // again on load in case hydration replaced the tree in between.
  if (document.readyState !== "loading") setTimeout(boot, 0);
  else document.addEventListener("DOMContentLoaded", function() { setTimeout(boot, 0); });
  window.addEventListener("load", function() { setTimeout(boot, 50); });
})();
</script>
"""

# Kept out of <head>: a <div> there makes the HTML parser close <head> early and
# hoist the rest of the head into <body>, which breaks hydration of SSR apps
# (React #418/#412 — content that animates in on hydrate stays invisible).
# Fixed-position pill at the bottom, no body margin: the product's own layout
# (fixed headers, viewport-height heroes) must render exactly as on its origin.
_BANNER = """<div id="nav-record-banner" class="navigator-chrome" style="position:fixed;bottom:12px;left:50%;transform:translateX(-50%);z-index:2147483646;background:rgba(17,17,17,.88);color:#fff;font:12px/1.4 system-ui,sans-serif;padding:6px 12px;border-radius:999px;pointer-events:none;max-width:90vw;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">Recording in this browser. Log in here, then press Start capturing on the dashboard.</div>
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


def _inject_html(raw: bytes, token: str, upstream_host: str, narrate: bool = True) -> bytes:
    text = _rewrite_html_assets(raw.decode("utf-8", "replace"), token, upstream_host)
    snippet = (
        _INJECT.replace("__TOKEN__", json.dumps(token))
        .replace("__HOST__", json.dumps(upstream_host))
        .replace("__REG__", json.dumps(registrable_domain(upstream_host)))
        .replace("__NARRATE__", "true" if narrate else "false")
    )
    # Controls are built by the script after load inside a shadow root on <html>
    # — no markup is added to the document, so SSR hydration is untouched.
    lower = text.lower()
    match = re.search(r"<head(?:\s[^>]*)?>", lower)
    if match:
        end = match.end()
        return (text[:end] + snippet + text[end:]).encode("utf-8")
    return (snippet + text).encode("utf-8")


def _rewrite_set_cookie(value: str, *, secure_origin: bool = True) -> str:
    """Re-scope an upstream cookie onto the Navigator origin (host-only, Path=/)."""
    kept: list[str] = []
    for i, part in enumerate(value.split(";")):
        piece = part.strip()
        name = piece.split("=", 1)[0].lower()
        if i > 0 and name in {"domain", "path", "partitioned"}:
            continue
        if i > 0 and name == "secure" and not secure_origin:
            continue
        if i > 0 and name == "samesite":
            if piece.split("=", 1)[-1].strip().lower() == "none" and not secure_origin:
                piece = "SameSite=Lax"
        kept.append(piece)
    kept.append("Path=/")
    return "; ".join(p for p in kept if p)


def _secure_origin(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.split(",")[0].strip().lower() == "https"


def registrable_domain(host: str) -> str:
    """Best-effort eTLD+1 (handles two-letter ccTLD second levels like co.in)."""
    labels = [p for p in (host or "").lower().split(":")[0].split(".") if p]
    if len(labels) < 2:
        return ".".join(labels)
    if (
        len(labels) >= 3
        and len(labels[-1]) == 2
        and labels[-2] in {"co", "com", "net", "org", "gov", "ac", "edu", "ltd", "plc"}
    ):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _forward_headers(request: Request, product_origin: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        low = key.lower()
        if low in _HOP or low.startswith(("x-forwarded-", "cf-")) or low == "cdn-loop":
            continue
        headers[key] = value
    # The product must see its own origin, not the Navigator / tunnel host.
    for key in list(headers):
        if key.lower() in {"origin", "referer"}:
            del headers[key]
    if request.headers.get("origin"):
        headers["Origin"] = product_origin
    headers["Referer"] = product_origin + "/"
    return headers


_DROP_XO = _DROP_RESP | {
    "access-control-allow-origin",
    "access-control-allow-credentials",
    "access-control-allow-methods",
    "access-control-allow-headers",
    "access-control-expose-headers",
    "access-control-max-age",
    "vary",
}


def proxy_record_xo(token: str, rest: str, request: Request, body: bytes, job) -> Response:
    """Same-origin passthrough for the product's cross-origin API calls.

    Path shape: ``__xo/<scheme>/<host>/<path>``. Only hosts on the product's
    registrable domain are allowed, and only while the recording is live.
    """
    bits = rest.split("/", 2)
    if len(bits) < 2 or bits[0] not in {"http", "https"}:
        raise HTTPException(400, "bad passthrough path")
    scheme, host = bits[0], bits[1].lower()
    tail = bits[2] if len(bits) > 2 else ""
    product_host = (urlsplit(recording_base_url(job.start_url)).hostname or "").lower()
    reg = registrable_domain(product_host)
    hostname = host.split(":")[0]
    if not reg or not (hostname == reg or hostname.endswith("." + reg)):
        raise HTTPException(403, "passthrough host not allowed")
    if "\\" in tail or any(p == ".." for p in tail.split("/")):
        raise HTTPException(400, "bad passthrough path")
    target = f"{scheme}://{host}/{tail}"
    try:
        assert_public_product_url(target)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    if request.url.query:
        target += "?" + request.url.query
    if request.method == "OPTIONS":
        return Response(status_code=204)
    base = recording_base_url(job.start_url)
    origin = f"{urlsplit(base).scheme}://{urlsplit(base).netloc}"
    try:
        with httpx.Client(follow_redirects=False, timeout=30.0) as client:
            upstream = client.request(
                request.method,
                target,
                headers=_forward_headers(request, origin),
                content=body if request.method not in {"GET", "HEAD"} else None,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"product API unreachable: {exc}") from None
    out = Response(content=upstream.content, status_code=upstream.status_code, media_type=None)
    secure = _secure_origin(request)
    for key, value in upstream.headers.multi_items():
        low = key.lower()
        if low in _DROP_XO:
            continue
        if low == "set-cookie":
            out.headers.append("set-cookie", _rewrite_set_cookie(value, secure_origin=secure))
            continue
        if low == "location":
            loc = urlsplit(value)
            if loc.hostname and (loc.hostname == reg or loc.hostname.endswith("." + reg)) and loc.hostname != product_host:
                q = f"?{loc.query}" if loc.query else ""
                out.headers[key] = f"/client/record-surf/{token}/__xo/{loc.scheme}/{loc.netloc}{loc.path or '/'}{q}"
            else:
                out.headers[key] = rewrite_location(value, job.start_url, token)
            continue
        out.headers[key] = value
    return out


def proxy_record_document(
    token: str,
    path: str,
    request: Request,
    body: bytes,
) -> Response:
    job = record_job_for_token(token)
    if job is None:
        raise HTTPException(404, "no active recording")
    if path.startswith("__xo/"):
        return proxy_record_xo(token, path[len("__xo/"):], request, body, job)
    try:
        target = upstream_url(job.start_url, path, request.url.query)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    host = (urlsplit(recording_base_url(job.start_url)).hostname or "").lower()
    base = recording_base_url(job.start_url)
    headers = _forward_headers(request, f"{urlsplit(base).scheme}://{urlsplit(base).netloc}")
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
        raw = _inject_html(raw, token, host, narrate=job.narration is not None)
        if "text/html" not in media.lower():
            media = "text/html; charset=utf-8"

    out = Response(content=raw, status_code=upstream.status_code, media_type=None)
    for key, value in upstream.headers.multi_items():
        low = key.lower()
        if low in _DROP_RESP:
            continue
        if low == "set-cookie":
            out.headers.append(
                "set-cookie", _rewrite_set_cookie(value, secure_origin=_secure_origin(request))
            )
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
    # Lets stray root-relative requests (client routers, prefetch) find their way back.
    out.headers.append(
        "set-cookie",
        f"{SURF_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax",
    )
    if media and "content-type" not in {k.lower() for k in out.headers.keys()}:
        out.headers["content-type"] = media
    return out


SURF_COOKIE = "nav_record_surf"
_SURF_PREFIX = "/client/record-surf/"
_REFERER_TOKEN = re.compile(r"/client/record-surf/([A-Za-z0-9_-]+)(?:/|$)")


def stray_surf_redirect(path: str, query: str, cookie_token: str, referer: str) -> str | None:
    """Map a 404 for a product path back under the active record-surf prefix.

    Proxied pages are served on the Navigator origin, so client-side routers,
    prefetches, and location changes that build root-relative URLs (``/login/``)
    escape the prefix and hit Navigator. Only paths outside Navigator's own
    namespaces, from a browser tied to a *live* recording, are redirected.
    """
    if not path.startswith("/") or path.startswith(("/client/", "/v1/")) or path == "/client":
        return None
    candidates = []
    m = _REFERER_TOKEN.search(referer or "")
    if m:
        candidates.append(m.group(1))
    if cookie_token:
        candidates.append(cookie_token)
    for token in candidates:
        if record_job_for_token(token) is not None:
            target = f"{_SURF_PREFIX}{token}{path}"
            return target + (f"?{query}" if query else "")
    return None
