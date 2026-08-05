"""Host metrics and service health for the Client dashboard.

Surfaces real psutil data from the machine running Navigator. Copy is
client-facing — no internal package or vendor names in labels.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from typing import Any

import psutil

_BOOT = time.time()


def _gpu_block() -> dict[str, Any]:
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            row = out.stdout.strip().splitlines()[0]
            name, util, mem_used, mem_total = [p.strip() for p in row.split(",")]
            return {
                "active": True,
                "name": name,
                "utilization_percent": float(util),
                "memory_used_mb": float(mem_used),
                "memory_total_mb": float(mem_total),
            }
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return {
        "active": False,
        "name": "",
        "utilization_percent": None,
        "memory_used_mb": None,
        "memory_total_mb": None,
    }


def _friendly_process_name(proc: psutil.Process) -> str:
    name = (proc.name() or "").lower()
    if "chromium" in name or "chrome" in name or "firefox" in name:
        return "Browser worker"
    if "python" in name:
        return "Demo host"
    return proc.name() or "Process"


def _host_processes(max_items: int = 8) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    try:
        root = psutil.Process()
    except psutil.Error:
        return rows
    candidates = [root, *root.children(recursive=True)]
    seen: set[int] = set()
    for proc in candidates:
        if proc.pid in seen:
            continue
        seen.add(proc.pid)
        try:
            rows.append(
                {
                    "name": _friendly_process_name(proc),
                    "status": proc.status(),
                    "cpu": f"{proc.cpu_percent(interval=0.0):.1f}%",
                    "mem": f"{proc.memory_percent():.1f}%",
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if len(rows) >= max_items:
            break
    return rows


def _services(
    *,
    product_id: str,
    runner: Any,
) -> list[dict[str, str]]:
    from navigator.automation.explore.runner import active_session
    from navigator.client.content import recorder_status

    services: list[dict[str, str]] = [
        {"name": "Demo host", "status": "active", "detail": "Running on this device"},
    ]

    demos = runner.list(product_id)
    live = [d for d in demos if d.status in ("starting", "running")]
    if live:
        origin = "test" if live[0].origin == "dashboard_test" else "live"
        services.append(
            {
                "name": "Live demo session",
                "status": "active",
                "detail": f"{len(live)} {origin} demo(s) in progress",
            }
        )
    else:
        services.append(
            {"name": "Live demo session", "status": "idle", "detail": "No demo running"},
        )

    bot_live = any(d.bot_in_meeting for d in live)
    services.append(
        {
            "name": "Meeting assistant",
            "status": "active" if bot_live else "idle",
            "detail": "In meeting" if bot_live else "Standby",
        }
    )

    explore = active_session()
    if explore and explore.product_id == product_id and explore.phase not in ("done", "idle"):
        services.append(
            {
                "name": "Site explorer",
                "status": "active",
                "detail": f"Phase: {explore.phase}",
            }
        )
    else:
        services.append(
            {"name": "Site explorer", "status": "idle", "detail": "Not exploring"},
        )

    rec = recorder_status()
    if rec.get("active"):
        services.append(
            {
                "name": "Flow recorder",
                "status": "active",
                "detail": str(rec.get("phase") or "recording"),
            }
        )
    else:
        services.append(
            {"name": "Flow recorder", "status": "idle", "detail": "Not recording"},
        )

    return services


def _health_checks(*, registry: Any, product_id: str, db_path: str) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = [
        {"name": "Host connection", "ok": True, "detail": socket.gethostname()},
    ]
    try:
        registry.latest_revision(product_id)
        checks.append({"name": "Product config", "ok": True, "detail": "Registry reachable"})
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "Product config", "ok": False, "detail": str(exc)[:120]})

    try:
        import sqlite3

        with sqlite3.connect(db_path, timeout=2) as conn:
            conn.execute("SELECT 1")
        checks.append({"name": "Session log", "ok": True, "detail": "Writable"})
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "Session log", "ok": False, "detail": str(exc)[:120]})

    disk = psutil.disk_usage("/")
    checks.append(
        {
            "name": "Disk space",
            "ok": disk.percent < 92,
            "detail": f"{disk.percent:.0f}% used",
        }
    )
    return checks


def collect_system_health(
    *,
    product_id: str,
    registry: Any,
    runner: Any,
    db_path: str,
) -> dict[str, Any]:
    """Snapshot for GET /client/api/system/health."""
    net = psutil.net_io_counters()
    mem = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=None)

    return {
        "host_label": os.environ.get("NAVIGATOR_HOST_LABEL") or socket.gethostname(),
        "uptime_s": round(time.time() - _BOOT, 1),
        "cpu_percent": cpu,
        "cpu_count": psutil.cpu_count(logical=True) or 1,
        "memory_percent": mem.percent,
        "memory_used_mb": round(mem.used / (1024 * 1024), 1),
        "memory_total_mb": round(mem.total / (1024 * 1024), 1),
        "net_sent_bytes": int(net.bytes_sent),
        "net_recv_bytes": int(net.bytes_recv),
        "gpu": _gpu_block(),
        "services": _services(product_id=product_id, runner=runner),
        "processes": _host_processes(),
        "health": _health_checks(registry=registry, product_id=product_id, db_path=db_path),
    }
