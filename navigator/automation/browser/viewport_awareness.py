"""Which site-graph sections are inside the live viewport.

One DOM read per planner or executing step. Boxes are clipped by nested
overflow, and scroll distance is measured against that scroller. No model
call. The planner sees the section on screen and a short nearby off-screen
list — not every alias on the page.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

_NEARBY_EACH = 3
#: Hard limit for one DOM read. A hung evaluate is cancelled and the step moves on.
EVAL_TIMEOUT_S = 3.0
_ALIAS_CAP = 40
_HEADING_CAP = 24
_PARTIAL_NEARBY_PX = 24
#: Sticky-header clearance when a client has not set one on the site graph.
DEFAULT_HEADER_OFFSET_PX = 72

_viewport_step = threading.local()


@dataclass(frozen=True)
class Placement:
    id: str
    name: str
    kind: str  # section | landmark | alias
    relation: str  # visible | partial | above | below
    distance_px: int
    visible_ratio: float
    top: float
    css: str = ""
    #: Unclipped viewport y, used to scroll. ``top`` may be the clipped box.
    scroll_top: float = 0.0
    #: Viewport y of the nested scroller that owns this box. 0 means the window.
    container_top: float = 0.0


@dataclass(frozen=True)
class ViewportAwareness:
    viewport_w: int = 0
    viewport_h: int = 0
    scroll_x: int = 0
    scroll_y: int = 0
    current_section: str = ""
    current_section_id: str = ""
    placements: tuple[Placement, ...] = ()

    def summary(self) -> str:
        """Planner text: on-screen name, partials, nearby off-screen."""
        if not self.placements and not self.current_section:
            return ""
        lines = [f"currently on screen: {self.current_section or '(unknown)'}"]
        partial = [
            p
            for p in self.placements
            if p.relation == "partial" and p.id != self.current_section_id and p.kind != "alias"
        ]
        if partial:
            lines.append("partly visible: " + _fmt_list(partial))
        near = nearby_offscreen(self)
        lines.append(
            "nearby offscreen: " + (_fmt_list(near) if near else "(none)")
        )
        visible_aliases = [
            p.name
            for p in self.placements
            if p.kind == "alias" and p.relation == "visible"
        ][:8]
        if visible_aliases:
            lines.append("visible: " + ", ".join(visible_aliases))
        return "\n".join(lines)

    def nearby_payload(self) -> list[dict[str, Any]]:
        return [
            {
                "id": p.id,
                "name": p.name,
                "direction": _direction(p),
                "distance_px": p.distance_px,
            }
            for p in nearby_offscreen(self)
        ]


def begin_viewport_step() -> None:
    """Reuse one viewport read for the rest of this planner or executing step."""
    _viewport_step.active = True
    _viewport_step.token = getattr(_viewport_step, "token", 0) + 1


def end_viewport_step() -> None:
    """Drop the step cache. The next read measures again."""
    _viewport_step.active = False


def header_offset_px(graph: Any = None) -> int:
    """Pixels to keep clear under a sticky header. Per client via ``_meta``."""
    meta = getattr(graph, "meta", None)
    if isinstance(meta, dict) and meta.get("header_offset_px") is not None:
        try:
            return max(0, int(meta["header_offset_px"]))
        except (TypeError, ValueError):
            pass
    return DEFAULT_HEADER_OFFSET_PX


def classify_in_view(
    *,
    top: float,
    bottom: float,
    left: float,
    right: float,
    viewport_w: float,
    viewport_h: float,
    clip_top: float | None = None,
    clip_bottom: float | None = None,
    clip_left: float | None = None,
    clip_right: float | None = None,
) -> tuple[str, int, float]:
    """Classify a box against the window and any nested clip.

    A box that sits in the window but outside an overflow ancestor is
    off screen. Coordinates are shifted into that visible pane first.
    """
    view_top = 0.0 if clip_top is None else max(0.0, float(clip_top))
    view_bottom = (
        float(viewport_h) if clip_bottom is None else min(float(viewport_h), float(clip_bottom))
    )
    view_left = 0.0 if clip_left is None else max(0.0, float(clip_left))
    view_right = (
        float(viewport_w) if clip_right is None else min(float(viewport_w), float(clip_right))
    )
    if view_bottom - view_top <= 1 or view_right - view_left <= 1:
        return "below", 0, 0.0
    return classify_rect(
        top=top - view_top,
        bottom=bottom - view_top,
        left=left - view_left,
        right=right - view_left,
        viewport_w=view_right - view_left,
        viewport_h=view_bottom - view_top,
    )


def classify_rect(
    *,
    top: float,
    bottom: float,
    left: float,
    right: float,
    viewport_w: float,
    viewport_h: float,
) -> tuple[str, int, float]:
    """Return (relation, distance_px, visible_ratio) for one box.

    Distance is pixels outside the viewport edge (0 when fully on screen).
    """
    height = bottom - top
    width = right - left
    if height <= 1 or width <= 1 or viewport_w <= 0 or viewport_h <= 0:
        return "below", 0, 0.0
    iy1 = max(top, 0.0)
    iy2 = min(bottom, viewport_h)
    ix1 = max(left, 0.0)
    ix2 = min(right, viewport_w)
    inter = max(0.0, iy2 - iy1) * max(0.0, ix2 - ix1)
    ratio = inter / (height * width)
    if bottom <= 0:
        return "above", int(round(-bottom)), ratio
    if top >= viewport_h:
        return "below", int(round(top - viewport_h)), ratio
    if ratio >= 0.55:
        return "visible", 0, ratio
    above_clip = max(0.0, -top)
    below_clip = max(0.0, bottom - viewport_h)
    if above_clip >= below_clip and above_clip > 0:
        return "partial", int(round(above_clip)), ratio
    if below_clip > 0:
        return "partial", int(round(below_clip)), ratio
    return "partial", 0, ratio


def pick_current(placements: list[Placement]) -> Placement | None:
    """Section or landmark the screenshare is actually showing."""
    sections = [
        p
        for p in placements
        if p.kind in {"section", "landmark"} and p.relation in {"visible", "partial"}
    ]
    pool = sections or [
        p for p in placements if p.relation in {"visible", "partial"}
    ]
    if not pool:
        return None

    def _key(p: Placement) -> tuple:
        rank = 0 if p.relation == "visible" else 1
        # Closest to the top of the viewport, preferring ones already inside it.
        return (rank, abs(p.top) if p.top < 160 else p.top + 1000)

    return min(pool, key=_key)


def nearby_offscreen(awareness: ViewportAwareness) -> list[Placement]:
    """Closest sections above and below. Aliases fill in only when no section is off screen."""
    current = awareness.current_section_id
    sections = [
        p
        for p in awareness.placements
        if p.kind in {"section", "landmark"} and p.id != current and _is_off(p)
    ]
    chosen = _closest_each_direction(sections)
    if chosen:
        return chosen
    aliases = [
        p
        for p in awareness.placements
        if p.kind == "alias" and p.id != current and p.relation in {"above", "below"}
    ]
    aliases.sort(key=lambda p: p.distance_px)
    return aliases[:4]


def relation_for_label(awareness: ViewportAwareness, label: str) -> str | None:
    """visible/partial/above/below when `label` matches a placement, else None."""
    want = _norm(label)
    if not want:
        return None
    for p in awareness.placements:
        if _norm(p.name) == want or _norm(p.id) == want:
            return p.relation
    return None


def measure_viewport(
    page: Any, *, graph: Any = None, page_id: str = "", fresh: bool = False
) -> ViewportAwareness:
    """Read the live viewport once and place site-graph sections against it.

    Inside a step started with ``begin_viewport_step``, later calls for the
    same page reuse that read until a scroll or navigation asks for ``fresh``.
    """
    if page is None or not hasattr(page, "evaluate"):
        return ViewportAwareness()
    url = ""
    try:
        url = page.url or ""
    except Exception:  # noqa: BLE001
        url = ""
    if not fresh:
        cached = _recall(page, url)
        if cached is not None:
            return cached
    targets = _alias_targets(graph, page_id=page_id, page_url=url)
    try:
        raw = _eval(page, _MEASURE_JS, {"targets": targets})
    except Exception as exc:  # noqa: BLE001
        print(f"[viewport] measure skipped: {exc}", flush=True)
        return ViewportAwareness()
    if not isinstance(raw, dict):
        awareness = ViewportAwareness()
        _remember(page, url, awareness)
        return awareness
    awareness = _from_raw(raw, graph=graph, targets=targets)
    _remember(page, url, awareness)
    return awareness


def note_viewport_change(
    page: Any, graph: Any, page_id: str, *, why: str
) -> ViewportAwareness:
    """Remeasure after scroll or navigation and log the on-screen section."""
    awareness = measure_viewport(page, graph=graph, page_id=page_id, fresh=True)
    text = awareness.summary().replace("\n", " | ") or "currently on screen: (unknown)"
    print(f"[viewport] {why} {text}", flush=True)
    return awareness


def bring_alias_into_view(page: Any, graph: Any, page_id: str, alias: str) -> None:
    """Scroll toward an alias that is above or below the viewport. No-op when visible."""
    try:
        awareness = measure_viewport(page, graph=graph, page_id=page_id)
        hit = next(
            (p for p in awareness.placements if p.kind == "alias" and p.id == alias),
            None,
        )
        if hit is None or hit.relation == "visible":
            return
        if hit.relation == "partial" and hit.distance_px < _PARTIAL_NEARBY_PX:
            return
        _scroll_by(page, hit, graph=graph)
        note_viewport_change(page, graph, page_id, why="scroll")
    except Exception as exc:  # noqa: BLE001
        print(f"[viewport] scroll skipped: {exc}", flush=True)


def scroll_toward_label(page: Any, graph: Any, page_id: str, label: str) -> bool:
    """Scroll so a named section moves into view. False when it is unknown or already visible."""
    awareness = measure_viewport(page, graph=graph, page_id=page_id)
    rel = relation_for_label(awareness, label)
    if rel is None or rel == "visible":
        return False
    want = _norm(label)
    hit = next(
        (
            p
            for p in awareness.placements
            if _norm(p.name) == want or _norm(p.id) == want
        ),
        None,
    )
    if hit is None:
        return False
    _scroll_by(page, hit, graph=graph)
    note_viewport_change(page, graph, page_id, why="scroll")
    return True


def label_still_offscreen(page: Any, graph: Any, page_id: str, label: str) -> bool:
    """True only when the label matches a section that is still above or below."""
    rel = relation_for_label(
        measure_viewport(page, graph=graph, page_id=page_id), label
    )
    return rel in {"above", "below"}


def _scroll_by(page: Any, hit: Placement, *, graph: Any = None) -> None:
    header = header_offset_px(graph)
    origin = hit.scroll_top if hit.scroll_top or hit.container_top else hit.top
    # A nested pane cannot show anything above its own top. Aligning to the
    # window header would scroll the element past that clip.
    target = max(float(header), float(hit.container_top))
    delta = int(round(origin - target))
    if abs(delta) < 12:
        return
    try:
        _eval(page, _SCROLL_JS, {"css": hit.css or "", "delta": delta})
    except TimeoutError as exc:
        print(f"[viewport] scroll evaluate timed out: {exc}", flush=True)


def _from_raw(raw: dict[str, Any], *, graph: Any, targets: list[dict[str, str]]) -> ViewportAwareness:
    vw = float(raw.get("vw") or 0)
    vh = float(raw.get("vh") or 0)
    by_id = {t["id"]: t for t in targets}
    placements: list[Placement] = []
    seen: set[str] = set()

    for row in raw.get("headings") or []:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        placed = _place_row(row, vw=vw, vh=vh, css="")
        if placed is None:
            continue
        mapped = _match_page(text, graph)
        if mapped is not None:
            pid, name = mapped
            item = Placement(
                id=pid,
                name=name,
                kind="section",
                relation=placed.relation,
                distance_px=placed.distance_px,
                visible_ratio=placed.visible_ratio,
                top=placed.top,
                css=_section_css(
                    graph,
                    pid,
                    heading=text,
                    heading_css=str(row.get("css") or ""),
                ),
                scroll_top=placed.scroll_top,
                container_top=placed.container_top,
            )
        else:
            item = Placement(
                id="landmark:" + _norm(text)[:48],
                name=text[:80],
                kind="landmark",
                relation=placed.relation,
                distance_px=placed.distance_px,
                visible_ratio=placed.visible_ratio,
                top=placed.top,
                css=str(row.get("css") or ""),
                scroll_top=placed.scroll_top,
                container_top=placed.container_top,
            )
        if item.id in seen:
            continue
        seen.add(item.id)
        placements.append(item)

    for row in raw.get("measured") or []:
        if not isinstance(row, dict) or row.get("missing"):
            continue
        meta = by_id.get(str(row.get("id") or ""))
        if not meta:
            continue
        placed = _place_row(row, vw=vw, vh=vh, css=meta.get("css") or "")
        if placed is None or meta["id"] in seen:
            continue
        item = Placement(
            id=meta["id"],
            name=meta.get("name") or meta["id"],
            kind="alias",
            relation=placed.relation,
            distance_px=placed.distance_px,
            visible_ratio=placed.visible_ratio,
            top=placed.top,
            css=meta.get("css") or "",
            scroll_top=placed.scroll_top,
            container_top=placed.container_top,
        )
        if item.id in seen:
            continue
        seen.add(item.id)
        placements.append(item)

    current = pick_current(placements)
    return ViewportAwareness(
        viewport_w=int(vw),
        viewport_h=int(vh),
        scroll_x=int(raw.get("sx") or 0),
        scroll_y=int(raw.get("sy") or 0),
        current_section=current.name if current else "",
        current_section_id=current.id if current else "",
        placements=tuple(placements),
    )


def _place_row(row: dict[str, Any], *, vw: float, vh: float, css: str) -> Placement | None:
    try:
        top = float(row.get("top"))
        bottom = float(row.get("bottom"))
        left = float(row.get("left"))
        right = float(row.get("right"))
    except (TypeError, ValueError):
        return None
    relation, distance, ratio = classify_in_view(
        top=top,
        bottom=bottom,
        left=left,
        right=right,
        viewport_w=vw,
        viewport_h=vh,
        clip_top=_opt_float(row.get("clipTop")),
        clip_bottom=_opt_float(row.get("clipBottom")),
        clip_left=_opt_float(row.get("clipLeft")),
        clip_right=_opt_float(row.get("clipRight")),
    )
    raw_top = row.get("rawTop")
    scroll_top = float(raw_top) if raw_top is not None else top
    container_top = float(row.get("containerTop") or 0)
    return Placement(
        id="",
        name="",
        kind="",
        relation=relation,
        distance_px=distance,
        visible_ratio=ratio,
        top=top,
        css=css,
        scroll_top=scroll_top,
        container_top=container_top,
    )


def _opt_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _alias_targets(graph: Any, *, page_id: str, page_url: str) -> list[dict[str, str]]:
    pages = getattr(graph, "pages", None) or {}
    if not isinstance(pages, dict):
        return []
    out: list[dict[str, str]] = []
    for pid, spec in pages.items():
        if pid != page_id and not _same_document(page_url, getattr(spec, "url", "") or ""):
            continue
        selectors = getattr(spec, "selectors", None) or {}
        for alias, css in selectors.items():
            css_s = str(css or "").strip()
            if not css_s or css_s.lower() == "body":
                continue
            out.append(
                {
                    "id": str(alias),
                    "name": str(alias).replace("_", " "),
                    "css": css_s,
                }
            )
            if len(out) >= _ALIAS_CAP:
                return out
    return out


def _eval(page: Any, expression: str, arg: Any = None):
    """Run ``page.evaluate`` with a hard limit.

    An async stub is cancelled by ``asyncio.wait_for``. Sync Playwright
    receives the same limit as its own ``timeout`` (milliseconds).
    """
    import inspect

    from navigator.automation.browser.bounded import run_limited

    fn = page.evaluate
    if inspect.iscoroutinefunction(fn):
        return run_limited(fn(expression, arg), EVAL_TIMEOUT_S)
    timeout_ms = max(1, int(EVAL_TIMEOUT_S * 1000))
    try:
        return fn(expression, arg, timeout=timeout_ms)
    except TypeError:
        return fn(expression, arg)


def _section_css(
    graph: Any, page_id: str, *, heading: str = "", heading_css: str = ""
) -> str:
    """Selector for the heading that matched, never the page's first alias."""
    css = (heading_css or "").strip()
    if css and css.lower() != "body":
        return css
    pages = getattr(graph, "pages", None) or {}
    spec = pages.get(page_id) if isinstance(pages, dict) else None
    selectors = getattr(spec, "selectors", None) or {}
    want = _norm(heading)
    if not want:
        return ""
    for alias, raw in selectors.items():
        if _norm(str(alias)) != want and _norm(str(alias).replace("_", " ")) != want:
            continue
        css_s = str(raw or "").strip()
        if css_s and css_s.lower() != "body":
            return css_s
    return ""


