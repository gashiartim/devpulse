import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / "scripts" / "scan-servers.py"
GIT_INFO = ROOT / "scripts" / "git-info.py"
STOP = ROOT / "scripts" / "stop-server.py"
STOP_CONTAINER = ROOT / "scripts" / "stop-container.py"


def load_scanner():
    spec = importlib.util.spec_from_file_location("devpulse_scanner", SCANNER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def process_start_time(pid):
    raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    closing = raw.rfind(")")
    fields = raw[closing + 2:].split()
    return fields[19]


class QuietHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.send_response(204)
        self.end_headers()

    def log_message(self, format, *args):
        pass


class DevPulseHelpersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scanner = load_scanner()

    def test_endpoint_parsing_accepts_ipv4_and_ipv6(self):
        self.assertEqual(self.scanner.parse_endpoint("127.0.0.1:3000"), ("127.0.0.1", 3000))
        self.assertEqual(self.scanner.parse_endpoint("[::1]:5173"), ("::1", 5173))
        self.assertEqual(self.scanner.parse_endpoint("0.0.0.0:43123"), ("0.0.0.0", 43123))

    def test_port_specs_support_ranges_and_remain_bounded(self):
        self.assertEqual(self.scanner.parse_port_spec("3000, 4100-4102 nope"), {3000, 4100, 4101, 4102})
        self.assertEqual(self.scanner.parse_port_spec("3-1"), {1, 2, 3})
        self.assertLessEqual(len(self.scanner.parse_port_spec("1-65535")), self.scanner.MAX_CONFIG_PORTS)

    def test_scanner_configuration_parses_include_and_ignore_lists(self):
        included, ignored = self.scanner.read_configuration([
            "--config", '{"includedPorts":"4100","ignoredPorts":"5000-5001"}'
        ])
        self.assertEqual(included, {4100})
        self.assertEqual(ignored, {5000, 5001})

    def test_browser_host_uses_reachable_listener_address(self):
        self.assertEqual(self.scanner.browser_host({"0.0.0.0"}), "localhost")
        self.assertEqual(self.scanner.browser_host({"::1"}), "localhost")
        self.assertEqual(self.scanner.browser_host({"192.168.10.170"}), "192.168.10.170")
        self.assertEqual(self.scanner.browser_host({"fe80::1%enp1s0"}), "[fe80::1%25enp1s0]")

    def test_exposure_detection_distinguishes_loopback_and_lan(self):
        self.assertFalse(self.scanner.listener_is_exposed({"127.0.0.1", "::1"}))
        self.assertTrue(self.scanner.listener_is_exposed({"0.0.0.0"}))
        self.assertTrue(self.scanner.listener_is_exposed({"::"}))
        self.assertTrue(self.scanner.listener_is_exposed({"192.168.10.170"}))

    def test_docker_port_parser_groups_dual_stack_publications(self):
        ports = self.scanner.parse_docker_published_ports(
            "0.0.0.0:54323->3000/tcp, [::]:54323->3000/tcp, 5432/tcp"
        )
        self.assertEqual(set(ports), {54323})
        self.assertEqual(ports[54323][0], {"0.0.0.0", "::"})
        self.assertEqual(ports[54323][1], 3000)

    def test_docker_project_name_uses_compose_context(self):
        container = {
            "Names": "web_reliva",
            "Labels": "com.docker.compose.project=reliva,com.docker.compose.service=web",
            "Image": "example/web:latest",
        }
        self.assertEqual(self.scanner.container_project_name(container), "Reliva · web")

    def test_http_probe_accepts_real_http_and_rejects_non_http(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.bind(("127.0.0.1", 0))
        raw.listen()
        try:
            self.assertTrue(self.scanner.probe_http_url(f"http://127.0.0.1:{server.server_port}"))
            self.assertFalse(self.scanner.probe_http_url(f"http://127.0.0.1:{raw.getsockname()[1]}"))
        finally:
            raw.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_listener_parser_collapses_ipv4_and_ipv6_duplicates(self):
        output = "\n".join((
            'LISTEN 0 511 127.0.0.1:43123 0.0.0.0:* users:(("python3",pid=123,fd=3))',
            'LISTEN 0 511 [::1]:43123 [::]:* users:(("python3",pid=123,fd=4))',
        ))
        original = self.scanner.run_capture
        self.scanner.run_capture = lambda argv, timeout=2.0: output
        try:
            self.assertEqual(
                self.scanner.parse_listeners(),
                {(123, 43123): {"127.0.0.1", "::1"}},
            )
        finally:
            self.scanner.run_capture = original

    def test_framework_detection_uses_package_dependencies(self):
        cases = (
            ({"dependencies": {"next": "^15"}}, "Next.js"),
            ({"devDependencies": {"vite": "^6"}}, "Vite"),
            ({"dependencies": {"@redwoodjs/core": "^8"}}, "RedwoodJS"),
            ({"dependencies": {"@nestjs/core": "^11"}}, "NestJS"),
            ({"dependencies": {"nuxt": "^3"}}, "Nuxt"),
        )
        for package, expected in cases:
            framework, runtime, detected = self.scanner.detect_framework("node server.js", package, "node")
            self.assertEqual(framework, expected)
            self.assertEqual(runtime, "Node.js")
            self.assertTrue(detected)

    def test_live_scan_discovers_and_collapses_loopback_listeners(self):
        ipv4 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ipv6 = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        try:
            ipv4.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            ipv4.bind(("127.0.0.1", 0))
            port = ipv4.getsockname()[1]
            ipv4.listen()

            ipv6.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            ipv6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            ipv6.bind(("::1", port))
            ipv6.listen()

            rows = json.loads(subprocess.check_output([str(SCANNER)], text=True))
            matches = [row for row in rows if row["pid"] == os.getpid() and row["port"] == port]
            self.assertEqual(len(matches), 1)
            self.assertEqual(set(matches[0]["bindAddresses"]), {"127.0.0.1", "::1"})
            self.assertEqual(matches[0]["url"], f"http://localhost:{port}")
            self.assertFalse(matches[0]["exposed"])
            self.assertFalse(matches[0]["httpAvailable"])
            self.assertGreaterEqual(matches[0]["uptimeSeconds"], 0)
        finally:
            ipv6.close()
            ipv4.close()

    def test_git_info_reports_clean_and_dirty_repo(self):
        with tempfile.TemporaryDirectory() as temp:
            subprocess.run(["git", "init", "-q", "-b", "main", temp], check=True)
            subprocess.run(["git", "-C", temp, "config", "user.name", "DevPulse Test"], check=True)
            subprocess.run(["git", "-C", temp, "config", "user.email", "devpulse@example.invalid"], check=True)
            tracked = Path(temp) / "tracked.txt"
            tracked.write_text("clean\n", encoding="utf-8")
            subprocess.run(["git", "-C", temp, "add", "tracked.txt"], check=True)
            subprocess.run(["git", "-C", temp, "commit", "-q", "-m", "fixture"], check=True)

            clean = json.loads(subprocess.check_output([str(GIT_INFO), json.dumps([temp])], text=True))[temp]
            self.assertTrue(clean["available"])
            self.assertEqual(clean["branch"], "main")
            self.assertFalse(clean["dirty"])

            tracked.write_text("dirty\n", encoding="utf-8")
            dirty = json.loads(subprocess.check_output([str(GIT_INFO), json.dumps([temp])], text=True))[temp]
            self.assertTrue(dirty["dirty"])

    def test_git_info_handles_missing_and_non_repo_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            payload = json.dumps([temp, "/does/not/exist"])
            result = subprocess.check_output([str(GIT_INFO), payload], text=True)
            data = json.loads(result)
            self.assertFalse(data[temp]["available"])
            self.assertFalse(data["/does/not/exist"]["available"])

    def test_stop_sends_sigterm_to_exact_listener_process(self):
        script = (
            "import http.server,sys; "
            "s=http.server.ThreadingHTTPServer(('127.0.0.1',0),http.server.SimpleHTTPRequestHandler); "
            "print(s.server_port,flush=True); s.serve_forever()"
        )
        proc = subprocess.Popen(
            [sys.executable, "-u", "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            self.assertIsNotNone(proc.stdout)
            port = int(proc.stdout.readline().strip())
            result = subprocess.run(
                [str(STOP), str(proc.pid), process_start_time(proc.pid), str(port)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["port"], port)
            self.assertEqual(proc.wait(timeout=3), -signal.SIGTERM)
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=3)
            if proc.stdout is not None:
                proc.stdout.close()

    def test_stop_rejects_stale_start_time_without_signaling(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            result = subprocess.run([str(STOP), str(proc.pid), "1", "43123"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("process-changed", result.stdout)
            self.assertIsNone(proc.poll())
        finally:
            proc.terminate()
            proc.wait(timeout=3)

    def test_stop_rejects_process_that_no_longer_owns_listener(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            result = subprocess.run(
                [str(STOP), str(proc.pid), process_start_time(proc.pid), "43123"],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("listener-changed", result.stdout)
            self.assertIsNone(proc.poll())
        finally:
            proc.terminate()
            proc.wait(timeout=3)

    def test_stop_requires_start_time_and_port_tokens(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            result = subprocess.run([str(STOP), str(proc.pid)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid-pid", result.stdout)
            self.assertIsNone(proc.poll())
        finally:
            proc.terminate()
            proc.wait(timeout=3)

    def test_container_stop_rechecks_identity_and_published_port(self):
        container_id = "a" * 64
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            log_path = temp_path / "docker.log"
            docker = temp_path / "docker"
            docker.write_text(
                "#!/usr/bin/env python3\n"
                "import json,os,sys\n"
                "with open(os.environ['DOCKER_TEST_LOG'],'a') as f: f.write(' '.join(sys.argv[1:])+'\\n')\n"
                "if sys.argv[1] == 'inspect':\n"
                " print(json.dumps([{'Id':os.environ['DOCKER_TEST_ID'],'State':{'Running':True},"
                "'NetworkSettings':{'Ports':{'3000/tcp':[{'HostIp':'0.0.0.0','HostPort':'54323'}]}}}]))\n"
                "elif sys.argv[1] == 'stop': print(os.environ['DOCKER_TEST_ID'])\n",
                encoding="utf-8",
            )
            docker.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = temp + os.pathsep + env.get("PATH", "")
            env["DOCKER_TEST_LOG"] = str(log_path)
            env["DOCKER_TEST_ID"] = container_id

            stopped = subprocess.run(
                [str(STOP_CONTAINER), container_id, "54323"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
            self.assertTrue(json.loads(stopped.stdout)["ok"])
            calls = log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(calls[0], f"inspect {container_id}")
            self.assertEqual(calls[1], f"stop --time 10 {container_id}")

            log_path.write_text("", encoding="utf-8")
            stale = subprocess.run(
                [str(STOP_CONTAINER), container_id, "54324"],
                capture_output=True,
                text=True,
                env=env,
            )
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn("listener-changed", stale.stdout)
            self.assertEqual(log_path.read_text(encoding="utf-8").count("stop"), 0)

    def test_manifest_has_marketplace_safe_metadata_and_entries(self):
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schemaVersion"], 1)
        self.assertEqual(manifest["id"], "io.github.gashiartim.devpulse")
        self.assertEqual(manifest["version"], "0.2.0")
        self.assertEqual(manifest["license"], "MIT")
        self.assertTrue(manifest["barWidget"]["defaults"]["includeContainers"])
        for entry in manifest["entryPoints"].values():
            path = ROOT / entry
            self.assertTrue(path.is_file())
            self.assertFalse(path.is_symlink())

    def test_qml_wires_adaptive_polling_search_and_http_guard(self):
        service = (ROOT / "Service.qml").read_text(encoding="utf-8")
        panel = (ROOT / "Panel.qml").read_text(encoding="utf-8")
        self.assertIn("panelOpen ? activeIntervalSec : refreshIntervalSec", service)
        self.assertIn("includedPorts: includedPorts, ignoredPorts: ignoredPorts", service)
        self.assertIn("filterServers(allServers, filterText)", panel)
        self.assertIn("server.httpAvailable !== true", panel)
        self.assertIn("stopContainerPath", service)


if __name__ == "__main__":
    unittest.main()
