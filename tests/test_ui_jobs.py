import threading, time
from navigator.meeting import ui_jobs


def test_tool_call_runs_only_on_owner_thread(monkeypatch):
    ui = ui_jobs.UiJobs()
    ui.bind(page=object())
    monkeypatch.setattr(ui_jobs, "show_feature", lambda page, t, pf=None: {"ok": True, "target": t})
    monkeypatch.setattr(ui, "_spin", lambda: None)
    out = {}
    th = threading.Thread(target=lambda: out.update(ui.handle_tool("show_feature", {"target": "Phonebook"})))
    th.start()
    time.sleep(0.2)
    assert not out  # Live thread blocks until the demo thread pumps
    assert ui.hold_active()  # scripted flow paused meanwhile
    deadline = time.time() + 2
    while th.is_alive() and time.time() < deadline:
        ui.pump(); time.sleep(0.05)
    assert out == {"ok": True, "target": "Phonebook"}


def test_destructive_targets_detected():
    assert ui_jobs.DESTRUCTIVE.search("Logout") and ui_jobs.DESTRUCTIVE.search("Sign out")
    assert not ui_jobs.DESTRUCTIVE.search("Phonebook")
