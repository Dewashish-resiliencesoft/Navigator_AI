"""Video capture page: 1920×1080 @ 30fps, no jpeg poll cap, voice on the page."""

from __future__ import annotations

import socket
import threading
import time

from navigator.core.settings import settings
from navigator.meeting.relay import (
    offer_voice_to_capture_page,
    screencast_every_nth,
    start_relay,
)
from navigator.meeting.screen_page import (
    capture_pump_ms,
    delivered_capture_fps,
    encode_server_frame,
    pop_client_frame,
    view_page_html,
    ws_accept,
)


def test_delivered_fps_follows_override_without_a_lower_band(monkeypatch):
    monkeypatch.setattr(settings, "screenshare_fps", 30)
    monkeypatch.setattr(settings, "attendee_max_fps", 60)
    monkeypatch.setattr(settings, "screenshare_mode", "video")
    assert delivered_capture_fps() == 30
    assert screencast_every_nth() == 2  # compositor ~60 / 30
    assert screencast_every_nth(20) == 3


def test_sixty_fps_is_not_collapsed_to_the_jpeg_band(monkeypatch):
    monkeypatch.setattr(settings, "screenshare_fps", 60)
    monkeypatch.setattr(settings, "attendee_max_fps", 60)
    monkeypatch.setattr(settings, "screenshare_mode", "video")
    assert delivered_capture_fps() == 60
    assert screencast_every_nth() == 1
    assert capture_pump_ms() == 8
    assert "FPS=60" in view_page_html(jpeg=False)
    # 40 must not round down to every 2nd frame (~30fps).
    monkeypatch.setattr(settings, "screenshare_fps", 40)
    assert delivered_capture_fps() == 40
    assert screencast_every_nth() == 1
    assert "FPS=40" in view_page_html(jpeg=False)
    monkeypatch.setattr(settings, "screenshare_fps", 30)
    assert capture_pump_ms() == 16
    monkeypatch.setattr(settings, "screenshare_fps", 0)
    monkeypatch.setattr(settings, "meeting_url", "")
    assert delivered_capture_fps() == 60  # 0 means auto; no URL is local
    monkeypatch.setattr(settings, "screenshare_fps", 120)
    assert delivered_capture_fps() == 60


def test_video_page_is_1080p30_and_jpeg_page_still_polls(monkeypatch):
    monkeypatch.setattr(settings, "viewport_width", 1920)
    monkeypatch.setattr(settings, "viewport_height", 1080)
    monkeypatch.setattr(settings, "screenshare_fps", 30)
    monkeypatch.setattr(settings, "attendee_max_fps", 60)
    video = view_page_html(jpeg=False)
    assert "captureStream(FPS)" in video and "FPS=30" in video
    assert "width=1920" in video and "height=1080" in video
    assert "/screen.ws" in video
    assert "frame.jpg" not in video
    jpeg = view_page_html(jpeg=True, frame_ms=50, mjpeg=False)
    assert "frame.jpg" in jpeg
    assert "setInterval(pullFrame, 50)" in jpeg
    assert "captureStream" not in jpeg


def test_websocket_payloads_mark_frame_and_pcm():
    frame = encode_server_frame(2, b"F" + b"jpeg")
    assert frame[0] == 0x82
    buf = bytearray(frame)
    opcode, payload = pop_client_frame(buf)
    assert opcode == 2 and payload == b"Fjpeg" and buf == bytearray()
    pcm = b"A" + (16000).to_bytes(4, "little") + b"\x01\x00"
    masked = bytearray(b"\x82\x87" + b"\x01\x02\x03\x04")
    raw = pcm
    masked.extend(bytes(b ^ b"\x01\x02\x03\x04"[i % 4] for i, b in enumerate(raw)))
    opcode, payload = pop_client_frame(masked)
    assert opcode == 2 and payload == pcm


