"""Turn a narrated recording into per-step spoken lines.

The Client records once: they click through their product and talk over it, the
way they would on a real sales call. This module takes the audio plus the click
timeline and answers "what was said while step N was happening".

Pure transforms over data -- the only I/O is the existing Groq Whisper call in
`navigator.voice.stt`, injectable for tests.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Sequence

#: Speech that lands this long before a click still belongs to it -- people say
#: "now I'll open the inbox" and *then* click.
LEAD_IN_MS = 2500

_LANG_NAMES: dict[str, str] = {
    "en": "English",
    "hi": "Hindi",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "ja": "Japanese",
    "ar": "Arabic",
}

_FILLER_RE = re.compile(
    r"\b(?:um+|uh+|ah+|er+|hm+|hmm+|like|you know|basically|i mean|sort of|kind of)\b",
    re.I,
)


@dataclass(frozen=True)
class Segment:
    start_ms: int
    end_ms: int
    text: str


def strip_fillers(text: str) -> str:
    """Cheap pre-pass before the LLM — drops obvious disfluencies."""
    if not text.strip():
        return ""
    cleaned = _FILLER_RE.sub(" ", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    cleaned = re.sub(r"\s+([,.!?])", r"\1", cleaned)
    return cleaned.strip()


def spoken_for_live_step(hint: str, *, max_len: int | None = None) -> str:
    """TTS-safe line from a recorded narration hint.

    Default keeps the full refined transcript — only disfluencies drop.
    Pass ``max_len`` when a caller needs a short planning fallback.
    """
    text = strip_fillers(hint or "")
    if not text:
        return ""
    if max_len is None:
        return text
    for sep in (". ", "! ", "? "):
        idx = text.find(sep)
        if 0 < idx <= max_len:
            return text[: idx + len(sep)].strip()
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0]
    return ((cut or text[:max_len]).strip() + "…") if cut else text[:max_len].strip()


def placeholder_narration_lines(steps: Sequence[Any]) -> list[str]:
    """Fallback per-step lines from element aliases when STT is unavailable."""
    lines: list[str] = []
    for step in steps:
        alias = str(getattr(step, "alias", "") or "").strip()
        label = alias.replace("_", " ").strip()
        if label:
            lines.append(f"Here is {label}.")
        else:
            lines.append("")
    return lines


def step_timings_from_steps(step_times_ms: Sequence[int]) -> list[dict[str, int]]:
    """Timing rows for every step from click schedule (including silent steps)."""
    out: list[dict[str, int]] = []
    for i, at in enumerate(step_times_ms):
        nxt = step_times_ms[i + 1] if i + 1 < len(step_times_ms) else at
        speak_ms = max(0, nxt - at)
        out.append({"idx": i, "speak_ms": speak_ms})
    return out


def transcribe_timed(
    audio: bytes,
    api_key: str,
    *,
    model: str = "whisper-large-v3-turbo",
    language: str = "auto",
    transcribe_verbose: Callable[..., Any] | None = None,
) -> list[Segment]:
    """Transcribe with per-segment timings. Empty list when there is no speech.

    Falls back to one whole-clip segment if the provider returns plain text --
    alignment then puts everything on the first step, which is wrong but
    recoverable by hand, unlike losing the recording.
    """
    if not audio:
        return []
    if transcribe_verbose is None:
        transcribe_verbose = _groq_verbose

    try:
        raw = transcribe_verbose(
            audio=audio, api_key=api_key, model=model, language=language
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[narrate] transcription failed: {exc}", flush=True)
        return []

    return parse_segments(raw)


def parse_segments(raw: Any) -> list[Segment]:
    """Normalise a Whisper verbose_json response (or plain text) to Segments."""
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        return [Segment(0, 0, text)] if text else []

    data = raw
    if not isinstance(data, dict):
        # SDK response object — pull the attributes we need.
        data = {
            "segments": getattr(raw, "segments", None),
            "text": getattr(raw, "text", "") or "",
        }

    segments = data.get("segments")
    if not isinstance(segments, list):
        text = str(data.get("text") or "").strip()
        return [Segment(0, 0, text)] if text else []

    out: list[Segment] = []
    for seg in segments:
        if not isinstance(seg, dict):
            seg = {
                "start": getattr(seg, "start", 0.0),
                "end": getattr(seg, "end", 0.0),
                "text": getattr(seg, "text", ""),
            }
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        out.append(
            Segment(
                start_ms=int(float(seg.get("start") or 0.0) * 1000),
                end_ms=int(float(seg.get("end") or 0.0) * 1000),
                text=text,
            )
        )
    return out


def _assign(
    segments: Sequence[Segment], step_times_ms: Sequence[int]
) -> list[list[Segment]]:
    """Bucket each spoken segment into the step it was describing.

    A segment belongs to the last step that started at or before it, with a
    lead-in window so narration spoken just *before* the click still lands on
    that click. Speech before the first click belongs to the first step.
    """
    buckets: list[list[Segment]] = [[] for _ in step_times_ms]
    if not step_times_ms:
        return []

    for seg in segments:
        idx = 0
        for i, at in enumerate(step_times_ms):
            if seg.start_ms + LEAD_IN_MS >= at:
                idx = i
            else:
                break
        buckets[idx].append(seg)

    return buckets


def align(segments: Sequence[Segment], step_times_ms: Sequence[int]) -> list[str]:
    """Spoken text per step -- what was said while step N was happening."""
    return [
        " ".join(seg.text for seg in bucket).strip()
        for bucket in _assign(segments, step_times_ms)
    ]


def speech_windows(
    segments: Sequence[Segment], step_times_ms: Sequence[int]
) -> list[tuple[int, int] | None]:
    """When narration for each step actually started and ended, in ms.

    This is what makes playback a copy rather than an approximation: the host
    usually starts talking *before* they click, and that lead-in is per-step.
    ``None`` for a step nobody narrated.
    """
    out: list[tuple[int, int] | None] = []
    for bucket in _assign(segments, step_times_ms):
        if not bucket:
            out.append(None)
            continue
        out.append(
            (
                min(seg.start_ms for seg in bucket),
                max(seg.end_ms for seg in bucket),
            )
        )
    return out


def speech_windows_payload(
    windows: Sequence[tuple[int, int] | None],
) -> list[dict[str, int]]:
    """`_meta.step_speech` rows. Silent steps are omitted, not zero-filled."""
    return [
        {"idx": i, "start_ms": int(win[0]), "end_ms": int(win[1])}
        for i, win in enumerate(windows)
        if win is not None
    ]


_REFINE_PROMPT = """You are a professional demo script editor polishing raw speech-to-text from a live product walkthrough.

