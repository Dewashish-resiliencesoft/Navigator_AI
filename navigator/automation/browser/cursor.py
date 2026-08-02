"""Visible cursor overlay for demos (CSS, not OS pointer).

Custom PNG cursor asset + hotspot stay fixed. Motion/timing is tunable via
module constants (MOTION_SCALE=0 makes tests instant).
"""

from __future__ import annotations

from playwright.sync_api import Page

# -- timing (tune after watching real demos) ---------------------------------
MOVE_MS_SHORT = 300
MOVE_MS_LONG = 600
MOVE_DIST_SHORT_PX = 180.0
MOVE_DIST_LONG_PX = 600.0
EASE = "cubic-bezier(0.4, 0, 0.2, 1)"
PAUSE_BEFORE_CLICK_MS = 220
PAUSE_AFTER_CLICK_MS = 200
HIGHLIGHT_FADE_MS = 280
RIPPLE_MS = 320
PRESS_MS = 120
SCROLL_MS = 450
# Tests / CI: set to 0 so waits and animations collapse.
MOTION_SCALE = 1.0

_CURSOR_DATA_URI = 'data:image/png;base64,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'

_CURSOR_JS = """
(() => {
  if (document.getElementById('nav-cursor')) return;
  const c = document.createElement('div');
  c.id = 'nav-cursor';
  c.style.cssText = [
    'position:fixed', 'left:0', 'top:0', 'width:27px', 'height:32px',
    'background-image:url("' + 'data:image/png;base64,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' + '")',
    'background-size:contain', 'background-repeat:no-repeat',
    'pointer-events:none',
    'z-index:2147483647', 'transform:translate(-5%,-4%)',
    'transform-origin:5% 4%',
  ].join(';');
  document.documentElement.appendChild(c);

  const r = document.createElement('div');
  r.id = 'nav-cursor-ripple';
  r.style.cssText = [
    'position:fixed', 'left:0', 'top:0', 'width:10px', 'height:10px',
    'border-radius:50%', 'border:2px solid #0a5c31', 'pointer-events:none',
    'z-index:2147483647', 'transform:translate(-50%,-50%) scale(0)', 'opacity:0',
  ].join(';');
  document.documentElement.appendChild(r);

  const h = document.createElement('div');
  h.id = 'nav-cursor-highlight';
  h.style.cssText = [
    'position:fixed', 'left:0', 'top:0', 'width:0', 'height:0',
    'border-radius:8px', 'border:2px solid rgba(10,92,49,0.55)',
    'box-shadow:0 0 0 4px rgba(10,92,49,0.14)',
    'pointer-events:none', 'z-index:2147483646', 'opacity:0',
    'transition:opacity 160ms ease',
  ].join(';');
  document.documentElement.appendChild(h);
})();
"""


def install_cursor(page: Page) -> None:
    page.add_init_script(_CURSOR_JS)
    page.evaluate(_CURSOR_JS)


def move_duration_ms(distance_px: float) -> float:
    """Map travel distance → move duration (ease-in-out range)."""
    d = max(0.0, float(distance_px))
    if d <= MOVE_DIST_SHORT_PX:
        return float(MOVE_MS_SHORT)
    if d >= MOVE_DIST_LONG_PX:
        return float(MOVE_MS_LONG)
    t = (d - MOVE_DIST_SHORT_PX) / (MOVE_DIST_LONG_PX - MOVE_DIST_SHORT_PX)
    return MOVE_MS_SHORT + t * (MOVE_MS_LONG - MOVE_MS_SHORT)


def _scaled_ms(ms: float) -> float:
    return max(0.0, float(ms) * float(MOTION_SCALE))


def _wait_ms(page: Page, ms: float) -> None:
    t = int(round(_scaled_ms(ms)))
    if t > 0:
        page.wait_for_timeout(t)


def move_cursor(page: Page, x: float, y: float, steps: int = 8) -> None:
    """Animate overlay to (x, y). ``steps`` kept for call-compat; unused."""
    _ = steps
    install_cursor(page)
    page.evaluate(
        """async ([x, y, shortMs, longMs, shortD, longD, ease, scale]) => {
          const c = document.getElementById('nav-cursor');
          if (!c) return 0;
          const x0 = parseFloat(c.style.left) || 0;
          const y0 = parseFloat(c.style.top) || 0;
          const dist = Math.hypot(x - x0, y - y0);
          let duration = shortMs;
          if (dist >= longD) duration = longMs;
          else if (dist > shortD) {
            const t = (dist - shortD) / (longD - shortD);
            duration = shortMs + t * (longMs - shortMs);
          }
          duration = Math.max(0, duration * scale);
          if (duration <= 0 || dist < 0.5) {
            c.style.left = x + 'px';
            c.style.top = y + 'px';
            return 0;
          }
          const anim = c.animate(
            [
              { left: x0 + 'px', top: y0 + 'px' },
              { left: x + 'px', top: y + 'px' },
            ],
            { duration, easing: ease, fill: 'forwards' },
          );
          try {
            await anim.finished;
          } catch (e) {}
          c.style.left = x + 'px';
          c.style.top = y + 'px';
          try { anim.cancel(); } catch (e) {}
          return duration;
        }""",
        [
            x,
            y,
            MOVE_MS_SHORT,
            MOVE_MS_LONG,
            MOVE_DIST_SHORT_PX,
            MOVE_DIST_LONG_PX,
            EASE,
            MOTION_SCALE,
        ],
    )


