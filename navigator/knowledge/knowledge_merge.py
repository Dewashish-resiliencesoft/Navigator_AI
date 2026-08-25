"""Dual-source knowledge: user MD + explore MD → canonical .md via AI merge."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent / "products"


def _safe(product_id: str) -> str:
    return (product_id or "default").strip() or "default"


def user_path(product_id: str) -> Path:
    return _ROOT / f"{_safe(product_id)}.user.md"


def explore_path(product_id: str) -> Path:
    return _ROOT / f"{_safe(product_id)}.explore.md"


def canonical_path(product_id: str) -> Path:
    return _ROOT / f"{_safe(product_id)}.md"


def merged_at_path(product_id: str) -> Path:
    return _ROOT / f"{_safe(product_id)}.merged_at"


def _read(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = text if (text or "").endswith("\n") else (text or "") + ("\n" if text else "")
    path.write_text(body, encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def ensure_user_from_canonical(product_id: str) -> None:
    """One-time: if Client already had .md and no .user.md, treat .md as user source."""
    u = user_path(product_id)
    c = canonical_path(product_id)
    if u.is_file() or not c.is_file():
        return
    _write(u, c.read_text(encoding="utf-8"))


def load_knowledge_bundle(product_id: str) -> dict[str, str | None]:
    ensure_user_from_canonical(product_id)
    merged = _read(merged_at_path(product_id)) or None
    return {
        "user_markdown": _read(user_path(product_id)),
        "explore_markdown": _read(explore_path(product_id)),
        "markdown": _read(canonical_path(product_id)),
        "merged_at": merged,
    }


def save_user_markdown(product_id: str, text: str) -> str:
    return _write(user_path(product_id), text)


def save_explore_markdown(product_id: str, text: str) -> str:
    return _write(explore_path(product_id), text)


def save_canonical_markdown(product_id: str, text: str) -> str:
    return _write(canonical_path(product_id), text)


def _merge_simple(user_md: str, explore_md: str) -> str:
    parts: list[str] = []
    if user_md.strip():
        parts.append("# Client knowledge\n\n" + user_md.strip())
    if explore_md.strip():
        parts.append("# Product explore\n\n" + explore_md.strip())
    return "\n\n---\n\n".join(parts).strip()


def _merge_with_llm(user_md: str, explore_md: str) -> str | None:
    try:
        from navigator.core.settings import settings

        key = (settings.groq_api_key or "").strip()
        if not key:
            return None
        from groq import Groq

        client = Groq(api_key=key)
        prompt = (
            "Merge these two product knowledge documents into one clean markdown brief "
            "for a live product demo agent. Keep unique facts, collapse duplicates, "
            "prefer explore for UI/feature facts, prefer client text for positioning/"
            "pricing when they conflict. No preamble — output markdown only.\n\n"
            f"## Client knowledge\n{user_md or '(empty)'}\n\n"
            f"## Explore knowledge\n{explore_md or '(empty)'}\n"
        )
        resp = client.chat.completions.create(
            model=settings.brain_phrasing_model or "llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=4000,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or None
    except Exception as exc:  # noqa: BLE001
        print(f"[knowledge] merge LLM skipped: {exc}", flush=True)
        return None


def auto_merge_knowledge(product_id: str) -> dict[str, str | None]:
    """Rebuild canonical .md from user + explore sources. No merge button."""
    ensure_user_from_canonical(product_id)
    user_md = _read(user_path(product_id))
    explore_md = _read(explore_path(product_id))
    if user_md and not explore_md:
        canonical = user_md
    elif explore_md and not user_md:
        canonical = explore_md
    elif not user_md and not explore_md:
        canonical = ""
    else:
        canonical = _merge_with_llm(user_md, explore_md) or _merge_simple(
            user_md, explore_md
        )
    save_canonical_markdown(product_id, canonical)
    now = datetime.now(timezone.utc).isoformat()
    _write(merged_at_path(product_id), now)
    return load_knowledge_bundle(product_id)
