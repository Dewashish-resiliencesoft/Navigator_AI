"""Render the docs model to one self-contained HTML file.

No CDN, no build step, no framework. The output opens from disk over file:// and
works offline, because the people who most need it are integrating against a
server that isn't running yet.
"""

from __future__ import annotations

from html import escape

from navigator.docs.model import DocsModel, Endpoint, ModelDoc

_CSS = """
:root {
  --bg: #fff; --fg: #1a1a1a; --muted: #666; --line: #e5e5e5;
  --code-bg: #f6f8fa; --accent: #0a5c31; --warn: #8a5300;
  --get: #1a7f37; --post: #0969da; --put: #9a6700; --delete: #cf222e;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117; --fg: #e6edf3; --muted: #9198a1; --line: #30363d;
    --code-bg: #161b22; --accent: #3fb950; --warn: #d29922;
    --get: #3fb950; --post: #58a6ff; --put: #d29922; --delete: #f85149;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 16px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
}
.wrap { display: flex; max-width: 1200px; margin: 0 auto; gap: 40px; }
nav {
  position: sticky; top: 0; align-self: flex-start; width: 240px;
  max-height: 100vh; overflow-y: auto; padding: 32px 0 32px 16px; font-size: 14px;
}
nav h2 { font-size: 12px; text-transform: uppercase; letter-spacing: .06em;
         color: var(--muted); margin: 24px 0 8px; }
nav a { display: block; padding: 3px 0; color: var(--fg); text-decoration: none; }
nav a:hover { color: var(--accent); }
nav code { font-size: 12px; }
main { flex: 1; min-width: 0; padding: 32px 16px 120px; }
h1 { font-size: 30px; margin: 0 0 4px; }
h2 { font-size: 23px; margin: 48px 0 12px; padding-top: 12px; border-top: 1px solid var(--line); }
h3 { font-size: 17px; margin: 28px 0 8px; }
p, li { max-width: 68ch; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13.5px; }
code { background: var(--code-bg); padding: 1px 5px; border-radius: 4px; }
pre { background: var(--code-bg); padding: 14px 16px; border-radius: 8px;
      overflow-x: auto; border: 1px solid var(--line); }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 12px 0; font-size: 14px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line);
         vertical-align: top; }
th { font-weight: 600; color: var(--muted); font-size: 12px;
     text-transform: uppercase; letter-spacing: .04em; }
.sub { color: var(--muted); }
.method { display: inline-block; min-width: 52px; padding: 1px 7px; border-radius: 4px;
          font-size: 12px; font-weight: 700; color: #fff; text-align: center; }
.GET { background: var(--get); } .POST { background: var(--post); }
.PUT { background: var(--put); } .DELETE { background: var(--delete); }
.ep { border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; margin: 16px 0; }
.ep h3 { margin: 0 0 6px; display: flex; align-items: center; gap: 10px; }
.ep h3 code { font-size: 14px; background: none; padding: 0; }
.note { border-left: 3px solid var(--accent); padding: 2px 0 2px 14px; margin: 16px 0;
        color: var(--muted); }
.warn { border-left-color: var(--warn); }
.pill { display: inline-block; font-size: 11px; padding: 1px 6px; border-radius: 10px;
        border: 1px solid var(--line); color: var(--muted); }
footer { margin-top: 64px; padding-top: 16px; border-top: 1px solid var(--line);
         color: var(--muted); font-size: 13px; }
@media (max-width: 900px) { nav { display: none; } .wrap { display: block; } }
"""


def _e(text: str) -> str:
    return escape(str(text), quote=False)


def _endpoint_html(ep: Endpoint) -> str:
    out = [
        f'<div class="ep" id="{ep.anchor}">',
        f'<h3><span class="method {ep.method}">{ep.method}</span>'
        f"<code>{_e(ep.path)}</code></h3>",
    ]
    if ep.description:
        first = ep.description.split("\n\n")[0].replace("\n", " ")
        out.append(f"<p>{_e(first)}</p>")
    if not ep.authed:
        out.append('<p><span class="pill">no auth required</span></p>')

    if ep.params:
        out.append(
            "<table><tr><th>Parameter</th><th>In</th><th>Type</th><th></th></tr>"
        )
        for p in ep.params:
            req = "required" if p.required else "optional"
            out.append(
                f"<tr><td><code>{_e(p.name)}</code></td><td>{_e(p.kind)}</td>"
                f"<td><code>{_e(p.type_)}</code></td>"
                f'<td class="sub">{req}{" — " + _e(p.description) if p.description else ""}</td></tr>'
            )
        out.append("</table>")

    if ep.request_example:
        out.append(
            f"<p class=\"sub\">Request body:</p><pre><code>{_e(ep.request_example)}</code></pre>"
        )

    out.append("</div>")
    return "\n".join(out)


