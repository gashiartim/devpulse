#!/usr/bin/env python3
"""Emit the current user's likely local development servers as JSON.

This is deliberately a single, bounded scan: one ss invocation, one ps
snapshot, then cheap /proc reads for the PIDs ss reported. It never executes a
project command or reads environment files.
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


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
DOCKER_PORT_RE = re.compile(
    r"(?P<host>\[[^\]]+\]|[^,\s:]+):(?P<public>\d+)(?:-(?P<public_end>\d+))?"
    r"->(?P<private>\d+)(?:-(?P<private_end>\d+))?/tcp"
)
MAX_SERVERS = 128
MAX_CONFIG_PORTS = 512
HTTP_PROBE_TIMEOUT = 0.25


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


def parse_port_spec(value: object) -> set[int]:
    ports: set[int] = set()
    for token in re.split(r"[,\s]+", str(value or "")):
        if not token:
            continue
        try:
            if "-" in token:
                start_text, end_text = token.split("-", 1)
                start, end = int(start_text), int(end_text)
                if start > end:
                    start, end = end, start
            else:
                start = end = int(token)
        except ValueError:
            continue
        for port in range(max(1, start), min(65535, end) + 1):
            ports.add(port)
            if len(ports) >= MAX_CONFIG_PORTS:
                return ports
    return ports


def read_configuration(argv: list[str]) -> tuple[set[int], set[int]]:
    try:
        index = argv.index("--config")
        raw = argv[index + 1][:4096]
        value = json.loads(raw)
    except (ValueError, IndexError, TypeError, json.JSONDecodeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    return parse_port_spec(value.get("includedPorts")), parse_port_spec(value.get("ignoredPorts"))


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


def host_is_loopback(host: str) -> bool:
    candidate = host.split("%", 1)[0]
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return candidate == "localhost"


def listener_is_exposed(hosts: set[str]) -> bool:
    if any(host in {"0.0.0.0", "::", "*"} for host in hosts):
        return True
    return any(not host_is_loopback(host) for host in hosts)


def browser_host(hosts: set[str]) -> str:
    """Choose a URL-safe host that can reach the reported listener."""
    if not hosts or any(host in {"0.0.0.0", "::", "*"} for host in hosts):
        return "localhost"
    if "127.0.0.1" in hosts or "::1" in hosts:
        return "localhost"
    host = sorted(hosts, key=lambda value: (":" in value, value))[0]
    if ":" in host:
        return f"[{host.replace('%', '%25')}]"
    return host


def probe_http_url(url: str) -> bool:
    """Recognize an HTTP response within a hard, short local deadline."""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or not parsed.port:
        return False
    host = parsed.hostname.replace("%25", "%")
    connection: socket.socket | ssl.SSLSocket | None = None
    deadline = time.monotonic() + HTTP_PROBE_TIMEOUT
    try:
        connection = socket.create_connection((host, parsed.port), timeout=HTTP_PROBE_TIMEOUT)
        if parsed.scheme == "https":
            connection.settimeout(max(0.001, deadline - time.monotonic()))
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            connection = context.wrap_socket(connection, server_hostname=host.split("%", 1)[0])
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        connection.settimeout(remaining)
        request = f"HEAD / HTTP/1.0\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode("ascii", "ignore")
        connection.sendall(request)
        response = bytearray()
        while b"\n" not in response and len(response) < 4096:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            connection.settimeout(remaining)
            chunk = connection.recv(min(1024, 4096 - len(response)))
            if not chunk:
                break
            response.extend(chunk)
        return bytes(response).lstrip().startswith(b"HTTP/")
    except (OSError, ValueError, ssl.SSLError):
        return False
    finally:
        if connection is not None:
            connection.close()


def probe_server(row: dict[str, Any]) -> tuple[bool, str]:
    preferred = str(row.get("url", ""))
    alternate_scheme = "https" if preferred.startswith("http://") else "http"
    alternate = re.sub(r"^https?", alternate_scheme, preferred, count=1)
    for candidate in (preferred, alternate):
        if probe_http_url(candidate):
            return True, candidate
    return False, preferred


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


def process_uptime_seconds(start_time: str) -> int:
    try:
        system_uptime = float(read_text(Path("/proc/uptime"), 256).split()[0])
        ticks_per_second = int(os.sysconf("SC_CLK_TCK"))
        return max(0, int(system_uptime - int(start_time) / ticks_per_second))
    except (IndexError, OSError, TypeError, ValueError):
        return 0


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
        raw = read_text(package_path, 1_000_000)
        data = json.loads(raw)
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


def docker_label(labels: str, key: str) -> str:
    match = re.search(rf"(?:^|,){re.escape(key)}=([^,]*)", labels)
    return match.group(1).strip() if match else ""


def parse_docker_published_ports(value: str) -> dict[int, tuple[set[str], int]]:
    published: dict[int, tuple[set[str], int]] = {}
    for match in DOCKER_PORT_RE.finditer(value):
        public_start = int(match.group("public"))
        public_end = int(match.group("public_end") or public_start)
        private_start = int(match.group("private"))
        private_end = int(match.group("private_end") or private_start)
        count = min(public_end - public_start, private_end - private_start, 15) + 1
        host = match.group("host").strip("[]")
        for offset in range(max(0, count)):
            public_port = public_start + offset
            if not 1 <= public_port <= 65535:
                continue
            private_port = private_start + offset
            hosts, _ = published.setdefault(public_port, (set(), private_port))
            hosts.add(host)
    return published


def container_project_name(container: dict[str, Any]) -> str:
    labels = str(container.get("Labels", ""))
    project = docker_label(labels, "com.docker.compose.project")
    service = docker_label(labels, "com.docker.compose.service")
    raw_name = str(container.get("Names", "")).lstrip("/")
    if not service:
        service = raw_name
        if project and service.endswith("_" + project):
            service = service[: -(len(project) + 1)]
        service = re.sub(r"^(?:supabase|docker)[-_]", "", service)
    project = re.sub(r"[-_]+", " ", project).strip()
    service = re.sub(r"[-_]+", " ", service).strip()
    if project and service and service.lower() != project.lower():
        return f"{project[:1].upper() + project[1:]} · {service}"
    value = project or service or str(container.get("Image", "Docker container"))
    return value[:1].upper() + value[1:] if value else "Docker container"


def discover_containers() -> list[dict[str, Any]]:
    raw = run_capture(["docker", "ps", "--no-trunc", "--format", "{{json .}}"], timeout=2.0)
    containers: list[dict[str, Any]] = []
    for line in raw.splitlines()[:64]:
        try:
            container = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(container, dict):
            continue
        container_id = str(container.get("ID", ""))
        if not container_id:
            continue
        labels = str(container.get("Labels", ""))
        workdir = docker_label(labels, "com.docker.compose.project.working_dir")
        if not workdir:
            workdir = docker_label(labels, "com.supabase.cli.workdir")
        if not os.path.isabs(workdir) or not Path(workdir).is_dir():
            workdir = ""
        project_root, _, _ = find_project(workdir)
        image = str(container.get("Image", "container")).split("@", 1)[0]
        image_label = image.rsplit("/", 1)[-1].split(":", 1)[0]
        for public_port, (hosts, private_port) in parse_docker_published_ports(str(container.get("Ports", ""))).items():
            identity = f"docker:{container_id}:{public_port}"
            scheme = "https" if private_port in {443, 8443} else "http"
            containers.append({
                "id": identity,
                "pid": 0,
                "startTime": "",
                "uptimeSeconds": 0,
                "runningFor": str(container.get("RunningFor", "")),
                "containerStatus": str(container.get("Status", "")),
                "port": public_port,
                "privatePort": private_port,
                "bindAddress": "0.0.0.0" if listener_is_exposed(hosts) else sorted(hosts)[0],
                "bindAddresses": sorted(hosts),
                "exposed": listener_is_exposed(hosts),
                "url": f"{scheme}://{browser_host(hosts)}:{public_port}",
                "httpAvailable": False,
                "command": str(container.get("Names", "Docker container")),
                "commandLine": f"docker {image}",
                "cwd": workdir,
                "projectRoot": str(project_root) if project_root else workdir,
                "projectName": container_project_name(container),
                "framework": f"Docker · {image_label}",
                "runtime": "Docker",
                "cpu": 0.0,
                "memoryBytes": 0,
                "owner": "",
                "ownedByUser": False,
                "canStop": False,
                "source": "docker",
                "containerId": container_id,
                "containerName": str(container.get("Names", "")),
                "score": 5,
            })
    return containers


def main() -> int:
    included_ports, ignored_ports = read_configuration(sys.argv[1:])
    listeners = parse_listeners()
    processes = snapshot_processes()
    output: dict[str, dict[str, Any]] = {}
    for (pid, port), hosts in listeners.items():
        if port in ignored_ports:
            continue
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
        if port not in included_ports and score_server(cwd, project_root, has_git, framework_signal, port) < 3:
            continue
        identity = f"{pid}:{port}"
        host_list = sorted(hosts)
        wildcard = any(host in {"0.0.0.0", "::", "*"} for host in host_list)
        bind_address = "0.0.0.0" if wildcard else (host_list[0] if host_list else "localhost")
        url_host = browser_host(hosts)
        scheme = "https" if re.search(r"(?:https://|--https(?:\b|=)|--ssl(?:\b|=)|--tls(?:\b|=)|\.pem\b|\.key\b)", command_line.lower()) else "http"
        start_time = process_start_time(pid)
        output[identity] = {
            "id": identity,
            "pid": pid,
            "startTime": start_time,
            "uptimeSeconds": process_uptime_seconds(start_time),
            "port": port,
            "bindAddress": bind_address,
            "bindAddresses": host_list,
            "exposed": listener_is_exposed(hosts),
            "url": f"{scheme}://{url_host}:{port}",
            "httpAvailable": False,
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
            "canStop": True,
            "source": "process",
            "score": score_server(cwd, project_root, has_git, framework_signal, port),
        }
    if "--include-containers" in sys.argv[1:]:
        for container in discover_containers():
            if int(container["port"]) not in ignored_ports:
                output[str(container["id"])] = container
    servers = sorted(
        output.values(),
        key=lambda row: (str(row["projectName"]).lower(), int(row["port"]), int(row["pid"])),
    )[:MAX_SERVERS]
    if servers:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(servers))) as executor:
            probe_results = list(executor.map(probe_server, servers))
        for server, (available, url) in zip(servers, probe_results):
            server["httpAvailable"] = available
            server["url"] = url
    json.dump(servers, sys.stdout, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
