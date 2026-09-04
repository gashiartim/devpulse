#!/usr/bin/env python3
"""Safely SIGTERM one verified current-user process."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
from pathlib import Path


PID_RE = re.compile(r"\bpid=(\d+)\b")


def start_time(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    closing = raw.rfind(")")
    if closing < 0:
        return ""
    fields = raw[closing + 2:].split()
    return fields[19] if len(fields) > 19 else ""


def process_owns_listener(pid: int, port: int) -> bool:
    """Recheck socket ownership immediately before signaling the process."""
    try:
        result = subprocess.run(
            ["ss", "-H", "-ltnp", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return pid in {int(value) for value in PID_RE.findall(result.stdout)}


def main() -> int:
    try:
        pid = int(sys.argv[1])
    except (IndexError, ValueError):
        print(json.dumps({"ok": False, "reason": "invalid-pid"}))
        return 2
    expected = str(sys.argv[2]) if len(sys.argv) > 2 else ""
    try:
        port = int(sys.argv[3])
    except (IndexError, ValueError):
        port = 0
    if pid <= 1 or not expected.isdigit() or not 1 <= port <= 65535:
        print(json.dumps({"ok": False, "reason": "invalid-pid"}))
        return 2
    try:
        proc = Path(f"/proc/{pid}")
        if proc.stat().st_uid != os.getuid():
            raise PermissionError
        actual = start_time(pid)
        if not actual or actual != expected:
            print(json.dumps({"ok": False, "reason": "process-changed"}))
            return 3
        if not process_owns_listener(pid, port):
            print(json.dumps({"ok": False, "reason": "listener-changed"}))
            return 6
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print(json.dumps({"ok": False, "reason": "process-gone"}))
        return 4
    except (OSError, PermissionError):
        print(json.dumps({"ok": False, "reason": "not-owned"}))
        return 5
    print(json.dumps({"ok": True, "pid": pid, "port": port}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
