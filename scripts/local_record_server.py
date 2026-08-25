#!/usr/bin/env python3
"""Headed Playwright server on THIS laptop — VPS record opens Chrome here.

Production: leave NAVIGATOR_RECORD_BROWSER_WS empty (browser stays on API host).
Lab: Platform sets NAVIGATOR_RECORD_BROWSER_WS + NAVIGATOR_RECORD_WS_PATH.

  .venv/bin/python scripts/local_record_server.py

Keep running, then Record in /client. Ctrl+C stops.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = Path.home() / ".cursor" / "navigator-record.env"
PORT = 3333
_JS = """\
const { chromium } = require(process.env.NAV_PW_DRIVER);
(async () => {
  const token = process.env.NAVIGATOR_RECORD_WS_PATH;
  const base = {
    headless: false,
    host: "0.0.0.0",
    port: Number(process.env.NAV_RECORD_PORT || 3333),
    wsPath: token,
    args: ["--start-maximized"],
  };
  let server;
  try {
    server = await chromium.launchServer({ ...base, channel: "chrome" });
    console.log("[record-local] Google Chrome (not Chromium)");
  } catch (err) {
    console.error("[record-local] chrome channel failed, Chromium fallback:", err.message);
    server = await chromium.launchServer(base);
  }
  console.log("[record-local] ws", server.wsEndpoint());
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
"""


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


def _lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.168.1.196", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main() -> int:
    os.chdir(ROOT)
    _load_dotenv(ENV_FILE)
    _load_dotenv(ROOT / ".env")
    token = (os.environ.get("NAVIGATOR_RECORD_WS_PATH") or "").strip().lstrip("/")
    if not token:
        print(
            "Set NAVIGATOR_RECORD_WS_PATH (same token as the API host .env).\n"
            f"Example file: {ENV_FILE}",
            file=sys.stderr,
        )
        return 2

    import playwright

    driver_root = Path(playwright.__file__).resolve().parent / "driver"
    node = driver_root / "node"
    pkg = driver_root / "package"
    if not node.is_file() or not pkg.is_dir():
        print("Playwright driver missing — pip install playwright && playwright install chromium", file=sys.stderr)
        return 2

    ip = _lan_ip()
    print(
        f"[record-local] headed Chromium on 0.0.0.0:{PORT}  LAN={ip}\n"
        f"[record-local] API host .env:\n"
        f"  NAVIGATOR_RECORD_BROWSER_WS=ws://{ip}:{PORT}\n"
        f"  NAVIGATOR_RECORD_WS_PATH={token}\n"
        f"[record-local] Record in /client — window opens here.",
        flush=True,
    )
    env = os.environ.copy()
    env["NAV_PW_DRIVER"] = str(pkg)
    env["NAVIGATOR_RECORD_WS_PATH"] = token
    env["NAV_RECORD_PORT"] = str(PORT)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tmp:
        tmp.write(_JS)
        js_path = tmp.name
    try:
        return subprocess.call([str(node), js_path], env=env, cwd=str(pkg))
    finally:
        Path(js_path).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
