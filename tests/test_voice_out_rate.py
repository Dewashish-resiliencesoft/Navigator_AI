"""Bot audio must reach Attendee at Gemini's native 24 kHz (never pre-upsampled to 48k).

Attendee returns a tuple for same-rate chunks and crashes send_raw_audio → silent bot.
"""

import base64
import json
import threading
import time

from websockets.sync.client import connect

from navigator.meeting.attendee_ws_patch import patch_upsample
from navigator.meeting.audio_bridge import AudioBridge


def test_bridge_sends_native_rate_and_counts(monkeypatch):
    import navigator.meeting.audio_bridge as ab

    monkeypatch.setattr(ab, "_PATCH_OK", False)
    b = AudioBridge().start()
    got = []
    try:
        def client():
            with connect(b.local_url) as ws:
                end = time.time() + 3
                while time.time() < end and len(got) < 2:
                    try:
                        m = json.loads(ws.recv(timeout=0.5))
                    except TimeoutError:
                        continue
                    if m.get("trigger") == "realtime_audio.bot_output":
                        got.append(m["data"])
        t = threading.Thread(target=client, daemon=True); t.start()
        time.sleep(0.3)
        b.push_outbound_pcm(b"\x01\x00" * 4800, sample_rate=24000)  # 0.2 s
        t.join(timeout=4)
        assert got and {d["sample_rate"] for d in got} == {24000}
        assert sum(len(base64.b64decode(d["chunk"])) for d in got) == 9600
        assert b.pcm_in_bytes == 9600 and b.bytes_sent == 9600 and b.send_errors == 0
    finally:
        b.stop()


def test_attendee_upsample_patch(tmp_path):
    p = tmp_path / "bots" / "bot_controller"
    p.mkdir(parents=True)
    f = p / "realtime_audio_output_manager.py"
    f.write_text("        if sample_rate == self.output_sample_rate:\n            return chunk, sample_rate\n")
    assert patch_upsample(tmp_path).startswith("patched")
    assert "return chunk, sample_rate" not in f.read_text()
    assert patch_upsample(tmp_path) == "already patched"


def test_resample_48k_is_smooth_not_duplicated():
    import numpy as np

    from navigator.meeting.audio_bridge import resample_pcm16

    x = (np.sin(np.arange(2400) / 7.0) * 12000).astype(np.int16)
    y = np.frombuffer(resample_pcm16(x.tobytes(), 24000, 48000), dtype=np.int16)
    assert y.size == 4800
    # interpolated midpoints, not zero-order-hold duplicates
    assert np.mean(y[1::2][:-1] != y[0::2][:-1]) > 0.9
