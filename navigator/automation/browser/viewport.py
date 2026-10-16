"""One Playwright viewport for every path that looks at a Client site.

Live demos, recording, explore, and authoring used to mix ``no_viewport``
with a hard-coded 1280×720, so the agent's idea of the screen did not match
the browser. Call ``context_kwargs()`` (or ``viewport_dict()`` for
``browser.new_page``) instead of writing a size inline.

The default is 1920×1080. Meet delivers screen share at most at 1080p30, and
ops set Attendee's ``WEBPAGE_STREAMER_VIDEO_FRAME_SIZE`` to the same size.
"""

from __future__ import annotations


def viewport_px() -> tuple[int, int]:
    """Even CSS pixel size, clamped so a bad env value cannot collapse the page."""
    from navigator.core.settings import settings

    w = int(getattr(settings, "viewport_width", 1920) or 1920)
    h = int(getattr(settings, "viewport_height", 1080) or 1080)
    w = max(320, min(3840, w))
    h = max(240, min(2160, h))
    w -= w % 2
    h -= h % 2
    return w, h


def viewport_dict() -> dict[str, int]:
    w, h = viewport_px()
    return {"width": w, "height": h}


def context_kwargs() -> dict[str, object]:
    """Arguments for ``browser.new_context``."""
    return {"viewport": viewport_dict(), "device_scale_factor": 1}
