"""Regression: bursty Gemini-like audio through Attendee's real output manager
and the real bot-page player (virtual clock, throttled timers). Needs the
attendee worker container and node; skipped otherwise."""
import json, shutil, subprocess
from pathlib import Path
import pytest

W = "attendee-attendee-worker-local-1"
FIX = Path(__file__).parent / "fixtures" / "audio_loopback"
ATT = Path("/home/akash-vaidya/projects/attendee")


def _ok() -> bool:
    if not shutil.which("docker") or not shutil.which("node"):
        return False
    r = subprocess.run(["docker", "inspect", W], capture_output=True)
    return r.returncode == 0


@pytest.mark.skipif(not _ok(), reason="attendee worker / node not available")
def test_zero_gaps_zero_loss(tmp_path):
    subprocess.run(["docker", "cp", str(FIX), f"{W}:/tmp/navaudio_t"], check=True)
    out = subprocess.run(["docker", "exec", W, "python", "/tmp/navaudio_t/drive.py",
                          "/attendee/bots/bot_controller/realtime_audio_output_manager.py"],
                         capture_output=True, text=True, check=True).stdout
    i, d = [int(x) for x in out.split()[1::2]]
    assert i == d, f"audio lost: in={i} delivered={d}"
    deliv = tmp_path / "deliv.json"
    subprocess.run(["docker", "cp", f"{W}:/tmp/deliv.json", str(deliv)], check=True)
    for throttle in ("4", "1000"):
        r = subprocess.run(["node", str(FIX / "replay.js"),
                            str(ATT / "bots/web_bot_adapter/shared_chromedriver_payload.js"),
                            str(deliv), throttle], capture_output=True, text=True, check=True)
        m = json.loads(r.stderr.strip().splitlines()[-1])
        assert m["midspeech_gaps"] == 0 and m["reported_underruns"] == 0, m
