# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Real Helm integration tests; select a binary with HELM_TEST_BINARY."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import unittest


HELPER = Path(__file__).resolve().parents[1] / "scripts" / "update-repo.sh"
REPOSITORY = "local-test"
CHART = "cached-chart"
SECRET = "synthetic-repository-secret-marker"
STRICT_FLAG = "--fail-on-repo-update-fail"


def chart_index(version="1.0.0-rc.1", warning=False):
    entries = {
        CHART: [{
            "apiVersion": "v2",
            "name": CHART,
            "version": version,
            "urls": [f"{CHART}-{version}.tgz"],
        }],
    }
    if warning:
        entries["invalid-chart"] = [{
            "apiVersion": "v2",
            "name": "invalid-chart",
            "version": SECRET,
            "urls": ["invalid-chart.tgz"],
        }]
    # JSON is valid YAML, so the fixture needs no third-party YAML dependency.
    return json.dumps({"apiVersion": "v1", "entries": entries}).encode()


class IndexHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests.append(self.path)
        status, body = self.server.response
        if self.path != f"/{SECRET}/index.yaml":
            status, body = 404, b"unexpected request"
        self.send_response(status)
        self.send_header("Content-Type", "application/yaml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class UpdateRepoIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        requested = os.environ.get("HELM_TEST_BINARY", "helm")
        discovered = shutil.which(requested)
        if discovered is None:
            raise unittest.SkipTest(
                "Real Helm unavailable; install Helm 3 or 4, or set "
                "HELM_TEST_BINARY to an executable Helm binary."
            )
        cls.helm_binary = str(Path(discovered).resolve())

    def setUp(self):
        self.assertTrue(HELPER.is_file(), f"Missing helper: {HELPER}")
        temporary = tempfile.TemporaryDirectory(prefix="helm-update-integration-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        binary_directory = self.directory / "bin"
        binary_directory.mkdir()
        (binary_directory / "helm").symlink_to(self.helm_binary)
        self.temp_root = self.directory / "tmp"
        self.temp_root.mkdir()
        self.cache = self.directory / "cache" / "repository"
        # Never load a user's Helm repositories, plugins, cluster, or shell hooks.
        self.env = {
            "PATH": f"{binary_directory}{os.pathsep}{os.defpath}",
            "HOME": str(self.directory),
            "TMPDIR": str(self.temp_root),
            "RUNNER_TEMP": str(self.temp_root),
            "HELM_CONFIG_HOME": str(self.directory / "config"),
            "HELM_CACHE_HOME": str(self.directory / "cache"),
            "HELM_DATA_HOME": str(self.directory / "data"),
            "HELM_REPOSITORY_CONFIG": str(self.directory / "repositories.yaml"),
            "HELM_REPOSITORY_CACHE": str(self.cache),
            "HELM_REGISTRY_CONFIG": str(self.directory / "registry.json"),
            "HELM_PLUGINS": str(self.directory / "plugins"),
            "KUBECONFIG": str(self.directory / "kubeconfig"),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
        version = self.run_helm("version", "--template", "{{.Version}}")
        self.assertEqual(version.returncode, 0, version.stdout + version.stderr)
        self.version = version.stdout.strip()
        match = re.fullmatch(r"v([34])\.\d+\.\d+(?:[+-][a-zA-Z0-9.+-]+)?", self.version)
        self.assertIsNotNone(match, f"Expected a real Helm 3 or 4 binary, got {self.version!r}")
        self.major = int(match.group(1))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), IndexHandler)
        self.addCleanup(self.server.server_close)
        self.server.response = (200, chart_index())
        self.server.requests = []
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_port}/{SECRET}"
        added = self.run_helm("repo", "add", REPOSITORY, self.url)
        self.assertEqual(added.returncode, 0, added.stdout + added.stderr)
        self.assertEqual(self.server.requests, [f"/{SECRET}/index.yaml"])
        self.assert_cached_version("1.0.0-rc.1")

    def run_command(self, *args):
        return subprocess.run(
            list(args), env=self.env, cwd=self.directory,
            capture_output=True, text=True, timeout=15,
        )

    def run_helm(self, *args):
        return self.run_command(self.helm_binary, *args)

    def run_helper(self):
        requests_before = len(self.server.requests)
        result = self.run_command("bash", str(HELPER), REPOSITORY)
        self.assertNotIn(SECRET, result.stdout + result.stderr)
        self.assertGreater(
            len(self.server.requests), requests_before,
            "Helper must fetch the index, not trust an existing cache or fail before fetching",
        )
        self.assertEqual(list(self.temp_root.iterdir()), [], "Helper leaked temporary diagnostics")
        return result

    def assert_cached_version(self, version):
        result = self.run_helm(
            "search", "repo", f"{REPOSITORY}/{CHART}", "--versions", "--devel", "--output", "json",
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(
            [(entry["name"], entry["version"]) for entry in json.loads(result.stdout)],
            [(f"{REPOSITORY}/{CHART}", version)],
        )

    def test_success_refreshes_cached_index(self):
        self.server.response = (200, chart_index("1.0.0-rc.2"))
        result = self.run_helper()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        self.assert_cached_version("1.0.0-rc.2")

    def assert_refresh_fails_with_stale_cache(self, status, body):
        initial = self.run_helper()
        self.assertEqual(initial.returncode, 0, initial.stdout + initial.stderr)
        cached_index = self.cache / f"{REPOSITORY}-index.yaml"
        cached_contents = cached_index.read_bytes()
        self.server.response = (status, body)

        # Establish that the raw CLI exposes the URL marker and that Helm 3
        # needs its strict flag even when an old, searchable index exists.
        raw = self.run_helm("repo", "update", REPOSITORY)
        self.assertEqual(raw.returncode == 0, self.major == 3, raw.stdout + raw.stderr)
        self.assertIn(SECRET, raw.stdout + raw.stderr)
        self.assertEqual(cached_index.read_bytes(), cached_contents)
        self.assert_cached_version("1.0.0-rc.1")

        for attempt in range(2):
            with self.subTest(attempt=attempt):
                result = self.run_helper()
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("existence is unknown", result.stderr)
                self.assertEqual(cached_index.read_bytes(), cached_contents)
                self.assert_cached_version("1.0.0-rc.1")

    def test_http_503_cannot_succeed_from_stale_cache(self):
        self.assert_refresh_fails_with_stale_cache(503, b"temporarily unavailable")

    def test_invalid_yaml_cannot_succeed_from_stale_cache(self):
        self.assert_refresh_fails_with_stale_cache(200, b"apiVersion: v1\nentries: [\n")

    def test_non_index_yaml_cannot_succeed_from_stale_cache(self):
        self.assert_refresh_fails_with_stale_cache(200, b"notAnIndex: true\n")

    def test_stderr_warning_is_failure_even_when_helm_exits_zero(self):
        self.server.response = (200, chart_index(warning=True))
        flags = [STRICT_FLAG] if self.major == 3 else []
        raw = self.run_helm("repo", "update", REPOSITORY, *flags)
        self.assertEqual(raw.returncode, 0, raw.stdout + raw.stderr)
        self.assertIn(SECRET, raw.stderr, "Fixture must cause a real Helm stderr warning")
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("existence is unknown", result.stderr)

    def test_legacy_strict_flag_compatibility(self):
        requests_before = len(self.server.requests)
        result = self.run_helm("repo", "update", REPOSITORY, STRICT_FLAG)
        if self.major == 3:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertGreater(len(self.server.requests), requests_before)
        else:
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(f"unknown flag: {STRICT_FLAG}", result.stderr)
            self.assertEqual(len(self.server.requests), requests_before)


if __name__ == "__main__":
    unittest.main()