def _highlight_selector(page: Page, selector: str) -> None:
    page.evaluate(
        """(sel) => {
          const el = document.querySelector(sel);
          const h = document.getElementById('nav-cursor-highlight');
          if (!el || !h) return;
          const r = el.getBoundingClientRect();
          h.style.left = (r.left - 4) + 'px';
          h.style.top = (r.top - 4) + 'px';
          h.style.width = (r.width + 8) + 'px';
          h.style.height = (r.height + 8) + 'px';
          h.style.opacity = '1';
        }""",
        selector,
    )


def _clear_highlight(page: Page) -> None:
    page.evaluate(
        """() => {
          const h = document.getElementById('nav-cursor-highlight');
          if (h) h.style.opacity = '0';
        }"""
    )


def guide_to(
    page: Page,
    selector: str,
    timeout: float = 5000,
    *,
    highlight: bool = True,
) -> tuple[float, float]:
    """Smooth-scroll if needed, highlight, ease cursor to center, pause before action.

    Returns viewport center (x, y) of the target.
    """
    install_cursor(page)
    page.evaluate("(s) => { window.__navMotionScale = s; }", MOTION_SCALE)

    loc = page.locator(selector).first
    loc.wait_for(state="attached", timeout=timeout)

    scrolled = page.evaluate(
        """(sel) => {
          const el = document.querySelector(sel);
          if (!el) return false;
          const r = el.getBoundingClientRect();
          const vh = window.innerHeight || document.documentElement.clientHeight;
          const vw = window.innerWidth || document.documentElement.clientWidth;
          const margin = 48;
          const visible =
            r.top >= margin &&
            r.left >= margin &&
            r.bottom <= vh - margin &&
            r.right <= vw - margin;
          if (visible) return false;
          const smooth = (window.__navMotionScale || 0) > 0;
          el.scrollIntoView({
            behavior: smooth ? 'smooth' : 'instant',
            block: 'center',
            inline: 'nearest',
          });
          return true;
        }""",
        selector,
    )
    if scrolled:
        _wait_ms(page, SCROLL_MS)

    box = loc.bounding_box(timeout=timeout)
    if box is None:
        raise RuntimeError(f"no bounding box for {selector}")
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2

    if highlight:
        _highlight_selector(page, selector)
    move_cursor(page, x, y)
    _wait_ms(page, PAUSE_BEFORE_CLICK_MS)
    return x, y


def click_with_cursor(page: Page, selector: str, timeout: float = 5000) -> None:
    """Human-paced move → pause → visual click synced with Playwright click."""
    x, y = guide_to(page, selector, timeout=timeout, highlight=True)
    loc = page.locator(selector).first

    page.evaluate(
        """([x, y, rippleMs, pressMs, scale]) => {
          const r = document.getElementById('nav-cursor-ripple');
          const c = document.getElementById('nav-cursor');
          const rm = Math.max(0, rippleMs * scale);
          const pm = Math.max(0, pressMs * scale);
          if (r) {
            r.style.left = x + 'px';
            r.style.top = y + 'px';
            r.style.transition = 'none';
            r.style.transform = 'translate(-50%,-50%) scale(0)';
            r.style.opacity = '0.65';
            void r.offsetWidth;
            if (rm <= 0) {
              r.style.opacity = '0';
            } else {
              r.style.transition =
                'transform ' + rm + 'ms ease-out, opacity ' + rm + 'ms ease-out';
              r.style.transform = 'translate(-50%,-50%) scale(4)';
              r.style.opacity = '0';
            }
          }
          if (c && pm > 0) {
            c.animate(
              [
                { transform: 'translate(-5%,-4%) scale(1)' },
                { transform: 'translate(-5%,-4%) scale(0.86)' },
                { transform: 'translate(-5%,-4%) scale(1)' },
              ],
              { duration: pm, easing: 'ease-out' },
            );
          }
        }""",
        [x, y, RIPPLE_MS, PRESS_MS, MOTION_SCALE],
    )
    # Real click at press peak — synced with visual land
    _wait_ms(page, PRESS_MS / 2.0)
    loc.click(timeout=timeout)
    _wait_ms(page, max(RIPPLE_MS / 2.0, HIGHLIGHT_FADE_MS / 2.0))
    _clear_highlight(page)
    _wait_ms(page, PAUSE_AFTER_CLICK_MS)
