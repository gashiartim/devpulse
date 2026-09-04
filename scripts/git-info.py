#!/usr/bin/env python3
"""Read compact, local Git metadata for a list of project directories."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def git_status(path: str) -> dict[str, object]:
    if not path or not os.path.isabs(path):
        return {"cwd": path, "available": False}
    try:
        candidate = Path(path).resolve()
    except OSError:
        return {"cwd": path, "available": False}
    if not candidate.is_dir():
        return {"cwd": path, "available": False}
    try:
        result = subprocess.run(
            ["git", "-C", str(candidate), "status", "--porcelain=v1", "--branch", "--untracked-files=no"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            encoding="utf-8", errors="replace", timeout=1.5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {"cwd": path, "available": False}
    if result.returncode != 0:
        return {"cwd": path, "available": False}
    lines = result.stdout.splitlines()
    branch = ""
    if lines and lines[0].startswith("## "):
        branch = lines[0][3:].split("...", 1)[0].strip()
        if branch == "HEAD (no branch)":
            branch = "detached"
    dirty = len(lines) > 1
    return {"cwd": path, "available": bool(branch), "branch": branch, "dirty": dirty}


def main() -> int:
    try:
        paths = json.loads(sys.argv[1]) if len(sys.argv) > 1 else []
    except (json.JSONDecodeError, TypeError):
        paths = []
    if not isinstance(paths, list):
        paths = []
    rows = {str(path): git_status(str(path)) for path in paths if isinstance(path, str)}
    json.dump(rows, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
