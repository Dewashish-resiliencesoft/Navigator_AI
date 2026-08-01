"""Visible cursor overlay for demos (CSS, not OS pointer)."""

from __future__ import annotations

import time

from playwright.sync_api import Page

_CURSOR_JS = """
(() => {
  if (document.getElementById('nav-cursor')) return;
  const c = document.createElement('div');
  c.id = 'nav-cursor';
  c.style.cssText = [
    'position:fixed', 'left:0', 'top:0', 'width:18px', 'height:18px',
    'border-radius:50%', 'border:2px solid #0a5c31',
    'background:rgba(10,92,49,0.35)', 'pointer-events:none',
    'z-index:2147483647', 'transform:translate(-50%,-50%)',
    'transition:left 80ms linear, top 80ms linear',
  ].join(';');
  document.documentElement.appendChild(c);
  const r = document.createElement('div');
  r.id = 'nav-cursor-ripple';
  r.style.cssText = [
    'position:fixed', 'left:0', 'top:0', 'width:8px', 'height:8px',
    'border-radius:50%', 'border:2px solid #0a5c31', 'pointer-events:none',
    'z-index:2147483647', 'transform:translate(-50%,-50%) scale(0)', 'opacity:0',
  ].join(';');
  document.documentElement.appendChild(r);
})();
"""


def install_cursor(page: Page) -> None:
    page.add_init_script(_CURSOR_JS)
    page.evaluate(_CURSOR_JS)


def move_cursor(page: Page, x: float, y: float, steps: int = 8) -> None:
    install_cursor(page)
    page.evaluate(
        """([x, y, steps]) => {
          const c = document.getElementById('nav-cursor');
          if (!c) return;
          const x0 = parseFloat(c.style.left) || 0;
          const y0 = parseFloat(c.style.top) || 0;
          for (let i = 1; i <= steps; i++) {
            const t = i / steps;
            c.style.left = (x0 + (x - x0) * t) + 'px';
            c.style.top = (y0 + (y - y0) * t) + 'px';
          }
        }""",
        [x, y, steps],
    )
    time.sleep(0.01 * max(steps, 1))


def click_with_cursor(page: Page, selector: str, timeout: float = 5000) -> None:
    loc = page.locator(selector).first
    box = loc.bounding_box(timeout=timeout)
    if box is None:
        raise RuntimeError(f"no bounding box for {selector}")
    x = box["x"] + box["width"] / 2
    y = box["y"] + box["height"] / 2
    move_cursor(page, x, y)
    page.evaluate(
        """([x, y]) => {
          const r = document.getElementById('nav-cursor-ripple');
          if (!r) return;
          r.style.left = x + 'px';
          r.style.top = y + 'px';
          r.style.transition = 'transform 300ms ease-out, opacity 300ms ease-out';
          r.style.transform = 'translate(-50%,-50%) scale(4)';
          r.style.opacity = '0.6';
          setTimeout(() => {
            r.style.opacity = '0';
            r.style.transform = 'translate(-50%,-50%) scale(0)';
          }, 320);
        }""",
        [x, y],
    )
    loc.click(timeout=timeout)
