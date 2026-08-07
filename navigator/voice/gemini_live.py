"""Gemini Live native-audio TTS — warm Indian female voice for Meet demos."""

from __future__ import annotations

import asyncio
import array
import io
import threading
import wave
from typing import Literal

from navigator.voice.language import SpokenLanguage, language_code

DEFAULT_MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"
# Sulafat: warm female — works well for English and Hindi product demos.
DEFAULT_VOICE = "Sulafat"
OUTPUT_SAMPLE_RATE = 24_000
MEET_SAMPLE_RATE = 16_000


def _pcm_to_wav(pcm: bytes, *, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _resample_for_meet(pcm: bytes, *, src_rate: int = OUTPUT_SAMPLE_RATE) -> bytes:
    if not pcm or src_rate == MEET_SAMPLE_RATE:
        return pcm
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
    if not samples:
        return b""
    ratio = MEET_SAMPLE_RATE / float(src_rate)
    out = array.array("h")
    out_len = max(1, int(len(samples) * ratio))
    for i in range(out_len):
        src_idx = i / ratio
        idx = int(src_idx)
        frac = src_idx - idx
        if idx + 1 < len(samples):
            val = samples[idx] * (1.0 - frac) + samples[idx + 1] * frac
        else:
            val = float(samples[min(idx, len(samples) - 1)])
        out.append(int(max(-32768, min(32767, round(val)))))
    return out.tobytes()


class _GeminiLiveEngine:
    """Async Live session on a background thread (sync callers stay unchanged)."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str,
        voice_name: str,
        spoken_language: SpokenLanguage,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._voice_name = voice_name
        self._spoken_language = spoken_language
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, name="gemini-live", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=15):
            raise RuntimeError("Gemini Live engine failed to start")

    def close(self) -> None:
        try:
            asyncio.run_coroutine_threadsafe(self._shutdown(), self._loop).result(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=3)

    def set_language(self, lang: SpokenLanguage) -> None:
        if lang == self._spoken_language:
            return
        self._spoken_language = lang
        fut = asyncio.run_coroutine_threadsafe(self._reset_session(), self._loop)
        try:
            fut.result(timeout=10)
        except Exception as exc:  # noqa: BLE001
            print(f"[speak] gemini live language reset failed: {exc}", flush=True)

    def synthesize(self, text: str, *, spoken_language: SpokenLanguage | None = None) -> bytes | None:
        lang = spoken_language or self._spoken_language
        fut = asyncio.run_coroutine_threadsafe(
            self._synthesize(text, lang),
            self._loop,
        )
        try:
            return fut.result(timeout=90)
        except Exception as exc:  # noqa: BLE001
            from navigator.core.gemini_keys import is_gemini_quota_error

            if is_gemini_quota_error(exc):
                raise
            print(f"[speak] gemini live failed: {exc}", flush=True)
            return None

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._init_session())
        self._ready.set()
        self._loop.run_forever()

    async def _init_session(self) -> None:
        from google import genai

        self._client = genai.Client(api_key=self._api_key)
        self._session = None
        self._session_cm = None
        await self._open_session(self._spoken_language)

    async def _open_session(self, lang: SpokenLanguage) -> None:
        from google.genai import types

        await self._close_session()
        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            speech_config=types.SpeechConfig(
                language_code=language_code(lang),
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=self._voice_name,
                    ),
                ),
            ),
            system_instruction=_tts_system_instruction(lang),
        )
        self._session_cm = self._client.aio.live.connect(
            model=self._model,
            config=config,
        )
        self._session = await self._session_cm.__aenter__()

    async def _close_session(self) -> None:
        if self._session_cm is not None:
            try:
                await self._session_cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        self._session = None
        self._session_cm = None

    async def _reset_session(self) -> None:
        await self._open_session(self._spoken_language)

    async def _shutdown(self) -> None:
        await self._close_session()

    async def _synthesize(self, text: str, lang: SpokenLanguage) -> bytes | None:
        from google.genai import types

        if not text.strip():
            return None
        if lang != self._spoken_language:
            self._spoken_language = lang
            await self._open_session(lang)
        session = self._session
        if session is None:
            await self._open_session(lang)
            session = self._session
        if session is None:
            return None

        pcm = bytearray()
        prompt = _speak_prompt(text, lang)
        await session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=prompt)]),
            turn_complete=True,
        )
        async for msg in session.receive():
            if msg.data:
                pcm.extend(msg.data)
            sc = msg.server_content
            if sc is not None and sc.turn_complete:
                break
        if not pcm:
            return None
        meet_pcm = _resample_for_meet(bytes(pcm))
        return _pcm_to_wav(meet_pcm, sample_rate=MEET_SAMPLE_RATE)


def _tts_system_instruction(lang: SpokenLanguage) -> str:
    if lang == "hi":
        return (
            "You are a warm, professional Indian female product demo specialist. "
            "Speak natural Hindi (Devanagari script in any text; spoken Hindi aloud). "
            "Keep standard product/UI terms in English when that sounds natural to Indian users. "
            "Say only what you are asked to say — no extra greeting or explanation."
        )
    return (
        "You are a warm, professional Indian female product demo specialist. "
        "Speak natural Indian English with a clear, friendly tone. "
        "Say only what you are asked to say — no extra greeting or explanation."
    )


def _speak_prompt(text: str, lang: SpokenLanguage) -> str:
    line = text.strip()
    if lang == "hi":
        return f"Say this line aloud in natural Hindi exactly (minor natural phrasing ok):\n{line}"
    return f"Say this line aloud in natural Indian English exactly (minor natural phrasing ok):\n{line}"


class GeminiLiveSpeaker:
    """Cloud TTS via Gemini Live. synthesize_wav → 16 kHz WAV for Attendee."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        voice_name: str = DEFAULT_VOICE,
        spoken_language: SpokenLanguage = "en",
    ) -> None:
        from navigator.core.gemini_keys import gemini_key_candidates

        keys = gemini_key_candidates()
        primary = (api_key or "").strip()
        if primary and primary not in keys:
            keys = [primary, *keys]
        elif primary and not keys:
            keys = [primary]
        self._api_keys = keys
        self._key_idx = 0
        self.model = model or DEFAULT_MODEL
        self.voice_name = voice_name or DEFAULT_VOICE
        self.spoken_language: SpokenLanguage = spoken_language
        self._engine: _GeminiLiveEngine | None = None
        self._synth_lock = threading.Lock()

    def _close_engine(self) -> None:
        if self._engine is not None:
            self._engine.close()
            self._engine = None

    def _ensure_engine(self) -> _GeminiLiveEngine | None:
        if not self._api_keys or self._key_idx >= len(self._api_keys):
            return None
        if self._engine is None:
            self._engine = _GeminiLiveEngine(
                self._api_keys[self._key_idx],
                model=self.model,
                voice_name=self.voice_name,
                spoken_language=self.spoken_language,
            )
        return self._engine

    def available(self) -> bool:
        return bool(self._api_keys)

    def set_language(self, lang: SpokenLanguage) -> None:
        if lang not in ("en", "hi"):
            return
        self.spoken_language = lang
        engine = self._engine
        if engine is not None:
            engine.set_language(lang)

    def synthesize_wav(self, text: str) -> bytes | None:
        if not text.strip():
            return None
        from navigator.core.gemini_keys import is_gemini_quota_error

        # One Live session — concurrent recv from prefetch + say_async crashes.
        with self._synth_lock:
            while self._key_idx < len(self._api_keys):
                engine = self._ensure_engine()
                if engine is None:
                    return None
                try:
                    wav = engine.synthesize(
                        text, spoken_language=self.spoken_language
                    )
                except Exception as exc:  # noqa: BLE001
                    if is_gemini_quota_error(exc) and self._key_idx + 1 < len(
                        self._api_keys
                    ):
                        print(
                            "[speak] gemini live quota hit — switching to backup key",
                            flush=True,
                        )
                        self._close_engine()
                        self._key_idx += 1
                        continue
                    print(f"[speak] gemini live failed: {exc}", flush=True)
                    wav = None
                if wav:
                    return wav
                # Dead websocket / empty audio — rebuild session once, then next key.
                print(
                    "[speak] gemini live silent — resetting session",
                    flush=True,
                )
                self._close_engine()
                engine = self._ensure_engine()
                if engine is not None:
                    try:
                        wav = engine.synthesize(
                            text, spoken_language=self.spoken_language
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"[speak] gemini live retry failed: {exc}", flush=True)
                        wav = None
                    if wav:
                        return wav
                if self._key_idx + 1 < len(self._api_keys):
                    print(
                        "[speak] gemini live still silent — next API key",
                        flush=True,
                    )
                    self._close_engine()
                    self._key_idx += 1
                    continue
                return None
            return None
    def say(self, text: str) -> None:
        print(f"[speak] {text}", flush=True)
        _ = self.synthesize_wav(text)

    def close(self) -> None:
        self._close_engine()