def test_offer_voice_only_when_capture_page_is_connected(monkeypatch):
    monkeypatch.setattr(settings, "screenshare_mode", "video")
    offer_voice_to_capture_page._logged = False  # type: ignore[attr-defined]
    relay = start_relay()
    try:
        assert offer_voice_to_capture_page(b"\x00\x01", 16000) is False
        client = relay.register_screen_client()
        assert offer_voice_to_capture_page(b"\x01\x02\x03\x04", 16000) is True
        _frame, _seq, audio, clear = relay.drain_screen_client(client)
        assert clear is False
        assert audio and audio[0][:1] == b"A"
        assert int.from_bytes(audio[0][1:5], "little") == 16000
        relay.clear_pcm()
        _frame, _seq, audio, clear = relay.drain_screen_client(client)
        assert clear is True and audio == []
        monkeypatch.setattr(settings, "screenshare_mode", "jpeg")
        assert offer_voice_to_capture_page(b"\x01\x02", 16000) is False
    finally:
        relay.stop()


def _read_http(sock: socket.socket) -> tuple[bytes, bytearray]:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        assert chunk
        data += chunk
    head, rest = data.split(b"\r\n\r\n", 1)
    return head, bytearray(rest)


def _read_ws(buf: bytearray, sock: socket.socket, timeout: float = 2.0):
    sock.settimeout(0.3)
    end = time.time() + timeout
    while time.time() < end:
        item = pop_client_frame(buf)
        if item is not None:
            return item
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            continue
        if not chunk:
            break
        buf.extend(chunk)
    raise AssertionError(f"no websocket frame, buf={bytes(buf[:40])!r}")


def test_screen_socket_pushes_latest_frame_and_pcm():
    relay = start_relay()
    sock = socket.create_connection(("127.0.0.1", relay.port), timeout=2)
    try:
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        sock.sendall(
            (
                f"GET /screen.ws HTTP/1.1\r\nHost: 127.0.0.1:{relay.port}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        head, buf = _read_http(sock)
        assert b" 101 " in head.split(b"\r\n", 1)[0]
        assert ws_accept(key).encode() in head
        time.sleep(0.05)
        relay.set_frame(b"OLD")
        relay.set_frame(b"NEW")
        relay.push_pcm(b"\x10\x00", 8000)
        kinds = {}
        deadline = time.time() + 2
        while time.time() < deadline and {"F", "A"} - kinds.keys():
            _opcode, payload = _read_ws(buf, sock, timeout=max(0.2, deadline - time.time()))
            kinds[chr(payload[0])] = payload
        assert kinds["F"] == b"FNEW"
        assert kinds["A"].startswith(b"A")
        assert int.from_bytes(kinds["A"][1:5], "little") == 8000
    finally:
        sock.close()
        relay.stop()


def test_relay_keeps_a_60fps_setting(monkeypatch):
    """A 200ms read between frames would deliver ~5fps. 60 must stay near 60."""
    monkeypatch.setattr(settings, "screenshare_fps", 60)
    monkeypatch.setattr(settings, "screenshare_mode", "video")
    relay = start_relay()
    sock = socket.create_connection(("127.0.0.1", relay.port), timeout=2)
    try:
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        sock.sendall(
            (
                f"GET /screen.ws HTTP/1.1\r\nHost: 127.0.0.1:{relay.port}\r\n"
                "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        head, buf = _read_http(sock)
        assert b" 101 " in head.split(b"\r\n", 1)[0]
        time.sleep(0.05)

        def produce() -> None:
            for i in range(48):
                relay.set_frame(bytes((i % 250) + 1))
                time.sleep(1 / 60)

        threading.Thread(target=produce, daemon=True).start()
        count = 0
        first = None
        wall = time.perf_counter()
        while count < 30 and time.perf_counter() - wall < 2.0:
            _opcode, payload = _read_ws(buf, sock, timeout=0.4)
            if not payload or payload[:1] != b"F":
                continue
            count += 1
            if first is None:
                first = time.perf_counter()
        assert first is not None and count >= 30
        # 30 frames at a hidden 30fps cap take a full second. 60fps is ~0.5s.
        assert time.perf_counter() - first < 0.85
    finally:
        sock.close()
        relay.stop()
