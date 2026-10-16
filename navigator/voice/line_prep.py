"""Prepare scripted lines before they reach Gemini Live.

A long English line sent to Live with "say this in Hindi" made the model
translate while speaking: audio arrived slower than realtime and the line
stuttered (30 underruns in one 20s line on 2026-10-14). Translating up front
with a fast text model and sending sentence-sized pieces keeps Live doing
only what it is fast at: reading text aloud.
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict

_LANG_NAMES = {"hi": "Hindi", "en": "English", "es": "Spanish", "fr": "French", "de": "German",
               "pt": "Portuguese", "ar": "Arabic", "bn": "Bengali", "ta": "Tamil", "te": "Telugu",
               "mr": "Marathi", "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam", "pa": "Punjabi"}
_CACHE: "OrderedDict[tuple[str, str], str]" = OrderedDict()
_LOCK = threading.Lock()
_INFLIGHT: dict[tuple[str, str], threading.Event] = {}
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")


def _already_in(text: str, lang: str) -> bool:
    if lang == "hi":
        letters = re.findall(r"[A-Za-z\u0900-\u097F]", text)
        return bool(letters) and len(_DEVANAGARI.findall(text)) / len(letters) > 0.5
    return False


def _script_ok(out: str, lang: str) -> bool:
    if lang != "hi":
        return True
    letters = re.findall(r"[A-Za-z\u0900-\u097F]", out)
    return bool(letters) and len(_DEVANAGARI.findall(out)) / len(letters) > 0.3


def _groq(prompt: str, model: str) -> str:
    from navigator.core.groq_client import chat_completions_create

    resp = chat_completions_create(
        purpose="live_line_translate", model=model,
        messages=[{"role": "user", "content": prompt}], temperature=0.2, max_tokens=1500,
        reasoning_effort="low", timeout=8,
    )
    return (resp.choices[0].message.content or "").strip()


def _ask(prompt: str, *, strong: bool = False) -> str:
    """Fast model first; ``strong`` uses the larger one. Never blocks > ~10s."""
    model = "openai/gpt-oss-120b" if strong else "openai/gpt-oss-20b"
    box: dict = {}

    def run() -> None:
        try:
            box["out"] = _groq(prompt, model)
        except Exception as exc:  # noqa: BLE001
            print(f"[line-prep] translate failed ({model}): {str(exc)[:100]}", flush=True)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(10.0)
    return box.get("out", "")


def translate(text: str, lang: str, *, timeout_s: float = 6.0) -> str:
    """Text in ``lang`` (cached). Returns the input unchanged on any failure."""
    text = (text or "").strip()
    lang = (lang or "en").lower()
    if not text or lang == "en" or _already_in(text, lang):
        return text
    key = (lang, text)
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
        ev = _INFLIGHT.get(key)
        mine = ev is None
        if mine:
            ev = _INFLIGHT[key] = threading.Event()
    if not mine:
        ev.wait(timeout_s)
        return _CACHE.get(key, text)
    try:
        name = _LANG_NAMES.get(lang, lang)
        prompt = (f"Translate this spoken product-demo narration into natural, conversational {name} "
                  f"as an Indian presenter would say it (keep product names, UI labels and button names "
                  f"in English). Reply with the translation only, no quotes.\n\n{text}")
        out = _ask(prompt).strip().strip('"').strip()
        if out and not _script_ok(out, lang):
            # The fast model sometimes echoes label-heavy lines back in English.
            strict = prompt.replace("Reply with the translation only",
                                    f"Write it in {name} script; only product names and UI labels stay in English. "
                                    "Reply with the translation only")
            out = _ask(strict, strong=True).strip().strip('"').strip()
        if not _script_ok(out, lang):
            print("[line-prep] translation not in target script — Live will translate", flush=True)
            out = text
        out = out if out and len(out) < 4 * len(text) + 200 else text
        with _LOCK:
            _CACHE[key] = out
            while len(_CACHE) > 400:
                _CACHE.popitem(last=False)
        return out
    finally:
        ev.set()
        with _LOCK:
            _INFLIGHT.pop(key, None)


def prefetch(text: str, lang: str) -> None:
    """Translate in the background so the line is ready when its turn comes."""
    if (lang or "en") == "en" or not (text or "").strip():
        return
    threading.Thread(target=translate, args=(text, lang), name="line-prefetch", daemon=True).start()


_SENT = re.compile(r"(?<=[.!?\u0964])\s+")


def pieces(text: str, max_chars: int = 220) -> list[str]:
    """Sentence-sized pieces (<= max_chars unless a single sentence is longer)."""
    sents = [s.strip() for s in _SENT.split((text or "").strip()) if s.strip()]
    out: list[str] = []
    buf = ""
    for s in sents:
        if buf and len(buf) + 1 + len(s) > max_chars:
            out.append(buf)
            buf = s
        else:
            buf = f"{buf} {s}".strip()
    if buf:
        out.append(buf)
    return out or ([text.strip()] if text.strip() else [])


def speak_chunks(text: str, *, min_clause: int = 16) -> list[str]:
    """Clause-sized pieces so the first audio request is not a whole sentence.

    Splits on sentence ends and, inside a sentence, on commas, semicolons,
    colons, and dashes once the left side is long enough to say. The first
    piece can be synthesized while the rest are still queued.
    """
    raw = (text or "").strip()
    if not raw:
        return []
    out: list[str] = []
    for sentence in [s.strip() for s in _SENT.split(raw) if s.strip()]:
        out.extend(_clauses(sentence, min_clause))
    return out or [raw]


def _clauses(sentence: str, min_clause: int) -> list[str]:
    pieces: list[str] = []
    rest = sentence.strip()
    while rest:
        cut = _clause_cut(rest, min_clause)
        if cut is None:
            pieces.append(rest)
            break
        left, right = rest[:cut].strip(), rest[cut:].strip()
        if not right:
            pieces.append(rest)
            break
        pieces.append(left)
        rest = right
    return [p for p in pieces if p]


def _clause_cut(text: str, min_clause: int) -> int | None:
    """Index just after the first clause break, or None."""
    for i, ch in enumerate(text):
        if i + 1 < min_clause:
            continue
        if ch in ",;:":
            if len(text[i + 1 :].strip()) >= 8:
                return i + 1
        if text.startswith(" — ", i) or text.startswith(" – ", i):
            if len(text[i + 3 :].strip()) >= 8:
                return i
    return None


def lead_and_rest(text: str, max_chars: int = 220) -> list[str]:
    """First sentence alone, then the rest in ``pieces``.

    The first piece can be spoken while later sentences are still translated
    or synthesized. A single sentence stays one piece (long ones still split
    only past ``max_chars``).
    """
    raw = (text or "").strip()
    if not raw:
        return []
    sents = [s.strip() for s in _SENT.split(raw) if s.strip()]
    if len(sents) <= 1:
        return pieces(raw, max_chars) if len(raw) > max_chars else [raw]
    rest = pieces(" ".join(sents[1:]), max_chars)
    return [sents[0], *rest]


def est_seconds(text: str, lang: str = "en") -> float:
    """Rough spoken length: ~2.6 words/s English, ~2.2 Hindi."""
    words = len((text or "").split())
    return words / (2.2 if lang == "hi" else 2.6)
