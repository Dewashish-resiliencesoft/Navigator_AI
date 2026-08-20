"""Conversation language: confidence, mixed speech, echo, TTS fallback."""

from __future__ import annotations

from navigator.voice.conversation_language import (
    ConversationLanguage,
    observe_user_utterance,
    tts_language,
)


def test_devanagari_is_hindi():
    snap = ConversationLanguage()
    out = observe_user_utterance("मुझे डैशबोर्ड दिखाओ", snap)
    assert out.user_language == "hi"
    assert out.narration_language == "hi"
    assert out.confidence >= 0.8
    assert out.source == "script"


def test_english_sentence_stays_english():
    snap = ConversationLanguage()
    out = observe_user_utterance("show me how the dashboard works", snap)
    assert out.user_language == "en"
    assert out.narration_language == "en"


def test_full_english_sentence_switches_from_hindi():
    snap = ConversationLanguage(user_language="hi", narration_language="hi", confidence=0.9)
    out = observe_user_utterance("show me how the dashboard works please", snap)
    assert out.user_language == "en"
    assert out.narration_language == "en"


def test_mixed_code_switch_does_not_flip():
    snap = ConversationLanguage(user_language="en", narration_language="en")
    out = observe_user_utterance("Can you show me the pricing वाला section?", snap)
    assert out.narration_language == "en"
    assert out.source == "mixed"


def test_short_backchannel_does_not_flip():
    snap = ConversationLanguage(user_language="en", narration_language="en", confidence=0.9)
    out = observe_user_utterance("haan", snap)
    assert out.narration_language == "en"


def test_explicit_switch_is_immediate():
    snap = ConversationLanguage()
    out = observe_user_utterance("talk to me in hindi", snap)
    assert out.user_language == "hi"
    assert out.narration_language == "hi"
    assert out.source == "switch"


def test_noisy_single_hindi_line_needs_streak():
    snap = ConversationLanguage()
    first = observe_user_utterance("दिखाओ", snap)
    assert first.narration_language == "en"
    assert first.pending_language == "hi"
    second = observe_user_utterance("यह कैसे काम करता है बताओ", first)
    assert second.narration_language == "hi"


def test_echo_does_not_change_language():
    snap = ConversationLanguage(user_language="en", narration_language="en")
    out = observe_user_utterance(
        "अब मैं आपको डैशबोर्ड दिखाती हूँ",
        snap,
        is_echo=True,
    )
    assert out.narration_language == "en"
    assert out.source == "echo_ignored"


def test_bot_speaking_is_not_user_evidence():
    snap = ConversationLanguage(user_language="hi", narration_language="hi")
    out = observe_user_utterance(
        "Now let me show you the dashboard",
        snap,
        bot_speaking=True,
    )
    assert out.narration_language == "hi"
    assert out.source == "echo_ignored"


def test_phrasing_prompt_names_user_language():
    from navigator.agent.phrasing import build_prompt

    prompt = build_prompt(
        intent="answer",
        utterance="यह कैसे काम करता है",
        spoken_language="hi",
    )
    assert "User language: hi" in prompt
    assert "Do not automatically translate into English" in prompt


def test_print_speaker_records_language():
    from navigator.voice.tts import PrintSpeaker

    sp = PrintSpeaker()
    sp.say("नमस्ते", language="hi")
    assert sp.said == ["नमस्ते"]
    assert sp.languages == ["hi"]


def test_demo_view_exposes_speech_fields():
    from navigator.app.main import DemoView
    from uuid import uuid4

    view = DemoView(
        demo_id=uuid4(),
        product_id="p",
        revision=1,
        session_id=uuid4(),
        origin="dashboard_test",
        status="running",
        page_id="home",
        actions=0,
        failures=0,
        language="hi",
        language_code="hi",
        language_confidence=0.9,
        current_narration="अब डैशबोर्ड",
        speech_status="speaking",
    )
    dumped = view.model_dump()
    assert dumped["language"] == "hi"
    assert dumped["current_narration"] == "अब डैशबोर्ड"
    assert dumped["speech_status"] == "speaking"


def test_publish_speech_calls_hook():
    from navigator.voice.conversation_language import (
        ConversationLanguage,
        publish_speech,
    )

    seen: list[dict] = []

    class Deps:
        spoken_language = "hi"
        conversation_language = ConversationLanguage(
            user_language="hi", narration_language="hi", confidence=0.91
        )
        on_speech = seen.append

    out = publish_speech(Deps(), status="speaking", narration="hello")
    assert seen[0]["language"] == "hi"
    assert seen[0]["current_narration"] == "hello"
    assert out["speech_status"] == "speaking"


def test_spanish_falls_back_for_tts():
    code, fallback = tts_language("es")
    assert code == "en"
    assert fallback is True
    snap = ConversationLanguage()
    out = observe_user_utterance("¿Puedes mostrarme el panel principal por favor?", snap)
    assert out.user_language == "es"
    assert out.narration_language == "en"
    assert out.tts_fallback is True


def test_sync_heard_skips_bot_playback():
    from navigator.voice.conversation_language import sync_heard_language

    class Live:
        speaking = True
        playback_phase = "playing"

    class Deps:
        spoken_language = "en"
        conversation_language = ConversationLanguage()
        live_agent = Live()
        extra_languages = ("hi",)
        speaker = None

    patch = sync_heard_language(Deps(), "अब मैं आपको डैशबोर्ड दिखाती हूँ")
    assert patch["user_language"] == "en"
    assert patch["language_source"] == "echo_ignored"


def test_demo_handle_public_includes_speech_fields():
    from uuid import uuid4

    from navigator.app.runner import DemoHandle

    handle = DemoHandle(
        demo_id=uuid4(),
        product_id="acme",
        revision=1,
        session_id=uuid4(),
        origin="dashboard_test",
        status="running",
        language="hi",
        language_code="hi",
        language_confidence=0.91,
        current_narration="अब डैशबोर्ड",
        speech_status="speaking",
    )
    pub = handle.public()
    assert pub["language"] == "hi"
    assert pub["current_narration"] == "अब डैशबोर्ड"
    assert pub["speech_status"] == "speaking"
