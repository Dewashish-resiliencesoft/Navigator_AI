"""Per-product agent settings API and gender-aware speech rules."""

from __future__ import annotations

from fastapi.testclient import TestClient

from navigator.app import main as app_module
from navigator.app.auth_store import AuthStore
from navigator.app.registry import Registry
from navigator.app.runner import DemoRunner
from navigator.agent.speech_persona import speech_rules
from navigator.core.agent_settings import merge_agent_settings
from navigator.logs.store import ActionLog


def _app(tmp_path):
    registry = Registry(tmp_path / "registry.db")
    log = ActionLog(tmp_path / "actions.db")
    auth = AuthStore(tmp_path / "auth.db")
    runner = DemoRunner(str(tmp_path / "actions.db"), redis_url=None)

    app_module.app.dependency_overrides[app_module.get_registry] = lambda: registry
    app_module.app.dependency_overrides[app_module.get_log] = lambda: log
    app_module.app.dependency_overrides[app_module.get_auth_store] = lambda: auth
    app_module.app.dependency_overrides[app_module.get_runner] = lambda: runner

    client = TestClient(app_module.app)
    return client, registry, auth


def _cleanup():
    app_module.app.dependency_overrides.clear()


def _signup(client: TestClient) -> tuple[str, str]:
    r = client.post(
        "/v1/auth/signup",
        json={
            "company_name": "Agent Settings Co",
            "email": "agent@example.com",
            "password": "secretpass",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["access_token"], body["product_id"]


def test_agent_settings_defaults_and_patch(tmp_path):
    client, registry, _auth = _app(tmp_path)
    try:
        token, product_id = _signup(client)
        headers = {"Host": "localhost", "Authorization": f"Bearer {token}"}

        got = client.get("/client/api/agent-settings", headers=headers)
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["default_language"] == "en"
        assert body["agent_gender"] == "female"
        assert body["extra_languages"] == ["hi"]
        assert body["has_gemini_api_key"] is False
        assert "tts_provider" not in body
        assert "has_fish_api_key" not in body

        put = client.put(
            "/client/api/agent-settings",
            headers=headers,
            json={
                "default_language": "hi",
                "agent_gender": "male",
                "agent_name": "Alex",
                "extra_languages": ["en", "hi"],
            },
        )
        assert put.status_code == 200, put.text
        saved = put.json()
        assert saved["ok"] is True
        assert saved["default_language"] == "hi"
        assert saved["agent_gender"] == "male"
        assert saved["agent_name"] == "Alex"

        stored = registry.get_agent_settings(product_id)
        assert stored.default_language == "hi"
        assert stored.agent_gender == "male"
        assert stored.effective_gemini_voice() == "Charon"
    finally:
        _cleanup()
        registry.close()


def test_hindi_speech_rules_match_gender():
    female = speech_rules(spoken_language="hi", agent_gender="female")
    male = speech_rules(spoken_language="hi", agent_gender="male")
    assert "feminine" in female.lower() or "रही" in female
    assert "masculine" in male.lower() or "रहा" in male
    assert "Never use masculine" in female


def test_merge_agent_settings_validates():
    s = merge_agent_settings('{"default_language":"hi","extra_languages":["en"]}')
    assert s.default_language == "hi"
    assert s.extra_languages == ["en"]
    assert "tts_provider" not in s.model_dump()


def test_merge_ignores_legacy_tts_provider():
    s = merge_agent_settings('{"tts_provider":"fish","default_language":"en"}')
    assert "tts_provider" not in s.model_dump()
