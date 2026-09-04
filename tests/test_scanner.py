import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / "scripts" / "scan-servers.py"
GIT_INFO = ROOT / "scripts" / "git-info.py"
STOP = ROOT / "scripts" / "stop-server.py"


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


class DevPulseHelpersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scanner = load_scanner()

    def test_endpoint_parsing_accepts_ipv4_and_ipv6(self):
        self.assertEqual(self.scanner.parse_endpoint("127.0.0.1:3000"), ("127.0.0.1", 3000))
        self.assertEqual(self.scanner.parse_endpoint("[::1]:5173"), ("::1", 5173))
        self.assertEqual(self.scanner.parse_endpoint("0.0.0.0:43123"), ("0.0.0.0", 43123))

    def test_browser_host_uses_reachable_listener_address(self):
        self.assertEqual(self.scanner.browser_host({"0.0.0.0"}), "localhost")
        self.assertEqual(self.scanner.browser_host({"::1"}), "localhost")
        self.assertEqual(self.scanner.browser_host({"192.168.10.170"}), "192.168.10.170")
        self.assertEqual(self.scanner.browser_host({"fe80::1%enp1s0"}), "[fe80::1%25enp1s0]")

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

    def test_stop_sends_sigterm_to_exact_process(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            result = subprocess.run(
                [str(STOP), str(proc.pid), process_start_time(proc.pid)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(json.loads(result.stdout)["ok"])
            self.assertEqual(proc.wait(timeout=3), -signal.SIGTERM)
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=3)

    def test_stop_rejects_stale_start_time_without_signaling(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            result = subprocess.run([str(STOP), str(proc.pid), "1"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("process-changed", result.stdout)
            self.assertIsNone(proc.poll())
        finally:
            proc.terminate()
            proc.wait(timeout=3)

    def test_stop_requires_a_start_time_token(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            result = subprocess.run([str(STOP), str(proc.pid)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid-pid", result.stdout)
            self.assertIsNone(proc.poll())
        finally:
            proc.terminate()
            proc.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
