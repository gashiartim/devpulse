#!/usr/bin/env python3
"""Emit the current user's likely local development servers as JSON.

This is deliberately a single, bounded scan: one ss invocation, one ps
snapshot, then cheap /proc reads for the PIDs ss reported. It never executes a
project command or reads environment files.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


HOME = Path(os.environ.get("HOME", str(Path.home()))).resolve()
UID = os.getuid()
TYPICAL_PORTS = {
    3000, 3001, 3002, 4000, 4200, 4321, 5000, 5173, 5174, 5175,
    5500, 8000, 8001, 8080, 8081, 8787, 8888, 9000, 10000,
}
PROJECT_FILES = (
    "package.json", "pyproject.toml", "requirements.txt", "manage.py",
    "Cargo.toml", "go.mod", "Gemfile", "composer.json",
)
PID_RE = re.compile(r"\bpid=(\d+)\b")


def run_capture(argv: list[str], timeout: float = 2.0) -> str:
    try:
        completed = subprocess.run(
            argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
            check=False,
        )
        return completed.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""


def parse_endpoint(value: str) -> tuple[str, int] | None:
    value = value.strip()
    if value.startswith("["):
        marker = value.rfind("]:")
        if marker < 0:
            return None
        host, port_text = value[1:marker], value[marker + 2:]
    else:
        host, separator, port_text = value.rpartition(":")
        if not separator:
            return None
    try:
        port = int(port_text)
    except ValueError:
        return None
    if not 1 <= port <= 65535:
        return None
    return host or "*", port


def parse_listeners() -> dict[tuple[int, int], set[str]]:
    output = run_capture(["ss", "-H", "-ltnp"])
    listeners: dict[tuple[int, int], set[str]] = {}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 5:
            continue
        endpoint = parse_endpoint(fields[3])
        if endpoint is None:
            continue
        host, port = endpoint
        for match in PID_RE.finditer(line):
            pid = int(match.group(1))
            listeners.setdefault((pid, port), set()).add(host)
    return listeners


def snapshot_processes() -> dict[int, dict[str, Any]]:
    output = run_capture(["ps", "-eo", "pid=,uid=,user=,pcpu=,rss=,comm=,args="])
    processes: dict[int, dict[str, Any]] = {}
    for line in output.splitlines():
        fields = line.strip().split(None, 6)
        if len(fields) < 7:
            continue
        try:
            pid = int(fields[0])
            uid = int(fields[1])
            cpu = float(fields[3].replace(",", "."))
            rss_kb = int(fields[4])
        except (ValueError, IndexError):
            continue
        processes[pid] = {
            "uid": uid,
            "owner": fields[2],
            "cpu": max(0.0, cpu),
            "memoryBytes": max(0, rss_kb) * 1024,
            "comm": fields[5],
            "commandLine": fields[6].strip(),
        }
    return processes


def read_text(path: Path, limit: int = 1_000_000) -> str:
    try:
        with path.open("rb") as stream:
            return stream.read(limit).decode("utf-8", "replace")
    except (OSError, UnicodeError):
        return ""


def process_start_time(pid: int) -> str:
    raw = read_text(Path("/proc") / str(pid) / "stat", 32_768)
    if not raw:
        return ""
    closing = raw.rfind(")")
    if closing < 0:
        return ""
    fields = raw[closing + 2:].split()
    # /proc stat field 22 is index 19 after pid and comm have been removed.
    return fields[19] if len(fields) > 19 else ""


def process_cwd(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return ""


def package_data(root: Path) -> dict[str, Any]:
    package_path = root / "package.json"
    if not package_path.is_file():
        return {}
    try:
        raw = package_path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw[:1_000_000])
        return data if isinstance(data, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def find_project(cwd: str) -> tuple[Path | None, dict[str, Any], bool]:
    if not cwd or not os.path.isabs(cwd):
        return None, {}, False
    try:
        current = Path(cwd).resolve()
    except OSError:
        return None, {}, False
    seen: set[Path] = set()
    while current not in seen and len(seen) < 32:
        seen.add(current)
        package = package_data(current)
        has_git = (current / ".git").exists()
        has_marker = bool(package) or any((current / name).is_file() for name in PROJECT_FILES[1:])
        if package or has_git or has_marker:
            return current, package, has_git
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None, {}, False


def dependency_names(package: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        value = package.get(section, {})
        if isinstance(value, dict):
            names.update(str(name).lower() for name in value)
    return names


def detect_framework(command: str, package: dict[str, Any], comm: str) -> tuple[str, str, bool]:
    text = f"{command} {comm}".lower()
    deps = dependency_names(package)
    if "next" in deps or re.search(r"\bnext(?:\.js)?\s+dev\b", text):
        return "Next.js", "Node.js", True
    if "vite" in deps or re.search(r"\bvite(?:\s|$)", text):
        return "Vite", "Node.js", True
    if any(name.startswith("@redwoodjs/") for name in deps) or re.search(r"\b(?:rw|redwood)(?:\s+dev|\b)", text):
        return "RedwoodJS", "Node.js", True
    if "@nestjs/core" in deps or re.search(r"\b(?:nest|nestjs)\s+start\b", text):
        return "NestJS", "Node.js", True
    if "astro" in deps or re.search(r"\bastro\s+dev\b", text):
        return "Astro", "Node.js", True
    if "nuxt" in deps or re.search(r"\b(?:nuxt|nuxi)\s+dev\b", text):
        return "Nuxt", "Node.js", True
    if "@remix-run/dev" in deps or "@react-router/dev" in deps or re.search(r"\bremix\s+dev\b|\breact-router\s+dev\b", text):
        return "Remix / React Router", "Node.js", True
    if "django" in deps or re.search(r"\bmanage\.py\s+runserver\b|\bdjango(?:-admin)?\s+runserver\b", text):
        return "Django", "Python", True
    if "fastapi" in deps or re.search(r"\buvicorn\b", text):
        return "FastAPI / Uvicorn", "Python", True
    if "flask" in deps or re.search(r"\bflask\s+run\b", text):
        return "Flask", "Python", True
    if "rails" in deps or re.search(r"\bb(?:in/)?rails\s+server\b|\brails\s+s\b", text):
        return "Rails", "Ruby", True
    if re.search(r"\bgo\s+(?:run|serve)\b|/go-build|\b(?:air)\b", text):
        return "Go", "Go", True
    if re.search(r"\bcargo\s+(?:run|watch)\b|/target/(?:debug|release)/", text):
        return "Rust", "Rust", True
    if re.search(r"\bphp\s+(?:-s|artisan\s+serve)\b|\bartisan\s+serve\b", text):
        return "PHP", "PHP", True
    if re.search(r"\b(?:node|npm|pnpm|yarn|bun|deno)\b", text):
        return "Node.js", "Node.js", True
    if re.search(r"\bpython(?:3)?\b|\buvicorn\b|\bgunicorn\b", text):
        return "Python", "Python", True
    if re.search(r"\bruby\b|\.rb\b", text):
        return "Ruby", "Ruby", True
    return "Process", (comm or "Process"), False


def display_project_name(package: dict[str, Any], root: Path | None, cwd: str, comm: str) -> str:
    raw = str(package.get("name", "")).strip() if package else ""
    if not raw and root is not None:
        raw = root.name
    if not raw:
        raw = Path(cwd).name if cwd else comm or "Local server"
    if raw.startswith("@") and "/" in raw:
        raw = raw.split("/", 1)[1]
    raw = re.sub(r"[-_]+", " ", raw).strip()
    return raw[:1].upper() + raw[1:] if raw else "Local server"


def score_server(cwd: str, project_root: Path | None, has_git: bool, framework_signal: bool, port: int) -> int:
    score = 0
    if cwd == str(HOME) or cwd.startswith(str(HOME) + os.sep):
        score += 2
    if project_root is not None:
        score += 2
    if has_git:
        score += 1
    if framework_signal:
        score += 2
    if port in TYPICAL_PORTS:
        score += 1
    return score


def main() -> int:
    listeners = parse_listeners()
    processes = snapshot_processes()
    output: dict[str, dict[str, Any]] = {}
    for (pid, port), hosts in listeners.items():
        proc = processes.get(pid, {})
        proc_path = Path("/proc") / str(pid)
        try:
            if proc_path.stat().st_uid != UID:
                continue
        except OSError:
            continue
        if proc.get("uid", UID) != UID:
            continue
        cwd = process_cwd(pid)
        command_line = str(proc.get("commandLine") or read_text(proc_path / "cmdline", 131_072).replace("\x00", " ")).strip()
        comm = str(proc.get("comm") or read_text(proc_path / "comm", 256).strip()).strip()
        project_root, package, has_git = find_project(cwd)
        framework, runtime, framework_signal = detect_framework(command_line, package, comm)
        if score_server(cwd, project_root, has_git, framework_signal, port) < 3:
            continue
        identity = f"{pid}:{port}"
        host_list = sorted(hosts)
        wildcard = any(host in {"0.0.0.0", "::", "*"} for host in host_list)
        bind_address = "0.0.0.0" if wildcard else (host_list[0] if host_list else "localhost")
        scheme = "https" if re.search(r"(?:https://|--https(?:\b|=)|--ssl(?:\b|=)|--tls(?:\b|=)|\.pem\b|\.key\b)", command_line.lower()) else "http"
        output[identity] = {
            "id": identity,
            "pid": pid,
            "startTime": process_start_time(pid),
            "port": port,
            "bindAddress": bind_address,
            "bindAddresses": host_list,
            "url": f"{scheme}://localhost:{port}",
            "command": comm or "Process",
            "commandLine": command_line,
            "cwd": cwd,
            "projectRoot": str(project_root) if project_root else cwd,
            "projectName": display_project_name(package, project_root, cwd, comm),
            "framework": framework,
            "runtime": runtime,
            "cpu": round(float(proc.get("cpu", 0.0)), 1),
            "memoryBytes": int(proc.get("memoryBytes", 0)),
            "owner": str(proc.get("owner", "")),
            "ownedByUser": True,
            "score": score_server(cwd, project_root, has_git, framework_signal, port),
        }
    servers = sorted(output.values(), key=lambda row: (str(row["projectName"]).lower(), int(row["port"]), int(row["pid"])))
    json.dump(servers, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
