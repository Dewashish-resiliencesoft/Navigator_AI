"""Which site-graph sections are inside the live viewport.

One DOM read (`getBoundingClientRect` against `window.innerWidth/innerHeight`).
No model call. The planner sees the section on screen and a short nearby
off-screen list — not every alias on the page.

Updated after scroll and navigation. Scroll distance comes from that same
measurement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

_NEARBY_EACH = 3
_ALIAS_CAP = 40
_HEADING_CAP = 24
_PARTIAL_NEARBY_PX = 24


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


def measure_viewport(page: Any, *, graph: Any = None, page_id: str = "") -> ViewportAwareness:
    """Read the live viewport once and place site-graph sections against it."""
    if page is None or not hasattr(page, "evaluate"):
        return ViewportAwareness()
    url = ""
    try:
        url = page.url or ""
    except Exception:  # noqa: BLE001
        url = ""
    targets = _alias_targets(graph, page_id=page_id, page_url=url)
    try:
        raw = page.evaluate(_MEASURE_JS, {"targets": targets})
    except Exception as exc:  # noqa: BLE001
        print(f"[viewport] measure skipped: {exc}", flush=True)
        return ViewportAwareness()
    if not isinstance(raw, dict):
        return ViewportAwareness()
    return _from_raw(raw, graph=graph, targets=targets)


def note_viewport_change(
    page: Any, graph: Any, page_id: str, *, why: str
) -> ViewportAwareness:
    """Remeasure after scroll or navigation and log the on-screen section."""
    awareness = measure_viewport(page, graph=graph, page_id=page_id)
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
        _scroll_by(page, hit)
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
    _scroll_by(page, hit)
    note_viewport_change(page, graph, page_id, why="scroll")
    return True


def label_still_offscreen(page: Any, graph: Any, page_id: str, label: str) -> bool:
    """True only when the label matches a section that is still above or below."""
    rel = relation_for_label(
        measure_viewport(page, graph=graph, page_id=page_id), label
    )
    return rel in {"above", "below"}


def _scroll_by(page: Any, hit: Placement) -> None:
    delta = int(round(hit.top - 72))
    if abs(delta) < 12:
        return
    page.evaluate(
        _SCROLL_JS,
        {"css": hit.css or "", "delta": delta},
    )


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
                css=_section_css(graph, pid),
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
    relation, distance, ratio = classify_rect(
        top=top, bottom=bottom, left=left, right=right, viewport_w=vw, viewport_h=vh
    )
    return Placement(
        id="",
        name="",
        kind="",
        relation=relation,
        distance_px=distance,
        visible_ratio=ratio,
        top=top,
        css=css,
    )


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


def _section_css(graph: Any, page_id: str) -> str:
    pages = getattr(graph, "pages", None) or {}
    spec = pages.get(page_id) if isinstance(pages, dict) else None
    selectors = getattr(spec, "selectors", None) or {}
    for css in selectors.values():
        css_s = str(css or "").strip()
        if css_s and css_s.lower() != "body":
            return css_s
    return ""


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
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return {top: r.top, bottom: r.bottom, left: r.left, right: r.right,
            width: r.width, height: r.height};
  };
  const measured = [];
  for (const item of (spec.targets || [])) {
    let el = null;
    try { el = document.querySelector(item.css); } catch (e) { el = null; }
    if (!el) { measured.push({id: item.id, missing: true}); continue; }
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
  while (node && node !== document.body) {
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