def _recall(page: Any, url: str) -> ViewportAwareness | None:
    if not getattr(_viewport_step, "active", False):
        return None
    saved = getattr(page, "_navigator_viewport", None)
    if not saved:
        return None
    token, saved_url, awareness = saved
    if token != getattr(_viewport_step, "token", None) or saved_url != url:
        return None
    return awareness


def _remember(page: Any, url: str, awareness: ViewportAwareness) -> None:
    if not getattr(_viewport_step, "active", False):
        return
    try:
        page._navigator_viewport = (
            getattr(_viewport_step, "token", 0),
            url,
            awareness,
        )
    except Exception:  # noqa: BLE001 — a page that rejects attributes just remeasures
        pass


def _match_page(text: str, graph: Any) -> tuple[str, str] | None:
    pages = getattr(graph, "pages", None) or {}
    if not isinstance(pages, dict):
        return None
    folded = text.strip()
    if len(_norm(folded)) < 3:
        return None
    for pid, spec in pages.items():
        name = str(getattr(spec, "name", "") or "").strip()
        if not name:
            continue
        if _norm(name) == _norm(folded) or _norm(str(pid)) == _norm(folded):
            return str(pid), name
        if len(name) >= 4 and re.search(rf"\b{re.escape(name)}\b", folded, re.I):
            return str(pid), name
    return None


