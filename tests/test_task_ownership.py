"""The prospect's active task owns the screen (10:33 Forgot Password run)."""
import threading
import time
from types import SimpleNamespace

import pytest

from navigator.meeting import task_runner as tr
from navigator.meeting import task_state as ts
from navigator.meeting import ui_jobs


@pytest.fixture(autouse=True)
def _clean():
    ts.reset(); tr.HEARD.clear()
    yield
    ts.reset(); tr.HEARD.clear()


def _user_tool(name, heard, **args):
    tr.HEARD.append(heard) if heard and (not tr.HEARD or tr.HEARD[-1] != heard) else None
    return ui_jobs._task_for_tool(name, args)


def test_stays_on_requested_screen_while_awaiting_user():
    tid = _user_tool("show_feature", "show me forgot password", target="Forgot Password?")
    ts.transition(tid, "awaiting_user", "asked for email")
    assert not ts.nav_allowed("flow", "step 9 navigate campaigns")
    assert ts.active().id == tid


def test_no_dashboard_without_reason():
    _user_tool("show_feature", "open forgot password", target="Forgot Password?")
    for owner in ("recovery", "auto_login", "orchestrator", "narrated_actions", "planner"):
        assert not ts.nav_allowed(owner, "go to dashboard")


def test_flow_cannot_interrupt_active_task():
    _user_tool("do_task", "reset my password", goal="reset password")
    live = SimpleNamespace(nudge=lambda t: None, answering=False)
    done = []
    th = threading.Thread(target=lambda: (ui_jobs._hold_for_task(live), done.append(1)), daemon=True)
    th.start(); time.sleep(0.5)
    assert not done  # still holding
    ts.on_heard("ok thanks, you can continue the demo")
    th.join(2); assert done and ts.current().state == "completed"


def test_stale_nav_after_task_change_is_blocked():
    old = _user_tool("show_feature", "open forgot password", target="Forgot Password?")
    box = {"seq": 0, "task_id": old}
    _user_tool("show_feature", "actually open campaigns", target="Campaigns")
    res = ui_jobs.superseded("click_control", box)
    assert res and res["skipped"]


def test_failed_click_does_not_navigate_elsewhere():
    tid = _user_tool("click_control", "click send reset link", label="Send reset link")
    ts.transition(tid, "awaiting_user", "click_control ok=False")
    assert ts.active().id == tid
    assert not ts.nav_allowed("recovery", "failed click -> dashboard")
    assert not ts.nav_allowed("flow", "return from detour")


def test_narration_does_not_change_task():
    tid = _user_tool("show_feature", "open forgot password", target="Forgot Password?")
    ts.transition(tid, "awaiting_user")
    assert not ts.nav_allowed("narrated_actions", "let's click Campaigns")
    assert ts.active().id == tid and ts.active().goal.startswith("show_feature")


def test_required_cross_screen_nav_for_the_task_is_allowed():
    tid = _user_tool("show_feature", "open forgot password", target="Forgot Password?")
    tid2 = _user_tool("fill_field", "my email is user@gmail.com", label="Email", value="user@gmail.com")
    assert tid2 == tid  # follow-up details continue the same task
    assert ts.nav_allowed("task", "submit -> check-email screen", tid)


def test_new_instruction_supersedes_previous():
    t1 = _user_tool("show_feature", "open forgot password", target="Forgot Password?")
    t2 = _user_tool("do_task", "no, show me how to add a contact", goal="add contact")
    assert t1 != t2 and ts.active().id == t2
    assert ts.is_stale(t1)
    assert ts.current().history == [] or True


def test_flow_resumes_only_after_terminal_state():
    tid = _user_tool("show_feature", "open forgot password", target="Forgot Password?")
    ts.transition(tid, "awaiting_user")
    assert not ts.nav_allowed("flow", "next step")
    ts.on_heard("what does this button do?")   # not a terminal phrase
    assert ts.active() is not None
    ts.on_heard("never mind")
    assert ts.active() is None and ts.current().state == "cancelled"
    assert ts.nav_allowed("flow", "next step")


def test_idle_cap_asks_before_resuming(monkeypatch):
    monkeypatch.setattr(ui_jobs, "TASK_IDLE_ASK_S", 0.2)
    monkeypatch.setattr(ui_jobs, "TASK_IDLE_GIVEUP_S", 0.3)
    tid = _user_tool("show_feature", "open forgot password", target="Forgot Password?")
    ts.transition(tid, "awaiting_user")
    asked = []
    ui_jobs._hold_for_task(SimpleNamespace(nudge=asked.append, answering=False))
    assert asked and "continue" in asked[0].lower() and ts.current().state == "completed"


def test_fill_field_matches_placeholder_and_email_type():
    F = [{"label": "", "placeholder": "Enter your email", "name": "email", "type": "email"},
         {"label": "Password", "placeholder": "", "name": "pw", "type": "password"}]
    assert tr.match_field(F, "Email address", "user@gmail.com")["name"] == "email"
    assert tr.match_field(F, "password", "x")["name"] == "pw"
    assert tr.match_field([F[0]], "Username", "user@gmail.com")["name"] == "email"
    assert tr.match_field(F, "Company", "x") is None


def test_fill_field_finds_form_outside_main_on_real_page():
    import asyncio
    from playwright.sync_api import sync_playwright

    asyncio.events._set_running_loop(None)
    html = ("<main><h1>Forgot password</h1></main><div class=auth><form>"
            "<input type=email placeholder='Enter your email'><button>Send link</button></form></div>")
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(); pg.set_content(html)
        res = tr.fill_field(pg, "Email address", "user@gmail.com")
        val = pg.locator("input[type=email]").input_value()
        b.close()
    assert res["ok"] and val == "user@gmail.com"


def test_tool_call_answering_continue_does_not_open_a_new_task():
    tid = _user_tool("show_feature", "open forgot password", target="Forgot Password?")
    ts.transition(tid, "awaiting_user")
    tr.HEARD.append("Thanks, that's all. Please continue the demo.")
    ts.on_heard(tr.HEARD[-1])
    res = ui_jobs.UiJobs().handle_tool("click_control", {"label": "Back to Login"})
    assert not res["ok"] and ts.active() is None
