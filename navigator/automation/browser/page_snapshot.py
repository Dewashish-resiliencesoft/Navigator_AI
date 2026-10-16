"""Compact per-step view of the live page.

One ``page.evaluate`` — no screenshot and no model call. The planner sees
what is on screen, where the viewport and cursor are, and which notable
elements sit below the fold. Scroll uses the live viewport (whatever size
the browser was launched at) and an instant scroll, so a CSS smooth-scroll
animation cannot leave the target off-screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_MEASURE_JS = """
() => {
  const vw = window.innerWidth || 0;
  const vh = window.innerHeight || 0;
  const sx = Math.round(window.scrollX || 0);
  const sy = Math.round(window.scrollY || 0);
  const cur = document.getElementById('nav-cursor');
  let cursor = null;
  if (cur) {
    const r = cur.getBoundingClientRect();
    cursor = {
      x: Math.round(r.left + r.width / 2),
      y: Math.round(r.top + r.height / 2)
    };
  }
  const sel = 'h1,h2,h3,a,button,input,textarea,select,[role="button"],[aria-label]';
  const nodes = Array.from(document.querySelectorAll(sel)).slice(0, 80);
  const visible = [];
  const below = [];
  const seen = new Set();
  for (const el of nodes) {
    if (el.id === 'nav-cursor' || el.closest('#nav-cursor')) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    let label = (el.getAttribute('aria-label') || el.innerText || el.value || '').trim();
    while (label.indexOf('  ') !== -1) label = label.split('  ').join(' ');
    label = label.slice(0, 60);
    if (!label || seen.has(label)) continue;
    const onScreen = r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;
    if (onScreen && r.top >= -8) {
      if (visible.length < 10) {
        seen.add(label);
        visible.push(label);
      }
    } else if (r.top >= vh && below.length < 6) {
      seen.add(label);
      below.push({label: label, distance_px: Math.round(r.top - vh)});
    }
  }
  return {vw, vh, sx, sy, cursor, visible, below};
}
"""

# Instant scroll. ``behavior:'smooth'`` waits on CSS and misses an inner
# overflow container, which is how a step sat off-screen until the poll gave up.
_SCROLL_EL_JS = """
(el) => {
  if (!el || !el.getBoundingClientRect) return {ok: false};
  const vh = window.innerHeight || 0;
  const vw = window.innerWidth || 0;
  const r = el.getBoundingClientRect();
  const inView = r.width > 1 && r.height > 1
    && r.top >= 8 && r.bottom <= vh - 8 && r.left >= 0 && r.right <= vw;
  if (inView) return {ok: true, scrolled: false, vw: vw, vh: vh};
  let node = el.parentElement;
  let scroller = null;
  while (node && node !== document.documentElement) {
    const oy = window.getComputedStyle(node).overflowY;
    if ((oy === 'auto' || oy === 'scroll' || oy === 'overlay')
        && node.scrollHeight > node.clientHeight + 8) {
      scroller = node;
      break;
    }
    node = node.parentElement;
  }
  const delta = (r.top + r.height / 2) - (vh / 2);
  if (scroller) scroller.scrollBy(0, delta);
  else window.scrollBy(0, delta);
  return {ok: true, scrolled: true, vw: vw, vh: vh, delta: Math.round(delta)};
}
"""


@dataclass
class PageSnapshot:
    viewport_w: int = 0
    viewport_h: int = 0
    scroll_x: int = 0
    scroll_y: int = 0
    cursor_x: int | None = None
    cursor_y: int | None = None
    visible: list[str] = field(default_factory=list)
    below: list[tuple[str, int]] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"viewport={self.viewport_w}x{self.viewport_h} "
            f"scroll=({self.scroll_x},{self.scroll_y})"
        ]
        if self.cursor_x is not None and self.cursor_y is not None:
            lines.append(f"cursor=({self.cursor_x},{self.cursor_y})")
        lines.append("visible: " + (", ".join(self.visible) or "(none)"))
        if self.below:
            bits = [f"{name} ({dist}px below)" for name, dist in self.below]
            lines.append("below the fold: " + "; ".join(bits))
        return "\n".join(lines)


def measure_page(page) -> PageSnapshot:
    """Read viewport, scroll, cursor, and on/off-screen labels. Never raises."""
    try:
        raw = page.evaluate(_MEASURE_JS) or {}
    except Exception as exc:  # noqa: BLE001
        print(f"[page] snapshot failed: {exc}", flush=True)
        return PageSnapshot()
    if not isinstance(raw, dict):
        return PageSnapshot()
    cursor = raw.get("cursor") if isinstance(raw.get("cursor"), dict) else {}
    visible = [str(x) for x in (raw.get("visible") or []) if str(x).strip()]
    below: list[tuple[str, int]] = []
    for item in raw.get("below") or []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        if not label:
            continue
        try:
            dist = int(item.get("distance_px") or 0)
        except (TypeError, ValueError):
            dist = 0
        below.append((label, dist))
    cx = cursor.get("x") if cursor else None
    cy = cursor.get("y") if cursor else None
    return PageSnapshot(
        viewport_w=int(raw.get("vw") or 0),
        viewport_h=int(raw.get("vh") or 0),
        scroll_x=int(raw.get("sx") or 0),
        scroll_y=int(raw.get("sy") or 0),
        cursor_x=int(cx) if cx is not None else None,
        cursor_y=int(cy) if cy is not None else None,
        visible=visible,
        below=below,
    )


def planner_screen(page, *, max_chars: int = 900) -> str:
    """Url, title, and the compact snapshot. Falls back to body text if empty."""
    try:
        url = page.url or ""
    except Exception:  # noqa: BLE001
        url = ""
    try:
        title = page.title() or ""
    except Exception:  # noqa: BLE001
        title = ""
    snap = measure_page(page)
    body = snap.summary() if (snap.viewport_w or snap.visible or snap.below) else ""
    if not body:
        from navigator.automation.browser.screen_context import screen_snapshot

        return screen_snapshot(page, max_chars=max_chars)
    text = f"url={url}\ntitle={title}\n{body}"
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "…"
    return text


def attach_snapshot(page, screen: str, *, limit: int = 1200) -> str:
    """Append a snapshot when the screen string does not already have one."""
    base = screen or ""
    if page is None or "viewport=" in base:
        return base[:limit]
    extra = measure_page(page).summary()
    if not extra or extra.startswith("viewport=0x0"):
        return base[:limit]
    merged = f"{base}\n{extra}".strip() if base else extra
    return merged[:limit]


def scroll_locator_into_view(page, loc) -> bool:
    """Instant-scroll ``loc`` to the middle of the live viewport.

    Scrolls the nearest overflow ancestor, otherwise the window, once.
    Returns False when the element cannot be measured.
    """
    try:
        info = loc.evaluate(_SCROLL_EL_JS)
    except Exception as exc:  # noqa: BLE001
        print(f"[page] scroll-into-view failed: {exc}", flush=True)
        return False
    if not isinstance(info, dict) or not info.get("ok"):
        return False
    if info.get("scrolled"):
        print(
            f"[page] scrolled target into view "
            f"delta={info.get('delta')} viewport={info.get('vw')}x{info.get('vh')}",
            flush=True,
        )
    return True
