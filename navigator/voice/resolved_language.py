"""Single authoritative language resolution for a demo session.

Every component (Live config, intake, narration, acknowledgements, goodbye)
must use the same resolved language.  Nothing downstream should independently
re-resolve or fall back to English.

Resolution order:
  1. explicit session language (passed at demo start)
  2. agent_settings.default_language  (Client configured in dashboard)
  3. global settings.default_spoken_language
  4. "en" hard fallback

Log at startup so it is always visible in the run log:

    [language] requested=hi source=agent_settings resolved=hi
    [language] requested=en source=fallback    resolved=en
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SpokenLanguage = Literal["en", "hi"]

_VALID: frozenset[str] = frozenset({"en", "hi"})


@dataclass(frozen=True)
class ResolvedLanguage:
    code: SpokenLanguage
    source: Literal["session", "agent_settings", "global_default", "fallback"]

    def log(self) -> None:
        print(
            f"[language] source={self.source} resolved={self.code!r}",
            flush=True,
        )


def resolve_language(
    *,
    session_language: str | None = None,
    agent_settings_language: str | None = None,
    global_default: str | None = None,
) -> ResolvedLanguage:
    """Return the one language the entire session should use.

    Any component that needs the language must call this once at session start
    and pass the result through.  Do NOT re-call inside intake, narration, or
    acknowledgements — use the value already resolved.
    """
    def _valid(v: str | None) -> SpokenLanguage | None:
        if v and str(v).strip().lower() in _VALID:
            return str(v).strip().lower()  # type: ignore[return-value]
        return None

    if (v := _valid(session_language)):
        return ResolvedLanguage(code=v, source="session")
    if (v := _valid(agent_settings_language)):
        return ResolvedLanguage(code=v, source="agent_settings")
    if (v := _valid(global_default)):
        return ResolvedLanguage(code=v, source="global_default")
    return ResolvedLanguage(code="en", source="fallback")
