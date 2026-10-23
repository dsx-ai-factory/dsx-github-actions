# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import gzip
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest


COMPARATOR = Path(__file__).resolve().parents[1] / "scripts" / "compare-charts.py"
CHART = ("example/Chart.yaml", b"apiVersion: v2\nname: example\nversion: 1.0.0\n")
LOCK = b"""dependencies:
- name: child
  version: 2.0.0
  repository: https://charts.example.test
digest: sha256:original
generated: "2026-10-01T00:00:00Z"
"""


def special_entry(name, entry_type):
    entry = tarfile.TarInfo(name)
    entry.type = entry_type
    return entry


class CompareChartsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.local = Path(self.directory.name) / "local.tgz"
        self.remote = Path(self.directory.name) / "remote.tgz"
        self.env = {
            **os.environ,
            "KUBECONFIG": str(Path(self.directory.name) / "kubeconfig"),
            "HELM_PLUGINS": str(Path(self.directory.name) / "plugins"),
        }

    def write_chart(self, path, entries, timestamp=1):
        contents = io.BytesIO()
        with tarfile.open(fileobj=contents, mode="w") as archive:
            for value in entries:
                entry = value if isinstance(value, tarfile.TarInfo) else tarfile.TarInfo(value[0])
                entry.mtime = timestamp
                entry.uid = timestamp
                if isinstance(value, tarfile.TarInfo):
                    archive.addfile(entry)
                else:
                    entry.size = len(value[1])
                    archive.addfile(entry, io.BytesIO(value[1]))
        path.write_bytes(gzip.compress(contents.getvalue(), mtime=timestamp))

    def compare(self, expected, message="", env=None):
        result = subprocess.run(
            [sys.executable, str(COMPARATOR), str(self.local), str(self.remote)],
            capture_output=True, text=True, check=False, env=self.env if env is None else env,
        )
        self.assertEqual(result.returncode, expected, result.stderr)
        self.assertIn(message, result.stderr)
        return result

    def test_archive_metadata_order_and_lock_generation_times_do_not_change_identity(self):
        entries = [
            CHART,
            ("example/values.yaml", b"replicas: 2\n"),
            ("example/Chart.lock", LOCK),
            ("example/charts/child/Chart.yaml", b"apiVersion: v2\nname: child\nversion: 2.0.0\n"),
            ("example/charts/child/Chart.lock", LOCK),
        ]
        self.write_chart(self.local, entries)
        refreshed = [
            (name, contents.replace(b"2026-10-01", b"2026-10-02"))
            for name, contents in reversed(entries)
        ] + [special_entry("example/", tarfile.DIRTYPE)]
        self.write_chart(self.remote, refreshed, timestamp=2)
        self.assertNotEqual(self.local.read_bytes(), self.remote.read_bytes())
        self.compare(0)

    def test_changed_contents_and_file_membership_fail(self):
        self.write_chart(self.local, [CHART, ("example/values.yaml", b"replicas: 2\n")])
        cases = [
            ("changed contents", [CHART, ("example/values.yaml", b"replicas: 3\n")], "contents differ"),
            ("missing file", [CHART], "file paths differ"),
            ("extra file", [CHART, ("example/values.yaml", b"replicas: 2\n"),
                            ("example/extra", b"new")], "file paths differ"),
        ]
        for scenario, entries, message in cases:
            with self.subTest(scenario=scenario):
                self.write_chart(self.remote, entries)
                self.compare(1, message)

    def test_lock_dependency_digest_and_other_fields_are_preserved(self):
        self.write_chart(self.local, [CHART, ("example/Chart.lock", LOCK)])
        cases = [
            ("dependency version", LOCK.replace(b"2.0.0", b"2.0.1")),
            ("digest", LOCK.replace(b"sha256:original", b"sha256:changed")),
            ("other field", LOCK + b"other: value\n"),
        ]
        for scenario, lock in cases:
            with self.subTest(scenario=scenario):
                self.write_chart(self.remote, [CHART, ("example/Chart.lock", lock)])
                self.compare(1, "contents differ at 'example/Chart.lock'")

    def test_nested_lock_fields_remain_significant(self):
        entries = [CHART, ("example/charts/child/Chart.yaml",
                           b"apiVersion: v2\nname: child\nversion: 2.0.0\n")]
        name = "example/charts/child/Chart.lock"
        self.write_chart(self.local, [*entries, (name, LOCK)])
        self.write_chart(self.remote, [*entries, (name, LOCK.replace(b"sha256:original", b"sha256:changed"))])
        self.compare(1, f"contents differ at {name!r}")

    def test_equivalent_lock_yaml_formatting_does_not_change_identity(self):
        formatted = b'''# Same dependency resolution, with different YAML formatting.
generated: '2026-10-02T00:00:00Z'
digest: "sha256:original"
dependencies: [{repository: "https://charts.example.test", version: "2.0.0", name: child}]
'''
        self.write_chart(self.local, [CHART, ("example/Chart.lock", LOCK)])
        self.write_chart(self.remote, [CHART, ("example/Chart.lock", formatted)])
        self.compare(0)

    def test_application_file_named_chart_lock_remains_byte_exact(self):
        self.write_chart(self.local, [CHART, ("example/templates/x/Chart.lock", LOCK)])
        self.write_chart(self.remote, [CHART, ("example/templates/x/Chart.lock",
                                             LOCK.replace(b"2026-10-01", b"2026-10-02"))])
        self.compare(1, "contents differ at 'example/templates/x/Chart.lock'")

    def test_malformed_locks_fail_without_printing_their_contents(self):
        cases = [
            ("invalid YAML", b"digest: [private-lock-content"),
            ("not a mapping", b"- private-lock-content\n"),
            ("multiple documents", LOCK + b"---\ndigest: private-lock-content\n"),
            ("duplicate fields", LOCK + b"digest: private-lock-content\n"),
        ]
        for scenario, lock in cases:
            with self.subTest(scenario=scenario):
                self.write_chart(self.local, [CHART, ("example/Chart.lock", lock)])
                self.write_chart(self.remote, [CHART, ("example/Chart.lock", lock)])
                result = self.compare(1, "cannot parse chart lock")
                self.assertNotIn("private-lock-content", result.stderr + result.stdout)

    def test_ambiguous_or_unsafe_remote_members_fail(self):
        self.write_chart(self.local, [CHART])
        cases = [
            ("duplicate path", [CHART, CHART], "duplicate chart path"),
            ("parent traversal", [CHART, ("../outside", b"value")], "unsafe chart path"),
            ("absolute path", [CHART, ("/outside", b"value")], "unsafe chart path"),
            ("noncanonical path", [CHART, ("example/./values.yaml", b"value")], "unsafe chart path"),
            ("symlink", [CHART, special_entry("example/link", tarfile.SYMTYPE)], "unsupported chart entry"),
            ("parent is a file", [CHART, ("example/files", b"value"),
                                  ("example/files/config", b"value")], "also a parent directory"),
            ("empty archive", [], "contains no files"),
        ]
        for scenario, entries, message in cases:
            with self.subTest(scenario=scenario):
                self.write_chart(self.remote, entries)
                self.compare(1, message)

    def test_unreadable_or_truncated_archive_fails(self):
        self.write_chart(self.local, [CHART])
        valid = self.local.read_bytes()
        for scenario, contents in [("invalid", b"not an archive"), ("truncated gzip", valid[:-4])]:
            with self.subTest(scenario=scenario):
                self.remote.write_bytes(contents)
                self.compare(1, "cannot read chart archive 'remote.tgz'")
        self.remote.unlink()
        self.compare(1, "cannot read chart archive 'remote.tgz'")

    def test_corrupt_later_tar_header_cannot_hide_after_matching_files(self):
        self.write_chart(self.local, [CHART])
        raw = gzip.decompress(self.local.read_bytes())
        # One header plus the padded Chart.yaml payload precedes tar's EOF.
        self.remote.write_bytes(gzip.compress(raw[:1024] + b"invalid header".ljust(512, b"x") + raw[1536:]))
        self.compare(1, "Helm cannot load chart archive 'remote.tgz'")

    def test_invalid_lock_timestamp_cannot_be_normalized_away(self):
        self.write_chart(self.local, [CHART, ("example/Chart.lock", LOCK)])
        invalid = LOCK.replace(b"2026-10-01T00:00:00Z", b"not-a-timestamp")
        self.write_chart(self.remote, [CHART, ("example/Chart.lock", invalid)])
        self.compare(1, "Helm cannot load chart archive 'remote.tgz'")

    def test_missing_yaml_parser_cannot_accept_lock_files(self):
        self.write_chart(self.local, [CHART, ("example/Chart.lock", LOCK)])
        self.write_chart(self.remote, [CHART, ("example/Chart.lock", LOCK)])
        self.compare(1, "cannot parse chart lock", env={**os.environ, "PATH": self.directory.name})


if __name__ == "__main__":
    unittest.main()