The host clicked through their product while talking. Each numbered line below is what they said during one demo step. The transcript has:
- filler words (um, uh, ah, like, you know, basically, I mean)
- false starts and repetition
- grammar mistakes and run-on sentences
- speech-to-text errors (wrong words, missing punctuation, broken phrases)

Rewrite EACH line so it reads clearly aloud during the demo.

Rules:
- Keep EVERY fact, detail, UI label, button name, and instruction the speaker said. Do NOT summarize, shorten, or omit content.
- Fix grammar, punctuation, and speech-to-text errors only. You may split run-ons into sentences.
- Remove filler words (um, uh, ah, like, you know, basically, I mean) but keep all substantive words.
- Do not invent features or drop steps the speaker mentioned.
- No marketing fluff. Sound like a helpful colleague, not a brochure.
- Each output line should be at least as informative as the input — prefer slightly longer over missing detail.
- If a line is empty or only silence, return "".
- Output language for the script: {output_language}

Lines:
{lines}

Reply with JSON only: {{"lines": ["<clean line 1>", "<clean line 2>", ...]}}
The "lines" array MUST have exactly {count} entries, in the same order."""


_TRANSLATE_PROMPT = """Translate these product demo spoken lines into {lang_name}.

Keep product names, brand names, and UI labels unchanged unless there is a standard {lang_name} equivalent everyone uses.
One output line per input line. Empty lines stay empty.

Lines:
{lines}

