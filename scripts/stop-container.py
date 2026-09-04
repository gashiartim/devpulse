#!/usr/bin/env python3
"""Safely stop one verified Docker container behind a selected published port."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from typing import Any


CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{64}$")


def inspect_container(container_id: str) -> dict[str, Any] | None:
    try:
        result = subprocess.run(
            ["docker", "inspect", container_id],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or len(result.stdout) > 2_000_000:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        return None
    return payload[0]


def published_ports(container: dict[str, Any]) -> set[int]:
    ports: set[int] = set()
    network = container.get("NetworkSettings", {})
    mappings = network.get("Ports", {}) if isinstance(network, dict) else {}
    if not isinstance(mappings, dict):
        return ports
    for bindings in mappings.values():
        if not isinstance(bindings, list):
            continue
        for binding in bindings:
            if not isinstance(binding, dict):
                continue
            try:
                port = int(binding.get("HostPort", 0))
            except (TypeError, ValueError):
                continue
            if 1 <= port <= 65535:
                ports.add(port)
    return ports


def stop_container(container_id: str) -> bool:
    try:
        result = subprocess.run(
            ["docker", "stop", "--time", "10", container_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15.0,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def emit(ok: bool, reason: str, **details: object) -> int:
    print(json.dumps({"ok": ok, "reason": reason, **details}, separators=(",", ":")))
    return 0 if ok else 2


def main() -> int:
    container_id = str(sys.argv[1]).lower() if len(sys.argv) > 1 else ""
    try:
        expected_port = int(sys.argv[2])
    except (IndexError, ValueError):
        expected_port = 0
    if not CONTAINER_ID_RE.fullmatch(container_id) or not 1 <= expected_port <= 65535:
        return emit(False, "invalid-container")

    container = inspect_container(container_id)
    if container is None:
        return emit(False, "container-gone")
    if str(container.get("Id", "")).lower() != container_id:
        return emit(False, "container-changed")
    state = container.get("State", {})
    if not isinstance(state, dict) or state.get("Running") is not True:
        return emit(False, "container-not-running")
    if expected_port not in published_ports(container):
        return emit(False, "listener-changed")
    if not stop_container(container_id):
        return emit(False, "stop-failed")
    return emit(True, "stopped", containerId=container_id, port=expected_port)


if __name__ == "__main__":
    raise SystemExit(main())