def _same_document(page_url: str, spec_url: str) -> bool:
    leaf = _leaf(spec_url)
    if not leaf:
        return False
    return leaf in (page_url or "").lower()


def _leaf(url: str) -> str:
    path = urlparse(url).path or url.split("?")[0]
    return path.rstrip("/").split("/")[-1].lower()


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _is_off(p: Placement) -> bool:
    if p.relation in {"above", "below"}:
        return True
    return p.relation == "partial" and p.distance_px >= _PARTIAL_NEARBY_PX


def _direction(p: Placement) -> str:
    if p.relation in {"above", "below"}:
        return p.relation
    return "above" if p.top < 0 else "below"


def _closest_each_direction(items: list[Placement]) -> list[Placement]:
    above = sorted(
        (p for p in items if _direction(p) == "above"),
        key=lambda p: p.distance_px,
    )[:_NEARBY_EACH]
    below = sorted(
        (p for p in items if _direction(p) == "below"),
        key=lambda p: p.distance_px,
    )[:_NEARBY_EACH]
    return above + below


def _fmt_list(items: list[Placement]) -> str:
    return "; ".join(
        f"{p.name} ({_direction(p)}, {p.distance_px}px)" for p in items
    )


_MEASURE_JS = """(spec) => {
  const vw = window.innerWidth || 0;
  const vh = window.innerHeight || 0;
  const clipsY = (oy) => oy === 'auto' || oy === 'scroll' || oy === 'hidden' || oy === 'clip';
  const clipsX = (ox) => ox === 'auto' || ox === 'scroll' || ox === 'hidden' || ox === 'clip';
  const cssEscape = (value) => {
    if (window.CSS && CSS.escape) return CSS.escape(value);
    return String(value).replace(/[^a-zA-Z0-9_-]/g, '\\\\$&');
  };
  const selectorFor = (el) => {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) {
      const sel = '#' + cssEscape(el.id);
      try { if (document.querySelector(sel) === el) return sel; } catch (e) {}
    }
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.body && node !== document.documentElement) {
      let part = node.tagName.toLowerCase();
      if (node.id) { parts.unshift('#' + cssEscape(node.id)); break; }
      const parent = node.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
        if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
      if (parts.length > 6) break;
      node = node.parentElement;
    }
    return parts.join(' > ');
  };
  const box = (el) => {
    const r = el.getBoundingClientRect();
    let top = r.top, bottom = r.bottom, left = r.left, right = r.right;
    let clipTop = 0, clipBottom = vh, clipLeft = 0, clipRight = vw;
    let containerTop = 0;
    let foundScroller = false;
    let node = el.parentElement;
    while (node && node !== document.documentElement) {
      const st = window.getComputedStyle(node);
      const oy = st.overflowY;
      const ox = st.overflowX;
      const cy = clipsY(oy);
      const cx = clipsX(ox);
      if (cy || cx) {
        const c = node.getBoundingClientRect();
        if (cy) {
          top = Math.max(top, c.top);
          bottom = Math.min(bottom, c.bottom);
          clipTop = Math.max(clipTop, c.top);
          clipBottom = Math.min(clipBottom, c.bottom);
        }
        if (cx) {
          left = Math.max(left, c.left);
          right = Math.min(right, c.right);
          clipLeft = Math.max(clipLeft, c.left);
          clipRight = Math.min(clipRight, c.right);
        }
        if (!foundScroller && (oy === 'auto' || oy === 'scroll') && node.scrollHeight > node.clientHeight + 8) {
          containerTop = c.top;
          foundScroller = true;
        }
      }
      node = node.parentElement;
    }
    return {
      top: r.top, bottom: r.bottom, left: r.left, right: r.right,
      rawTop: r.top, containerTop: foundScroller ? containerTop : 0,
      clipTop: clipTop, clipBottom: clipBottom, clipLeft: clipLeft, clipRight: clipRight,
      css: selectorFor(el)
    };
  };
  const measured = [];
  for (const item of (spec.targets || [])) {
    let el = null;
    try { el = document.querySelector(item.css); } catch (e) { el = null; }
    if (!el) { measured.push({id: item.id, missing: true}); continue; }
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) { measured.push({id: item.id, missing: true}); continue; }
    measured.push(Object.assign({id: item.id, missing: false}, box(el)));
  }
  const headings = [];
  const nodes = document.querySelectorAll(
    'h1,h2,h3,[role="region"][aria-label],section[aria-label],article[aria-label]'
  );
  for (const el of nodes) {
    if (headings.length >= 24) break;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    let text = (el.getAttribute('aria-label') || '').trim();
    if (!text) {
      const head = el.querySelector ? el.querySelector('h1,h2,h3') : null;
      const src = head || el;
      text = (src.innerText || src.textContent || '').trim().replace(/\\s+/g, ' ');
    }
    text = text.slice(0, 80);
    if (!text) continue;
    headings.push(Object.assign({text: text, tag: el.tagName || ''}, box(el)));
  }
  return {vw: vw, vh: vh, sx: Math.round(window.scrollX || 0), sy: Math.round(window.scrollY || 0),
          measured: measured, headings: headings};
}"""


_SCROLL_JS = """(spec) => {
  const delta = spec.delta || 0;
  let el = null;
  if (spec.css) {
    try { el = document.querySelector(spec.css); } catch (e) { el = null; }
  }
  let node = el ? el.parentElement : null;
  while (node && node !== document.body && node !== document.documentElement) {
    const st = window.getComputedStyle(node);
    const oy = st.overflowY;
    if ((oy === 'auto' || oy === 'scroll') && node.scrollHeight > node.clientHeight + 8) {
      node.scrollBy(0, delta);
      return true;
    }
    node = node.parentElement;
  }
  window.scrollBy(0, delta);
  return true;
}"""