Reply with JSON only: {{"lines": [...]}} with exactly {count} entries."""


def _refine_output_language(language: str) -> str:
    src = (language or "auto").strip().lower()
    if src in _LANG_NAMES:
        return _LANG_NAMES[src]
    return "English"


def refine(
    lines: Sequence[str],
    *,
    ask_text: Callable[[str], str] | None,
    language: str = "auto",
) -> list[str]:
    """One LLM pass over the whole flow. Returns the input unchanged on failure."""
    original = [strip_fillers(l) for l in lines]
    if not any(l.strip() for l in original) or ask_text is None:
        return list(lines)

    out_lang = _refine_output_language(language)
    listing = "\n".join(f"{i + 1}. {l or '(silence)'}" for i, l in enumerate(original))
    prompt = _REFINE_PROMPT.format(
        lines=listing, output_language=out_lang, count=len(original)
    )
    try:
        raw = ask_text(prompt)
    except Exception as exc:  # noqa: BLE001
        print(f"[narrate] refine failed: {exc}", flush=True)
        return list(lines)

    cleaned = _parse_lines_json(raw, expected=len(original))
    if cleaned is None:
        return list(lines)
    # Never let the model blank or shorten a line the human actually spoke.
    merged: list[str] = []
    for new, old in zip(cleaned, original):
        refined = str(new).strip()
        if not refined:
            merged.append(old)
            continue
        if old and len(refined.split()) < max(3, int(len(old.split()) * 0.55)):
            merged.append(old if old.strip() else refined)
            continue
        merged.append(refined)
    return merged


def translate_lines(
    lines: Sequence[str],
    *,
    target: str,
    ask_text: Callable[[str], str] | None,
) -> list[str]:
    """Translate refined lines when the Client wants a different script language."""
    original = list(lines)
    target = (target or "").strip().lower()
    if not target or target in {"same", "auto"} or not any(l.strip() for l in original):
        return original
    if ask_text is None:
        return original
    lang_name = _LANG_NAMES.get(target, target)
    listing = "\n".join(f"{i + 1}. {l or '(silence)'}" for i, l in enumerate(original))
    prompt = _TRANSLATE_PROMPT.format(
        lang_name=lang_name, lines=listing, count=len(original)
    )
    try:
        raw = ask_text(prompt)
    except Exception as exc:  # noqa: BLE001
        print(f"[narrate] translate failed: {exc}", flush=True)
        return original
    translated = _parse_lines_json(raw, expected=len(original))
    if translated is None:
        return original
    return [
        str(new).strip() or old
        for new, old in zip(translated, original)
    ]


def _parse_lines_json(raw: str | None, *, expected: int) -> list[str] | None:
    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    cleaned = data.get("lines") if isinstance(data, dict) else None
    if not isinstance(cleaned, list) or len(cleaned) != expected:
        return None
    return [str(x) for x in cleaned]


def skip_indices(lines: Sequence[str], step_times_ms: Sequence[int]) -> set[int]:
    """Steps to drop: silent AND indistinguishable from the click before them.

    A recorded walkthrough contains stray taps -- opening a menu to close it,
    double-clicking a row. If the host said nothing and the step landed within a
    blink of the previous one, it was noise, not a demo beat.
    """
    drop: set[int] = set()
    for i, line in enumerate(lines):
        if line.strip():
            continue
        if i == 0:
            continue
        gap = step_times_ms[i] - step_times_ms[i - 1] if i < len(step_times_ms) else 0
        if 0 <= gap < 700:
            drop.add(i)
    return drop


def step_timings(step_times_ms: Sequence[int], lines: Sequence[str]) -> list[dict[str, int]]:
    """How long the human spent on each step, for playback pacing."""
    out: list[dict[str, int]] = []
    for i, at in enumerate(step_times_ms):
        nxt = step_times_ms[i + 1] if i + 1 < len(step_times_ms) else at
        speak_ms = max(0, nxt - at)
        if i < len(lines) and lines[i].strip():
            out.append({"idx": i, "speak_ms": speak_ms})
    return out


def narrate_recording(
    *,
    audio: bytes,
    steps: Sequence[Any],
    api_key: str,
    ask_text: Callable[[str], str] | None = None,
    transcribe_verbose: Callable[..., Any] | None = None,
    language: str = "auto",
    translate_to: str = "same",
) -> tuple[list[str], list[dict[str, int]], list[tuple[int, int] | None]]:
    """Full pipeline: audio + steps → (line per step, timing hints, speech windows).

    Returns empty lists when there was no narration, so callers can fall through
    to the existing generated-narration path unchanged.
    """
    if not audio or not steps:
        return [], [], []

    step_times = [int(getattr(s, "at_ms", 0) or 0) for s in steps]
    segments = transcribe_timed(
        audio,
        api_key,
        language=language,
        transcribe_verbose=transcribe_verbose,
    )
    if not segments:
        return [], [], []

    lines = align(segments, step_times)
    # Taken before refine/translate rewrite the text -- the *timing* of what the
    # host said does not change when the wording is cleaned up.
    windows = speech_windows(segments, step_times)
    try:
        lines = refine(lines, ask_text=ask_text, language=language)
    except Exception as exc:  # noqa: BLE001
        print(f"[narrate] refine skipped: {exc}", flush=True)
    tgt = (translate_to or "same").strip().lower()
    src = (language or "auto").strip().lower()
    if tgt not in {"", "same", "auto"} and tgt != src:
        try:
            lines = translate_lines(lines, target=tgt, ask_text=ask_text)
        except Exception as exc:  # noqa: BLE001
            print(f"[narrate] translate skipped: {exc}", flush=True)
    return lines, step_timings(step_times, lines), windows


def _groq_verbose(
    *, audio: bytes, api_key: str, model: str, language: str = "auto"
) -> Any:
    """Whisper with segment timings. Same client the live STT path uses."""
    from navigator.core.groq_client import transcribe_create
    from navigator.voice.stt import pcm16_to_wav_bytes

    payload = audio
    # WebM/Opus from MediaRecorder is sent as-is; raw PCM needs a WAV header.
    if audio[:4] not in (b"RIFF", b"\x1a\x45\xdf\xa3", b"OggS"):
        payload = pcm16_to_wav_bytes(audio)

    kwargs: dict[str, Any] = {
        "file": ("narration.webm", payload, "audio/webm"),
        "model": model,
        "response_format": "verbose_json",
    }
    lang = (language or "auto").strip().lower()
    if lang and lang not in {"auto", "same"}:
        kwargs["language"] = lang

    return transcribe_create(api_key, **kwargs)
