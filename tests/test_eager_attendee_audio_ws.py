"""Attendee must open the audio WS before the first mixed-audio chunk."""

from __future__ import annotations

from pathlib import Path

from navigator.meeting.attendee_ws_patch import patch

_STUB = '''class BotWebsocketClientManager:
    def __init__(self, mixed_audio_url, per_participant_audio_url, per_participant_video_url, on_message_callback):
        client_by_url = {}
        self._clients = list(client_by_url.values())

    def _ensure_started(self, client):
        client.start()
'''


def test_patch_starts_clients_in_init(tmp_path: Path) -> None:
    path = tmp_path / "bots" / "bot_controller" / "bot_websocket_client_manager.py"
    path.parent.mkdir(parents=True)
    path.write_text(_STUB, encoding="utf-8")
    assert "patched" in patch(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "for _client in self._clients:" in text
    assert "self._ensure_started(_client)" in text
    assert patch(tmp_path) == "already patched"
