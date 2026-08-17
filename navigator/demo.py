"""Phase 1 entrypoint: a scripted demo, no LLM, no meeting.

    python -m navigator.demo

Opens a real browser window, replays one flow out of the site graph, verifies each
step's postcondition against the DOM, prints narration, and writes every action
to the ActionLog.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from uuid import uuid4

from navigator.agent.graph import build_graph
from navigator.agent.state import CallDeps, initial_state
from navigator.knowledge.site_graph import load_site_graph
from navigator.logs.store import ActionLog
from navigator.automation.browser.session import browser_page
from navigator.core.settings import settings
from navigator.voice.tts import PrintSpeaker


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--site-graph", default=str(settings.site_graph))
    ap.add_argument("--page", default="inbox")
    ap.add_argument("--flow", default="send_test_message")
    ap.add_argument("--db", default=str(settings.db_path))
    ap.add_argument("--product-id", default="default")
    ap.add_argument("--archive-dir", default="archives")
    ap.add_argument(
        "--headless", action="store_true", help="override NAVIGATOR_HEADFUL"
    )
    ap.add_argument("--mute", action="store_true", help="print narration (always on)")
    ap.add_argument(
        "--slow-mo", type=int, default=400, help="ms between actions, for watching"
    )
    args = ap.parse_args()

    graph_cfg = load_site_graph(args.site_graph)
    print(f"[demo] site graph v{graph_cfg.version} ({graph_cfg.site}) loaded")
    print(f"[demo] flow: {args.page}/{args.flow}")

    speaker = PrintSpeaker()
    session_id = uuid4()
    headful = settings.headful and not args.headless

    with ActionLog(args.db) as log, browser_page(headful, args.slow_mo) as page:
        deps = CallDeps(
            graph=graph_cfg,
            page=page,
            log=log,
            speaker=speaker,
            scripted_flow=(args.page, args.flow),
            product_id=args.product_id,
            archive_dir=Path(args.archive_dir),
        )
        app = build_graph(deps)
        final = app.invoke(initial_state(session_id, args.page))
        _report(log, session_id, args.product_id, final)

    return 1 if final.get("failures") else 0


def _report(log: ActionLog, session_id, product_id: str, final: dict) -> None:
    entries = log.entries(session_id, product_id=product_id)
    print(f"\n[demo] action log for session {session_id}")
    print(f"  {'tool':<14} {'page':<8} {'ok':<4} {'passed':<7} actual")
    for e in entries:
        passed = "-" if e.verify is None else str(e.verify.passed)
        print(
            f"  {e.tool_call.tool:<14} {e.page:<8} {str(e.actual_result.ok):<4} "
            f"{passed:<7} {(e.verify.actual if e.verify else e.actual_result.detail)[:60]}"
        )

    failures = [e for e in entries if e.failed]
    verdict = "FAILED" if failures else "PASSED"
    print(f"\n[demo] {verdict}: {len(entries)} action(s), {len(failures)} failure(s)")


if __name__ == "__main__":
    raise SystemExit(main())
