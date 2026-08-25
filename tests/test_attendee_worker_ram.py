"""Attendee worker must not spawn one Celery child per CPU (eats ~1.5GiB idle)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_attendee_worker_capped_concurrency():
    text = (ROOT / "docker" / "attendee-local.docker-compose.yaml").read_text()
    assert "--concurrency=2" in text
    assert "attendee-worker-local" in text
