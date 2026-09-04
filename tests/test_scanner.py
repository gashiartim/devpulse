import json
import importlib.util
import os
import signal
import subprocess
import sys
import tempfile
import time
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


class DevPulseHelpersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scanner = load_scanner()

    def test_endpoint_parsing_accepts_ipv4_and_ipv6(self):
        self.assertEqual(self.scanner.parse_endpoint("127.0.0.1:3000"), ("127.0.0.1", 3000))
        self.assertEqual(self.scanner.parse_endpoint("[::1]:5173"), ("::1", 5173))
        self.assertEqual(self.scanner.parse_endpoint("0.0.0.0:43123"), ("0.0.0.0", 43123))

    def test_framework_detection_covers_common_javascript_servers(self):
        cases = (
            ("pnpm next dev", {"next": "^15"}, "Next.js"),
            ("vite --host", {"vite": "^6"}, "Vite"),
            ("yarn rw dev", {"@redwoodjs/core": "^8"}, "RedwoodJS"),
            ("nest start --watch", {"@nestjs/core": "^11"}, "NestJS"),
            ("nuxi dev", {"nuxt": "^3"}, "Nuxt"),
        )
        for command, package, expected in cases:
            framework, runtime, signal = self.scanner.detect_framework(command, package, "node")
            self.assertEqual(framework, expected)
            self.assertEqual(runtime, "Node.js")
            self.assertTrue(signal)

    def test_git_info_handles_missing_and_non_repo_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            payload = json.dumps([temp, "/does/not/exist"])
            result = subprocess.check_output([str(GIT_INFO), payload], text=True)
            data = json.loads(result)
            self.assertFalse(data[temp]["available"])
            self.assertFalse(data["/does/not/exist"]["available"])

    def test_stop_rejects_stale_start_time_without_signaling(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            result = subprocess.run([str(STOP), str(proc.pid), "1"], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("process-changed", result.stdout)
            self.assertIsNone(proc.poll())
        finally:
            os.kill(proc.pid, signal.SIGTERM)
            proc.wait(timeout=3)

    def test_stop_requires_a_start_time_token(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            result = subprocess.run([str(STOP), str(proc.pid)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid-pid", result.stdout)
            self.assertIsNone(proc.poll())
        finally:
            os.kill(proc.pid, signal.SIGTERM)
            proc.wait(timeout=3)

    @unittest.skipUnless(os.environ.get("DEVPULSE_LIVE_FIXTURE") == "1", "requires loopback fixture namespace")
    def test_live_scan_has_expected_fixture_ports(self):
        fixture_dir = Path(os.environ.get("DEVPULSE_FIXTURE_DIR", "/tmp/devpulse-live-test"))
        marker = fixture_dir / "pyproject.toml"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=True)
        try:
            rows = json.loads(subprocess.check_output([str(SCANNER)], text=True))
            ports = {row["port"] for row in rows}
            self.assertTrue({43123, 43124}.issubset(ports), ports)
        finally:
            marker.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
