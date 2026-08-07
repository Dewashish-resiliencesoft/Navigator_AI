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


@dataclass(frozen=True)
class Segment:
    start_ms: int
    end_ms: int
    text: str


def transcribe_timed(
    audio: bytes,
    api_key: str,
    *,
    model: str = "whisper-large-v3-turbo",
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
        raw = transcribe_verbose(audio=audio, api_key=api_key, model=model)
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


def align(segments: Sequence[Segment], step_times_ms: Sequence[int]) -> list[str]:
    """Assign each spoken segment to the step it was describing.

    A segment belongs to the last step that started at or before it, with a
    lead-in window so narration spoken just *before* the click still lands on
    that click. Speech before the first click belongs to the first step.
    """
    lines: list[list[str]] = [[] for _ in step_times_ms]
    if not step_times_ms:
        return []

    for seg in segments:
        idx = 0
        for i, at in enumerate(step_times_ms):
            if seg.start_ms + LEAD_IN_MS >= at:
                idx = i
            else:
                break
        lines[idx].append(seg.text)

    return [" ".join(parts).strip() for parts in lines]


_REFINE_PROMPT = """You are cleaning up a product demo host's spoken narration.

Below are raw speech-to-text lines, one per demo step. They contain filler
("umm", "so basically"), false starts, and repetition, because a human was
talking while clicking.

Rewrite each line as one or two clean spoken sentences a demo host would say
out loud while performing that step. Keep the speaker's meaning and any concrete
product detail they mentioned. Do not invent features. Do not add marketing
language. If a line is empty, return an empty string for it.

Lines:
{lines}

Reply with JSON only: {{"lines": ["<clean line 1>", "<clean line 2>", ...]}}"""


def refine(
    lines: Sequence[str], *, ask_text: Callable[[str], str] | None
) -> list[str]:
    """One LLM pass over the whole flow. Returns the input unchanged on failure."""
    original = list(lines)
    if not any(l.strip() for l in original) or ask_text is None:
        return original

    listing = "\n".join(f"{i + 1}. {l or '(silence)'}" for i, l in enumerate(original))
    try:
        raw = ask_text(_REFINE_PROMPT.format(lines=listing))
    except Exception as exc:  # noqa: BLE001
        print(f"[narrate] refine failed: {exc}", flush=True)
        return original

    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return original
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return original
    cleaned = data.get("lines") if isinstance(data, dict) else None
    if not isinstance(cleaned, list) or len(cleaned) != len(original):
        return original
    # Never let the model blank a line the human actually spoke.
    return [
        str(new).strip() or old
        for new, old in zip(cleaned, original)
    ]


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
) -> tuple[list[str], list[dict[str, int]]]:
    """Full pipeline: audio + steps → (spoken line per step, timing hints).

    Returns empty lists when there was no narration, so callers can fall through
    to the existing generated-narration path unchanged.
    """
    if not audio or not steps:
        return [], []

    step_times = [int(getattr(s, "at_ms", 0) or 0) for s in steps]
    segments = transcribe_timed(
        audio, api_key, transcribe_verbose=transcribe_verbose
    )
    if not segments:
        return [], []

    lines = align(segments, step_times)
    lines = refine(lines, ask_text=ask_text)
    return lines, step_timings(step_times, lines)


def _groq_verbose(*, audio: bytes, api_key: str, model: str) -> Any:
    """Whisper with segment timings. Same client the live STT path uses."""
    from navigator.core.groq_client import groq_client
    from navigator.voice.stt import pcm16_to_wav_bytes

    payload = audio
    # WebM/Opus from MediaRecorder is sent as-is; raw PCM needs a WAV header.
    if audio[:4] not in (b"RIFF", b"\x1a\x45\xdf\xa3", b"OggS"):
        payload = pcm16_to_wav_bytes(audio)

    return groq_client(api_key).audio.transcriptions.create(
        file=("narration.webm", payload, "audio/webm"),
        model=model,
        response_format="verbose_json",
    )
