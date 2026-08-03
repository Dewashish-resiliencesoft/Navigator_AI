"""PERCEIVE: interactive-element inventory for the current page state.

Mirrors the manual recorder's element shape (`record.elInfo`) so
`prefer_selector` / `junk_record_reason` work on exploration output unchanged --
that shared shape is what makes both paths produce identical draft flows.
"""

from __future__ import annotations

from typing import Any

from playwright.sync_api import Page

_INVENTORY_JS = """
(() => {
  const SEL = [
    'button', 'a[href]', 'input', 'textarea', 'select',
    '[role=button]', '[role=link]', '[role=menuitem]', '[role=tab]',
    '[role=checkbox]', '[role=switch]', '[onclick]', '[data-testid]',
  ].join(',');
  const seen = new Set();
  const out = [];
  for (const t of document.querySelectorAll(SEL)) {
    if (out.length >= 200) break;
    const r = t.getBoundingClientRect();
    if (!r.width || !r.height) continue;               // invisible
    const style = window.getComputedStyle(t);
    if (style.visibility === 'hidden' || style.display === 'none') continue;
    if (t.disabled) continue;
    if (seen.has(t)) continue;
    seen.add(t);

    const tag = t.tagName.toLowerCase();
    const rawText = (t.innerText || t.value || '').trim();
    const firstLine = rawText.split('\\n').map(s => s.trim()).filter(Boolean)[0] || '';

    let label = '';
    if (t.id) {
      const lab = document.querySelector('label[for="' + CSS.escape(t.id) + '"]');
      if (lab) label = (lab.innerText || '').trim().slice(0, 60);
    }
    if (!label && t.closest) {
      const wrap = t.closest('label');
      if (wrap) label = (wrap.innerText || '').trim().slice(0, 60);
    }

    out.push({
      tag,
      id: t.id || '',
      name: t.getAttribute('name') || '',
      testid: t.getAttribute('data-testid') || '',
      text: firstLine.slice(0, 60),
      label: label,
      aria_label: t.getAttribute('aria-label') || '',
      title: t.getAttribute('title') || '',
      alt: t.getAttribute('alt') || '',
      role: t.getAttribute('role') || '',
      type: t.getAttribute('type') || t.type || '',
      autocomplete: t.getAttribute('autocomplete') || '',
      href: t.getAttribute('href') || '',
      value: tag === 'input' && t.type === 'submit' ? (t.value || '') : '',
      fillable: ['input', 'textarea', 'select'].includes(tag),
    });
  }
  return out;
})()
"""


def inventory(page: Page) -> list[dict[str, Any]]:
    """Visible, enabled, interactive elements. Never raises."""
    try:
        raw = page.evaluate(_INVENTORY_JS)
    except Exception as exc:  # noqa: BLE001
        print(f"[explore] inventory failed: {exc}", flush=True)
        return []
    return [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []


def screenshot_b64(page: Page) -> str:
    """Viewport PNG as base64, for the vision escalation path. "" on failure."""
    import base64

    try:
        return base64.b64encode(page.screenshot(type="png")).decode()
    except Exception as exc:  # noqa: BLE001
        print(f"[explore] screenshot failed: {exc}", flush=True)
        return ""


def is_fillable(el: dict[str, Any]) -> bool:
    if not el.get("fillable"):
        return False
    t = str(el.get("type") or "").lower()
    return t not in {"submit", "button", "reset", "hidden", "file", "image"}
