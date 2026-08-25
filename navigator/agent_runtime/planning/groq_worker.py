"""Async Groq worker: event → human-readable summaries (non-critical path)."""

from __future__ import annotations

import json
import threading
from queue import Empty, Queue
from uuid import UUID

from navigator.agent_runtime.models import AgentEvent, AgentEventKind
from navigator.core.settings import settings

_ZERO_SESSION = UUID(int=0)


class GroqEventWorker:
    """Background enrichment. Runtime works if this never starts."""

    def __init__(self) -> None:
        self._queue: Queue[AgentEvent] = Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.summaries: list[str] = []

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="groq-event-worker", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._queue.put_nowait(
            AgentEvent(
                event=AgentEventKind.SESSION_ENDED,
                session_id=_ZERO_SESSION,
            )
        )

    def enqueue(self, event: AgentEvent) -> None:
        if not settings.groq_api_key and not settings.groq_api_keys:
            return
        self._queue.put_nowait(event)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=0.5)
            except Empty:
                continue
            if event.event == AgentEventKind.SESSION_ENDED:
                break
            try:
                summary = self._summarize(event)
                if summary:
                    self.summaries.append(summary)
                    print(f"[runtime][groq] {summary}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"[runtime][groq] enrich failed: {exc}", flush=True)

    def _summarize(self, event: AgentEvent) -> str:
        from navigator.core.groq_client import chat_completions_create

        if event.event in {
            AgentEventKind.ACTION_STARTED,
            AgentEventKind.ACTION_COMPLETED,
            AgentEventKind.VERIFICATION_PASSED,
            AgentEventKind.VERIFICATION_FAILED,
            AgentEventKind.USER_INTERRUPTED,
            AgentEventKind.TASK_COMPLETED,
        }:
            payload = json.dumps(event.model_dump(mode="json"), default=str)[:2000]
            resp = chat_completions_create(
                settings.groq_api_key or None,
                purpose="runtime_event",
                model=settings.brain_phrasing_model,
                messages=[
                    {
                        "role": "system",
                        "content": "One concise log line for operators. No markdown.",
                    },
                    {"role": "user", "content": payload},
                ],
                temperature=0,
                max_tokens=120,
            )
            return (resp.choices[0].message.content or "").strip()
        return ""

