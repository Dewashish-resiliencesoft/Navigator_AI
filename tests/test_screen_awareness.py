import asyncio
from types import SimpleNamespace

from navigator.agent.nodes.executing import step_target_reached
from navigator.meeting import ui_jobs
from navigator.voice.live_agent import LiveAgent, looks_foreign_noise

LOGIN = """<main><h2>Welcome back</h2>
<div class=seg><button class="active">Email</button><button>WhatsApp</button><button>QR</button></div>
<label>Email <input type=email></label><label>Password <input type=password></label>
<button>Sign in</button><a href="/forgot">Forgot password?</a></main>
<nav><a href="/">Home</a></nav>"""


def _page_summary(html):
    from playwright.sync_api import sync_playwright

    asyncio.events._set_running_loop(None)
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(); pg.set_content(html)
        d = ui_jobs.screen_summary(pg)
        res = ui_jobs.dispatch(pg, "list_screen_controls", {}, None)
        b.close()
    return d, res


def test_summary_lists_unselected_tabs_buttons_fields_links():
    d, res = _page_summary(LOGIN)
    assert "Email (selected)" in d["tabs"] and "WhatsApp" in d["tabs"] and "QR" in d["tabs"]
    assert any("Password [password]" in f for f in d["inputs"])
    assert "Forgot password?" in d["links"] and "Home" in d["menu"]
    assert "QR" in res["screen"] and res["ok"]


def test_screen_context_injected_once_per_change():
    a = LiveAgent.__new__(LiveAgent)
    import queue, time
    a._cmds = queue.Queue(); a._in_say = False
    from navigator.agent.nodes import executing as _ex; _ex.STEP_URL = "https://x/login/"
    ui_jobs.SCREEN_SUMMARY.update(text="Screen /login/\nTabs/options: Email (selected), WhatsApp, QR", hash="h1",
                                  at=time.monotonic())
    a._inject_screen(); a._inject_screen()
    assert a._cmds.qsize() == 1 and "QR" in a._cmds.get_nowait().text
    ui_jobs.SCREEN_SUMMARY.update(hash="h2"); a._in_say = True
    a._inject_screen()
    assert a._cmds.empty()  # never mid scripted line


def test_resume_continues_from_current_screen_not_landing():
    deps = SimpleNamespace(page=SimpleNamespace(url="https://x.test/login/"), graph=None)
    assert step_target_reached(deps, SimpleNamespace(tool="click_element", selector="login"))
    assert not step_target_reached(deps, SimpleNamespace(tool="click_element", selector="sign_in"))
    deps.page.url = "https://x.test/"
    assert not step_target_reached(deps, SimpleNamespace(tool="click_element", selector="login"))


def test_foreign_short_transcript_is_noise():
    assert looks_foreign_noise("O que é o que é login?")
    assert not looks_foreign_noise("What is QR login?")
    assert not looks_foreign_noise("कैसे अलग उसका हो गया है?")


def test_prompt_forbids_asking_prospect_to_operate_and_claiming_absence():
    t = ui_jobs.LIVE_TOOL_INSTRUCTION
    assert "Never say something is not on screen" in t and "let me know when done" in t


def test_closest_control_maps_spoken_label_to_visible_tab():
    from navigator.meeting.task_runner import closest_control
    ctl = ["Business owner", "Email", "WhatsApp", "QR", "Sign in", "Forgot password?"]
    assert closest_control("Scan QR code", ctl) == "QR"
    assert closest_control("Can you explain QR login?", ["WhatsApp", "QR"]) == "QR"
    assert closest_control("WhatsApp OTP login", ctl) == "WhatsApp"
    assert closest_control("Billing", ctl) == ""