def _model_html(model: ModelDoc) -> str:
    out = [f"<h3>{_e(model.name)}</h3>"]
    if model.description:
        out.append(f"<p>{_e(model.description.split(chr(10) + chr(10))[0])}</p>")
    out.append("<table><tr><th>Field</th><th>Type</th><th>Default</th><th></th></tr>")
    for f in model.fields:
        out.append(
            f"<tr><td><code>{_e(f.name)}</code></td>"
            f"<td><code>{_e(f.type_)}</code></td>"
            f"<td class=\"sub\">{_e(f.default)}</td>"
            f'<td class="sub">{_e(f.description)}</td></tr>'
        )
    out.append("</table>")
    return "\n".join(out)


def render(model: DocsModel, snippets: dict[str, str]) -> str:
    """Build the full page. `snippets` are literal code samples, keyed by name."""
    nav = ['<nav><h2>Start</h2>']
    for anchor, label in [
        ("how-it-works", "How it works"),
        ("quickstart", "Quickstart"),
        ("site-graph", "Site graph"),
        ("tools", "Tools &amp; postconditions"),
        ("sdk", "SDK"),
        ("ci", "CI gate"),
    ]:
        nav.append(f'<a href="#{anchor}">{label}</a>')

    nav.append("<h2>API</h2>")
    for ep in model.endpoints:
        nav.append(
            f'<a href="#{ep.anchor}"><span class="method {ep.method}">{ep.method}</span> '
            f"<code>{_e(ep.path)}</code></a>"
        )
    nav.append("<h2>Reference</h2>")
    for m in model.models:
        nav.append(f'<a href="#model-{m.name.lower()}">{_e(m.name)}</a>')
    nav.append("</nav>")

    checks = "\n".join(
        f"<tr><td><code>{_e(k)}</code></td><td>{d}</td></tr>"
        for k, d in model.check_kinds
    )

    body = f"""
<h1>{_e(model.title)} — integration guide</h1>
<p class="sub">Version {_e(model.version)} · generated from the running API, not written by hand</p>

<h2 id="how-it-works">How it works</h2>
<p>Navigator joins a video call, drives <em>your</em> web product in a real browser,
narrates what it does out loud, and types data your prospect gives it live into your
app. It works on any web product because it knows nothing about any of them: the one
thing you supply is a <strong>site graph</strong>.</p>

<p>A site graph maps your pages to selector aliases and named flows. Every step in a
flow declares a <strong>postcondition</strong> — what must be true afterwards. After
each action the agent checks that postcondition against the real DOM and writes both
the expectation and the actual result to an action log.</p>

<div class="note">Postconditions are the reason this is safe to put in front of a
prospect. An agent that only acts can fail silently; an agent that declares what it
expects can tell you the moment reality diverged — and so can you, from the log.</div>

<h3>Three ways to supply a site graph</h3>
<table>
<tr><th>Path</th><th>What it costs you</th><th>When to pick it</th></tr>
<tr><td>Hand-written YAML</td><td>Author it once, upload it</td>
    <td>You can't or won't change your app's code</td></tr>
<tr><td><code>data-nav</code> attributes + SDK</td><td>One attribute per element</td>
    <td>Recommended. Selectors then survive redesigns</td></tr>
<tr><td>SDK + CI gate</td><td>A config file and a CI job</td>
    <td>You want a broken demo to fail the build, not the call</td></tr>
</table>

<h2 id="quickstart">Quickstart</h2>
<p>Register, upload a site graph, run a demo. Three calls.</p>
<pre><code>{_e(snippets["quickstart_curl"])}</code></pre>
<div class="note">The API key is returned <strong>once</strong>, at registration.
Store it as a secret; it is stored hashed on our side and cannot be recovered.</div>

<h2 id="site-graph">The site graph</h2>
<p>One YAML document. This is the only product-specific artifact in the system.</p>
<pre><code>{_e(snippets["site_graph_yaml"])}</code></pre>

<p>Rules worth knowing before you author one:</p>
<ul>
<li><strong>Aliases, not CSS.</strong> Flows reference <code>send_button</code>; the
    <code>selectors</code> block maps that to a real selector. Change your DOM and you
    edit one line of YAML, not a flow.</li>
<li><strong>Validation is all-or-nothing and happens at upload.</strong> An unknown
    alias, a <code>navigate</code> to a page that doesn't exist, or a postcondition
    missing its <code>expected</code> is rejected with a message naming the page, flow,
    and step. A rejected upload does not become the active revision, so a bad push
    cannot break a demo already scheduled.</li>
<li><strong>Uploads are versioned, never overwritten.</strong> Roll back with
    <code>POST /v1/products/site-graph/activate</code>.</li>
<li><strong><code>base_url</code> must be absolute</strong> (<code>https://…</code>)
    when uploaded through the API.</li>
<li><strong>Aliases are spoken aloud.</strong> Narration reads them as English —
    <code>send_button</code> becomes "the send button" — so name them the way you'd
    say them on a call.</li>
</ul>

<h2 id="tools">What the agent can do</h2>
<p>Four tools. There is no free-form DOM access, by design.</p>
{chr(10).join(_model_html(t) for t in model.tools)}

<h3>Postcondition checks</h3>
<table><tr><th>check</th><th>Asserts</th></tr>{checks}</table>
<div class="note">Every check is a plain DOM comparison — no model call, so it runs
after every single action. If an element is present but its state is unreadable, the
result is flagged ambiguous and only that case escalates to a vision check.</div>

<h2 id="sdk">The SDK</h2>
<p>Author flows in your own repo, in the same pull request as the feature they demo.</p>
<pre><code>{_e(snippets["sdk_install"])}</code></pre>

<h3>1. Annotate your components</h3>
<p>Add <code>data-nav</code> to the elements the agent touches. Any alias you use in a
flow but never declare resolves to <code>[data-nav="&lt;alias&gt;"]</code> automatically,
so you write no CSS at all — and your selectors stop breaking when you restyle.</p>
<pre><code>{_e(snippets["annotate_jsx"])}</code></pre>

<h3>2. Declare your flows</h3>
<pre><code>{_e(snippets["sdk_config"])}</code></pre>
<p>Then <code>npx navigator push</code>. The SDK compiles this to site graph YAML and
uploads it; our server validates it with the same validator a hand-written file goes
through, so there is exactly one definition of "valid" and no local/remote drift.</p>

<h2 id="ci">3. Gate CI on it</h2>
<p><code>navigator verify</code> pushes your config, runs every flow against your app,
and exits non-zero if any postcondition fails.</p>
<pre><code>{_e(snippets["ci_yaml"])}</code></pre>
<p>Output on a break tells you which step and what the DOM actually said:</p>
<pre><code>{_e(snippets["verify_output"])}</code></pre>
<div class="note warn">This is the point of the whole SDK. Your demo is a test suite
that happens to double as a sales script — so it breaks in CI, on your schedule,
instead of in front of a prospect.</div>

<h2 id="api">API reference</h2>
<p>Base URL is your deployment. Authenticate every request except registration and
<code>/healthz</code>:</p>
<pre><code>Authorization: Token &lt;your api key&gt;</code></pre>
<div class="note">Your <code>product_id</code> is derived from the API key, never from
the path. One customer cannot read another's site graph, demos, or action log; each
demo also runs in its own browser context, so sessions and cookies are never shared.</div>
{chr(10).join(_endpoint_html(ep) for ep in model.endpoints)}

<h2 id="reference">Schema reference</h2>
{chr(10).join(f'<div id="model-{m.name.lower()}">{_model_html(m)}</div>' for m in model.models)}

<footer>
Generated by <code>python -m navigator.docs build</code> from the live OpenAPI schema
and Pydantic models. Do not edit this file by hand — a test fails when it drifts.
</footer>
"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(model.title)} — integration guide</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
{chr(10).join(nav)}
<main>
{body}
</main>
</div>
</body>
</html>
"""
