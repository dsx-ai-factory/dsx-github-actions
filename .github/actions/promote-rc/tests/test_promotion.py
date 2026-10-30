# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import base64
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import charts
import promotion as p

COMMIT = "a" * 40
PUBLISHER_SHA = "d" * 40
IMAGE = "sha256:" + "b" * 64
RC = "1.2.3-rc.7"
DESTINATIONS = {"nvcr.io/example/components-dev/service:1.2.3"}


def config():
    return {
        "schema": 1, "enabled": True,
        "source_workflow": ".github/workflows/release-rc.yml",
        "source_publisher_sha": PUBLISHER_SHA,
        "source_ngc_path": "example/components-dev",
        "destination_ngc_path": "example/components-dev",
        "images": ["service"],
        "charts": [{"name": "service", "version_value_paths": [["image", "tag"]]}],
        "competing_workflows": [".github/workflows/release.yml"],
    }


def packed(files):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(content))
    return data.getvalue()


def archive(files=None):
    default = {
        "service/Chart.yaml": charts.encoded({
            "apiVersion": "v2", "name": "service", "version": RC, "appVersion": RC,
            "annotations": {"dsx.nvidia.com/source-revision": COMMIT, "existing": "kept"},
            "dependencies": [{"name": "child", "version": RC, "repository": "file://../child"}],
        }),
        "service/values.yaml": charts.encoded({
            "image": {"repository": "nvcr.io/example/components-dev/service", "tag": RC},
            "unrelated": RC,
        }),
        "service/templates/pod.yaml": (
            b"apiVersion: v1\nkind: Pod\nmetadata:\n  name: service\n"
            b"spec:\n  containers:\n    - name: service\n"
            b"      image: {{ .Values.image.repository }}:{{ .Values.image.tag }}\n"
        ),
        "service/Chart.lock": b'{"dependencies":[{"name":"child","version":"1.2.3-rc.7"}]}\n',
        "service/charts/child-1.2.3-rc.7.tgz": packed({
            "child/Chart.yaml": charts.encoded({
                "apiVersion": "v2", "name": "child", "version": RC, "appVersion": RC,
            }),
            "child/values.yaml": charts.encoded({
                "image": {"repository": "nvcr.io/example/components-dev/service", "tag": RC},
            }),
            "child/templates/pod.yaml": (
                b"apiVersion: v1\nkind: Pod\nmetadata:\n  name: child\n"
                b"spec:\n  containers:\n    - name: child\n"
                b"      image: {{ .Values.image.repository }}:{{ .Values.image.tag }}\n"
            ),
        }),
        "service/README.md": b"release notes\n",
    }
    return packed(files or default)


def duplicate_file(data, path, content=None, mode=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as source:
        with tarfile.open(fileobj=output, mode="w:gz") as target:
            for member in source:
                target.addfile(member, source.extractfile(member) if member.isfile() else None)
            duplicate = copy.deepcopy(source.getmember(path))
            payload = source.extractfile(duplicate).read() if content is None else content
            duplicate.size = len(payload)
            if mode is not None:
                duplicate.mode = mode
            target.addfile(duplicate, io.BytesIO(payload))
    return output.getvalue()


def evidence(data):
    return {
        "schema": 1, "repository": "example/product", "rc_version": RC, "commit": COMMIT,
        "run_id": 12, "run_attempt": 1, "source_workflow": ".github/workflows/release-rc.yml",
        "source_ngc_path": "example/components-dev",
        "images": [{"name": "service", "digest": IMAGE}],
        "charts": [{"name": "service", "digest": charts.digest(data),
                    "version_value_paths": [["image", "tag"]]}],
    }


class MemoryRegistry:
    def __init__(self, data):
        self.source_chart = data
        self.source_image = IMAGE
        self.images = {}
        self.charts = {}
        self.writes = []
        self.fail_chart = False
        self.bad_copy = False
        self.fail_read = False
        self.reads = []

    def image_digest(self, role, name, version, absent=False):
        p.require(role == ("source" if version == RC else "destination"), "wrong image read role")
        self.reads.append(("image", role))
        if self.fail_read:
            raise p.PromotionError("registry unavailable")
        if version == RC:
            return self.source_image
        value = self.images.get((name, version))
        if value is None and not absent:
            raise p.PromotionError("missing image")
        return value

    def verify_image(self, name, digest, commit):
        p.require(digest == self.source_image and commit == COMMIT, "wrong image provenance")

    def copy_image(self, name, digest, stable):
        self.writes.append(("image", name, digest, stable))
        self.images[name, stable] = "sha256:" + "c" * 64 if self.bad_copy else digest

    def chart(self, role, name, version, absent=False):
        p.require(role == ("source" if version == RC else "destination"), "wrong chart read role")
        self.reads.append(("chart", role))
        if version == RC:
            return self.source_chart
        value = self.charts.get((name, version))
        if value is None and not absent:
            raise p.PromotionError("missing chart")
        return value

    def chart_destination(self, name):
        pass

    def publish_chart(self, name, stable, data):
        if self.fail_chart:
            raise p.PromotionError("chart upload failed")
        self.writes.append(("chart", name, stable))
        self.charts[name, stable] = data


class MemoryGitHub:
    def __init__(self):
        self.reservation = None
        self.reports = []
        self.finished = False
        self.rc_commit = COMMIT

    def reserve(self, stable, commit, intent):
        if self.reservation:
            p.require(self.reservation == intent, "different promotion intent")
        self.reservation = copy.deepcopy(intent)
        return {"tag_name": f"v{stable}"}

    def upload(self, release, name, report):
        self.reports.append((name, copy.deepcopy(report)))

    def tag(self, name):
        return self.rc_commit

    def finish(self, release, commit):
        self.finished = True


class VersionTests(unittest.TestCase):
    def test_exact_rc(self):
        for value, stable in [("0.0.0-rc.1", "0.0.0"), (RC, "1.2.3"), ("12.34.56-rc.123", "12.34.56")]:
            self.assertEqual(p.version(value), stable)

    def test_noncanonical_versions(self):
        for value in ["v" + RC, "1.2.3", "1.2.3-rc.0", "01.2.3-rc.1", "1.2.3-rc.01",
                      RC + "\n", RC + "+build", "1.2.3-beta.1", "../1.2.3-rc.1", "", None]:
            with self.subTest(value=value), self.assertRaises(p.PromotionError):
                p.version(value)


class PolicyTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(p.policy(config()), config())

    def test_charts_reject_namespace_changes_including_no_declared_version_paths(self):
        for paths in ([["image", "tag"]], []):
            candidate = config()
            candidate["destination_ngc_path"] = "example/components"
            candidate["charts"][0]["version_value_paths"] = paths
            with self.subTest(paths=paths), self.assertRaisesRegex(
                p.PromotionError, "identical source and destination NGC paths"
            ):
                p.policy(candidate)

    def test_image_only_promotion_allows_distinct_namespaces(self):
        candidate = config()
        candidate["destination_ngc_path"] = "example/components"
        candidate["charts"] = []
        self.assertEqual(p.policy(candidate), candidate)

    def test_disabled_or_unknown_inputs(self):
        for field, value in [("enabled", False), ("enabled", "true"), ("schema", True),
                             ("environment", "ungated"), ("source_ngc_path", "../escape"),
                             ("source_workflow", "../release.yml"),
                             ("source_publisher_sha", "main"),
                             ("source_publisher_sha", "v1.2.3"),
                             ("images", ["service", "service"]),
                             ("images", ["service;command"]), ("competing_workflows", ["../x"])]:
            with self.subTest(field=field):
                value_ = config()
                value_[field] = value
                with self.assertRaises(p.PromotionError):
                    p.policy(value_)

    def test_bad_value_paths(self):
        for value in [[[]], [["image", "tag"], ["image", "tag"]], [["a.b"]], [["x\n"]]]:
            with self.subTest(value=value), self.assertRaises(p.PromotionError):
                p.value_paths(value)

    def test_deployment_values_are_not_part_of_the_promotion_policy(self):
        candidate = config()
        candidate["charts"][0]["render_values"] = {}
        with self.assertRaises(p.PromotionError):
            p.policy(candidate)

    def test_incomplete_manifest(self):
        data = archive()
        original = evidence(data)
        run = {"id": 12, "run_attempt": 1}
        self.assertEqual(p.manifest(original, config(), "example/product", RC, COMMIT, run), original)
        for field, value in [("images", []), ("charts", []), ("run_id", 99),
                             ("run_attempt", 2), ("repository", "fork/product"),
                             ("commit", "b" * 40), ("source_ngc_path", "other/team")]:
            candidate = copy.deepcopy(original)
            candidate[field] = value
            with self.subTest(field=field), self.assertRaises(p.PromotionError):
                p.manifest(candidate, config(), "example/product", RC, COMMIT, run)

    def test_manifest_unexpected_duplicate_and_changed_chart_policy(self):
        for change in ("extra", "duplicate", "paths"):
            candidate = evidence(archive())
            if change == "extra":
                candidate["images"].append({"name": "unexpected", "digest": IMAGE})
            elif change == "duplicate":
                candidate["images"] *= 2
            else:
                candidate["charts"][0]["version_value_paths"] = []
            with self.subTest(change=change), self.assertRaises(p.PromotionError):
                p.manifest(candidate, config(), "example/product", RC, COMMIT,
                           {"id": 12, "run_attempt": 1})


class ChartTests(unittest.TestCase):
    def test_promotion_preserves_helm_scalar_semantics_comments_and_unrelated_aliases(self):
        cases = {
            "rawNo": ("no", False), "rawOff": ("off", False),
            "rawOn": ("on", True), "rawYes": ("yes", True),
            "quotedNo": ('"no"', "no"), "quotedOff": ("'off'", "off"),
            "quotedOn": ('"on"', "on"), "quotedYes": ("'yes'", "yes"),
        }
        values = "# keep values comment\n" + "".join(
            f"{key}:\n  enabled: {literal} # keep {key}\n"
            for key, (literal, _) in cases.items()
        ) + (
            "fileMode: 0644 # keep octal mode\n"
            "quotedMode: '0644'\n"
            "settings: &settings\n  mode: 0644\n  enabled: off\n"
            "settingsCopy: *settings # keep unrelated alias\n"
            f"image:\n  repository: nvcr.io/example/components-dev/service\n  tag: {RC}\n"
        )
        source = packed({
            "service/Chart.yaml": (
                "# keep chart comment\napiVersion: v2\nname: service\n"
                f"version: {RC}\nappVersion: {RC}\nannotations:\n"
                f"  dsx.nvidia.com/source-revision: {COMMIT}\n"
            ).encode(),
            "service/values.yaml": values.encode(),
            "service/templates/configmap.yaml": (
                b"apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: scalar-contract\n"
                b"data:\n  values: {{ .Values | toJson | quote }}\n"
            ),
        })

        def rendered_values(data):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "service.tgz"
                path.write_bytes(data)
                result = subprocess.run(
                    ["helm", "template", "scalar-contract", str(path)],
                    capture_output=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(charts.yaml_object(result.stdout)["data"]["values"])

        before = rendered_values(source)
        for key, (_, expected) in cases.items():
            with self.subTest(key=key):
                self.assertEqual(before[key]["enabled"], expected)
                self.assertIs(type(before[key]["enabled"]), type(expected))
        self.assertEqual(before["fileMode"], 0o644)
        self.assertEqual(before["quotedMode"], "0644")
        self.assertEqual(before["settings"], {"mode": 0o644, "enabled": False})
        self.assertEqual(before["settingsCopy"], before["settings"])

        changed = charts.promote(source, "service", RC, "1.2.3", COMMIT,
                                 [["image", "tag"]], IMAGE, DESTINATIONS)
        expected = copy.deepcopy(before)
        expected["image"]["tag"] = "1.2.3"
        self.assertEqual(rendered_values(changed), expected)
        after = charts.unpack(changed, "service")
        self.assertIn(b"# keep chart comment", after["service/Chart.yaml"][0])
        for preserved in [b"# keep values comment", b"# keep octal mode",
                          b"# keep unrelated alias", b"&settings", b"*settings"]:
            self.assertIn(preserved, after["service/values.yaml"][0])
        for key in cases:
            self.assertIn(f"# keep {key}".encode(), after["service/values.yaml"][0])
        self.assertEqual(changed, charts.promote(source, "service", RC, "1.2.3", COMMIT,
                                               [["image", "tag"]], IMAGE, DESTINATIONS))

    def test_declared_tag_alias_cannot_change_an_undeclared_field(self):
        files = {name: content for name, (content, _) in charts.unpack(archive(), "service").items()}
        files["service/values.yaml"] = (
            "image: &sharedImage\n"
            "  repository: nvcr.io/example/components-dev/service\n"
            f"  tag: {RC}\n"
            "undeclaredImage: *sharedImage\n"
        ).encode()
        source = packed(files)
        original = charts.yaml_object(files["service/values.yaml"])
        self.assertEqual(original["undeclaredImage"]["tag"], RC)
        with self.assertRaises(charts.InvalidChart):
            charts.promote(source, "service", RC, "1.2.3", COMMIT,
                           [["image", "tag"]], IMAGE, DESTINATIONS)
        registry = MemoryRegistry(source)
        github = MemoryGitHub()
        with self.assertRaises(charts.InvalidChart):
            p.promote(config(), evidence(source), github, registry, "23-1")
        self.assertEqual(registry.writes, [])
        self.assertIsNone(github.reservation)

    def test_metadata_only_promotion_and_deterministic_bytes(self):
        source = archive()
        before = charts.unpack(source, "service")
        changed = charts.promote(source, "service", RC, "1.2.3", COMMIT,
                                 [["image", "tag"]], IMAGE, DESTINATIONS)
        self.assertNotEqual(charts.digest(source), charts.digest(changed))
        self.assertEqual(changed, charts.promote(source, "service", RC, "1.2.3", COMMIT,
                                               [["image", "tag"]], IMAGE, DESTINATIONS))
        after = charts.unpack(changed, "service")
        self.assertEqual(set(before), set(after))
        for path in before:
            if path not in {"service/Chart.yaml", "service/values.yaml"}:
                self.assertEqual(before[path], after[path], path)
        metadata = charts.yaml_object(after["service/Chart.yaml"][0])
        previous = charts.yaml_object(before["service/Chart.yaml"][0])
        self.assertEqual(metadata["version"], "1.2.3")
        self.assertEqual(metadata["appVersion"], "1.2.3")
        self.assertEqual(metadata["dependencies"], previous["dependencies"])
        self.assertEqual(metadata["annotations"]["dsx.nvidia.com/source-revision"], COMMIT)
        self.assertEqual(metadata["annotations"]["dsx.nvidia.com/source-chart-digest"], charts.digest(source))
        self.assertEqual(metadata["annotations"]["existing"], "kept")
        values = charts.yaml_object(after["service/values.yaml"][0])
        self.assertEqual(values["image"]["tag"], "1.2.3")
        self.assertEqual(values["unrelated"], RC)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "service-1.2.3.tgz"
            path.write_bytes(changed)
            result = subprocess.run(["helm", "show", "chart", str(path)], capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(charts.yaml_object(result.stdout)["version"], "1.2.3")

    def test_render_retains_vendored_rc_image_and_root_repository(self):
        changed = charts.promote(archive(), "service", RC, "1.2.3", COMMIT,
                                 [["image", "tag"]], IMAGE, DESTINATIONS)
        after = charts.unpack(changed, "service")
        values = charts.yaml_object(after["service/values.yaml"][0])
        self.assertEqual(values["image"]["repository"], "nvcr.io/example/components-dev/service")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "service-1.2.3.tgz"
            path.write_bytes(changed)
            result = subprocess.run(
                ["helm", "template", "promoted", str(path)], capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            references = [line.strip() for line in result.stdout.decode().splitlines()
                          if line.strip().startswith("image:")]
            self.assertCountEqual(references, [
                "image: nvcr.io/example/components-dev/service:1.2.3",
                f"image: nvcr.io/example/components-dev/service:{RC}",
            ])

    def test_missing_or_wrong_version_path(self):
        for paths in [[["missing"]], [["image", "repository"]], [["image", "tag", "child"]]]:
            with self.subTest(paths=paths), self.assertRaises(charts.InvalidChart):
                charts.promote(archive(), "service", RC, "1.2.3", COMMIT, paths, IMAGE, DESTINATIONS)

    def test_deployment_inputs_and_app_version_templates_are_preserved_without_rendering(self):
        files = {name: content for name, (content, _) in charts.unpack(archive(), "service").items()}
        files["service/templates/required.yaml"] = b'{{ required "deployment input required" .Values.endpoint }}'
        files["service/templates/pod.yaml"] = files["service/templates/pod.yaml"].replace(
            b".Values.image.tag", b".Chart.AppVersion",
        )
        values = charts.yaml_object(files["service/values.yaml"])
        values["image"]["repository"] = "registry.example.com/service"
        files["service/values.yaml"] = charts.encoded(values)
        source = packed(files)
        run = subprocess.run

        def static_tools_only(args, **kwargs):
            self.assertEqual(args[0], "yq")
            return run(args, **kwargs)

        with patch("subprocess.run", side_effect=static_tools_only):
            promoted = charts.promote(source, "service", RC, "1.2.3", COMMIT,
                                      [], IMAGE, DESTINATIONS)
        after = charts.unpack(promoted, "service")
        for name, content in files.items():
            if name != "service/Chart.yaml":
                self.assertEqual(after[name][0], content, name)

    def test_disabled_template_still_validates_each_declared_repository(self):
        files = {name: content for name, (content, _) in charts.unpack(archive(), "service").items()}
        values = charts.yaml_object(files["service/values.yaml"])
        values["enabled"] = False
        values["image"]["repository"] = "nvcr.io/example/components-dev/not-promoted"
        files["service/values.yaml"] = charts.encoded(values)
        files["service/templates/pod.yaml"] = (
            b"{{ if .Values.enabled }}\n" + files["service/templates/pod.yaml"] + b"{{ end }}\n"
        )
        with self.assertRaisesRegex(charts.InvalidChart, "declared chart image is not a verified"):
            charts.promote(packed(files), "service", RC, "1.2.3", COMMIT,
                           [["image", "tag"]], IMAGE, DESTINATIONS)

    def test_declared_image_mapping_supports_separate_registry_and_rejects_unknown_shapes(self):
        original = {name: content for name, (content, _) in charts.unpack(archive(), "service").items()}
        for image, valid in [
            ({"repository": "example/components-dev/service", "registry": "nvcr.io", "tag": RC}, True),
            ({"repository": "example/components-dev/service", "registry": "other.invalid", "tag": RC}, False),
            ({"tag": RC}, False),
            ({"repository": "nvcr.io/example/components-dev/service", "registry": None, "tag": RC}, False),
        ]:
            with self.subTest(image=image):
                files = copy.deepcopy(original)
                values = charts.yaml_object(files["service/values.yaml"])
                values["image"] = image
                files["service/values.yaml"] = charts.encoded(values)
                files["service/templates/pod.yaml"] = files["service/templates/pod.yaml"].replace(
                    b".Values.image.repository }}",
                    b".Values.image.registry }}/{{ .Values.image.repository }}",
                )
                if valid:
                    charts.promote(packed(files), "service", RC, "1.2.3", COMMIT,
                                   [["image", "tag"]], IMAGE, DESTINATIONS)
                else:
                    with self.assertRaises(charts.InvalidChart):
                        charts.promote(packed(files), "service", RC, "1.2.3", COMMIT,
                                       [["image", "tag"]], IMAGE, DESTINATIONS)

    def test_identical_duplicate_files_canonicalize_and_keep_dependency_bytes(self):
        original = archive()
        source = duplicate_file(original, "service/templates/pod.yaml")
        source = duplicate_file(source, "service/Chart.yaml")
        before = charts.unpack(original, "service")
        self.assertEqual(charts.unpack(source, "service"), before)
        promoted = charts.promote(source, "service", RC, "1.2.3", COMMIT,
                                  [["image", "tag"]], IMAGE, DESTINATIONS)
        with tarfile.open(fileobj=io.BytesIO(promoted), mode="r:gz") as archive_:
            paths = archive_.getnames()
        self.assertEqual(len(paths), len(set(paths)))
        after = charts.unpack(promoted, "service")
        self.assertEqual(after["service/charts/child-1.2.3-rc.7.tgz"],
                         before["service/charts/child-1.2.3-rc.7.tgz"])
        metadata = charts.yaml_object(after["service/Chart.yaml"][0])
        self.assertEqual(metadata["annotations"]["dsx.nvidia.com/source-chart-digest"],
                         charts.digest(source))

    def test_duplicate_content_and_exact_mode_conflicts_are_rejected(self):
        for content, mode in [(b"different", None), (None, 0o600), (None, 0o4644)]:
            with self.subTest(content=content, mode=mode):
                source = duplicate_file(archive(), "service/Chart.yaml", content, mode)
                with self.assertRaisesRegex(charts.InvalidChart, "conflicting duplicate"):
                    charts.unpack(source, "service")

    def test_duplicate_occurrences_count_against_entry_and_expanded_size_limits(self):
        original = archive()
        files = charts.unpack(original, "service")
        source = duplicate_file(original, "service/Chart.yaml")
        with patch.object(charts, "MAX_FILES", len(files)):
            with self.assertRaisesRegex(charts.InvalidChart, "excessive chart archive entries"):
                charts.unpack(source, "service")
        limit = max(len(source), sum(len(content) for content, _ in files.values()))
        with patch.object(charts, "MAX_ARCHIVE", limit):
            with self.assertRaisesRegex(charts.InvalidChart, "expanded chart exceeds"):
                charts.unpack(source, "service")

    def test_wrong_source_revision_or_version(self):
        for rc, commit in [(RC, "b" * 40), ("1.2.3-rc.8", COMMIT)]:
            with self.subTest(rc=rc), self.assertRaises(charts.InvalidChart):
                charts.promote(archive(), "service", rc, "1.2.3", commit, [], IMAGE, DESTINATIONS)

    def test_unsafe_paths_and_links(self):
        for name in ["../Chart.yaml", "/service/Chart.yaml", "service/../Chart.yaml",
                     "service/./Chart.yaml", "other/Chart.yaml", "service\\Chart.yaml"]:
            with self.subTest(name=name), self.assertRaises(charts.InvalidChart):
                charts.unpack(archive({name: b"bad"}), "service")
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as tar:
            info = tarfile.TarInfo("service/Chart.yaml")
            info.type = tarfile.SYMTYPE
            info.linkname = "../../outside"
            tar.addfile(info)
        with self.assertRaises(charts.InvalidChart):
            charts.unpack(data.getvalue(), "service")


class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.data = archive()
        self.evidence = evidence(self.data)
        self.registry = MemoryRegistry(self.data)
        self.github = MemoryGitHub()

    def promote(self, attempt="23-1", path=None):
        return p.promote(config(), self.evidence, self.github, self.registry, attempt, path)

    def test_complete_promotion_and_matching_rerun(self):
        first = self.promote()
        self.assertEqual(first["status"], "completed")
        self.assertTrue(self.github.finished)
        self.assertEqual(self.registry.images["service", "1.2.3"], IMAGE)
        self.assertEqual(len(self.registry.writes), 2)
        self.assertEqual(self.promote("23-2")["intent"], first["intent"])
        self.assertEqual(len(self.registry.writes), 2)

    def test_preflight_and_verification_reads_use_explicit_roles(self):
        expected = [
            ("image", "source"), ("image", "destination"),
            ("chart", "source"), ("chart", "destination"),
            ("image", "destination"), ("image", "destination"),
            ("chart", "destination"), ("chart", "destination"),
        ]
        self.promote()
        self.assertEqual(self.registry.reads, expected)
        self.registry.reads.clear()
        self.promote("23-2")
        self.assertEqual(self.registry.reads, expected)

    def test_destination_read_failure_stops_before_any_write(self):
        for method in ("image_digest", "chart"):
            with self.subTest(method=method):
                self.setUp()
                original = getattr(self.registry, method)

                def read(role, *args, **kwargs):
                    if role == "destination":
                        raise p.PromotionError("destination read denied")
                    return original(role, *args, **kwargs)

                with patch.object(self.registry, method, side_effect=read):
                    with self.assertRaisesRegex(p.PromotionError, "destination read denied"):
                        self.promote()
                self.assertIsNone(self.github.reservation)
                self.assertEqual(self.registry.writes, [])

    def test_cross_namespace_charts_fail_before_any_io(self):
        candidate = config()
        candidate["destination_ngc_path"] = "example/components"
        with self.assertRaisesRegex(p.PromotionError, "identical source and destination NGC paths"):
            p.promote(candidate, self.evidence, None, None, "23-1")

    def test_unpromoted_chart_repository_fails_before_any_write(self):
        for disabled in (False, True):
            with self.subTest(disabled=disabled):
                files = {name: content for name, (content, _) in charts.unpack(archive(), "service").items()}
                values = charts.yaml_object(files["service/values.yaml"])
                values["image"]["repository"] = "nvcr.io/example/components-dev/not-promoted"
                files["service/values.yaml"] = charts.encoded(values)
                candidate = config()
                if disabled:
                    files["service/templates/pod.yaml"] = (
                        b"{{ if false }}\n" + files["service/templates/pod.yaml"] + b"{{ end }}\n"
                    )
                source = packed(files)
                proof = evidence(source)
                proof["charts"][0]["version_value_paths"] = candidate["charts"][0]["version_value_paths"]
                registry = MemoryRegistry(source)
                github = MemoryGitHub()
                with self.assertRaisesRegex(charts.InvalidChart, "not a verified promotion destination"):
                    p.promote(candidate, proof, github, registry, "23-1")
                self.assertEqual(registry.writes, [])
                self.assertIsNone(github.reservation)

    def test_original_compressed_digest_is_verified_before_duplicate_normalization(self):
        self.registry.source_chart = duplicate_file(self.data, "service/Chart.yaml")
        with patch.object(charts, "unpack") as unpack:
            with self.assertRaisesRegex(p.PromotionError, "RC chart changed after verification"):
                self.promote()
            unpack.assert_not_called()
        self.assertEqual(self.registry.writes, [])
        self.assertIsNone(self.github.reservation)

    def test_partial_failure_retains_intent_and_resumes(self):
        self.registry.fail_chart = True
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "result.json"
            with self.assertRaises(p.PromotionError):
                self.promote(path=report)
            saved = json.loads(report.read_text())
            self.assertEqual(saved["status"], "partial")
            self.assertEqual(len(saved["artifacts"]), 1)
        self.assertFalse(self.github.finished)
        self.assertIsNotNone(self.github.reservation)
        self.assertEqual(self.github.reports[-1][1]["status"], "partial")
        self.registry.fail_chart = False
        self.assertEqual(self.promote("23-2")["status"], "completed")
        self.assertEqual([item[0] for item in self.registry.writes], ["image", "chart"])

    def test_preflight_failure_never_reserves_or_writes(self):
        for failure in ["source_image", "source_chart", "stable_image", "stable_chart", "registry"]:
            with self.subTest(failure=failure):
                self.setUp()
                if failure == "source_image":
                    self.registry.source_image = "sha256:" + "c" * 64
                elif failure == "source_chart":
                    self.registry.source_chart = b"changed"
                elif failure == "stable_image":
                    self.registry.images["service", "1.2.3"] = "sha256:" + "c" * 64
                elif failure == "stable_chart":
                    self.registry.charts["service", "1.2.3"] = self.data
                else:
                    self.registry.fail_read = True
                with self.assertRaises(p.PromotionError):
                    self.promote()
                self.assertIsNone(self.github.reservation)
                self.assertFalse(self.registry.writes)

    def test_digest_conversion_never_finishes_release(self):
        self.registry.bad_copy = True
        with self.assertRaises(p.PromotionError):
            self.promote()
        self.assertFalse(self.github.finished)
        self.assertFalse(self.registry.charts)

    def test_rc_tag_movement_never_finishes_release(self):
        self.github.rc_commit = "b" * 40
        with self.assertRaises(p.PromotionError):
            self.promote()
        self.assertFalse(self.github.finished)

    def test_conflicting_reserved_intent_rejects_without_write(self):
        self.github.reservation = {"other": "RC"}
        with self.assertRaises(p.PromotionError):
            self.promote()
        self.assertFalse(self.registry.writes)


class CaptureTests(unittest.TestCase):
    def test_capture_does_not_read_default_branch_policy_or_activation(self):
        data = archive()
        expected = evidence(data)
        env = {
            "PATH": os.environ["PATH"],
            "RC_IMAGES": json.dumps([{"name": "service"}]),
            "RC_CHARTS": json.dumps([{"name": "service", "versionValuePaths": [["image", "tag"]]}]),
            "RC_NGC_PATH": "example/components-dev", "RC_VERSION": RC,
            "GITHUB_REPOSITORY": "example/product", "GITHUB_SHA": COMMIT,
            "GITHUB_RUN_ID": "12", "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_WORKFLOW_REF": "example/product/.github/workflows/release-rc.yml@refs/heads/release/1.2.3",
        }
        with patch.dict(os.environ, env, clear=True), \
                patch.object(sys, "argv", ["promotion.py", "capture", "--policy", "missing.json"]), \
                patch.object(p, "authenticate_registries") as auth, \
                patch.object(p, "Registry", return_value=MemoryRegistry(data)), \
                patch.object(Path, "read_text", side_effect=AssertionError("capture read a policy")), \
                patch.object(Path, "write_bytes") as write:
            p.main()
        self.assertTrue(auth.call_args.kwargs["capture_only"])
        self.assertEqual(json.loads(write.call_args.args[0]), expected)

    def test_captures_exact_inventory_without_publishing(self):
        data = archive()
        expected = evidence(data)
        context = {key: expected[key] for key in (
            "repository", "rc_version", "commit", "run_id", "run_attempt", "source_workflow",
        )}
        registry = MemoryRegistry(data)
        self.assertEqual(p.capture(config(), registry, context), expected)
        self.assertEqual(registry.writes, [])
        self.assertEqual(registry.reads, [("image", "source"), ("chart", "source")])

    def test_changed_chart_metadata_prevents_capture(self):
        context = {
            "repository": "example/product", "rc_version": RC, "commit": COMMIT,
            "run_id": 12, "run_attempt": 1, "source_workflow": ".github/workflows/release-rc.yml",
        }
        registry = MemoryRegistry(archive())
        registry.source_chart = archive({"service/Chart.yaml": b"name: wrong\n"})
        with self.assertRaises(charts.InvalidChart):
            p.capture(config(), registry, context)
        self.assertEqual(registry.writes, [])


class GateTests(unittest.TestCase):
    def setUp(self):
        self.github = p.GitHub("example/product")
        self.context = {"event": "workflow_dispatch", "ref": "refs/heads/main",
                        "sha": COMMIT, "protected": "true"}
        self.rules = {
            "can_admins_bypass": False,
            "deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True},
            "protection_rules": [{"type": "required_reviewers", "prevent_self_review": True,
                                  "reviewers": [{"type": "Team", "reviewer": {"slug": "example-team"}}]}],
        }
        self.workflow = {"state": "disabled_manually"}
        self.definition = {
            "concurrency": {"group": "dsx-stable-release-${{ github.repository }}",
                            "cancel-in-progress": False},
            "jobs": {"release": {"if": "vars.DSX_RELEASE_MODE != 'rc-promotion'"}},
        }
        self.branch = {"protected": True, "commit": {"sha": COMMIT}}
        self.policies = [{"name": "main", "type": "branch"}]
        self.github.request = self.request
        self.github.pages = lambda *args: self.policies

    def request(self, path, *args, **kwargs):
        if path.endswith("/product"):
            return {"default_branch": "main"}
        if "/branches/" in path:
            return self.branch
        if "/environments/" in path:
            return self.rules
        if "/actions/workflows/" in path:
            return self.workflow
        if "/contents/" in path:
            import base64
            return {"encoding": "base64",
                    "content": base64.b64encode(json.dumps(self.definition).encode()).decode()}
        raise AssertionError(path)

    def test_valid_gate(self):
        self.github.gates(config(), self.context)

    def test_reject_events_and_source_refs(self):
        for key, value in [("event", "push"), ("event", "pull_request"),
                           ("ref", "refs/heads/release/1.2.3"), ("protected", "false")]:
            context = {**self.context, key: value}
            with self.subTest(key=key, value=value), self.assertRaises(p.PromotionError):
                self.github.gates(config(), context)

    def test_reject_incomplete_environment_protection(self):
        for mode in ["none", "bypass", "self", "user", "extra_branch"]:
            with self.subTest(mode=mode):
                self.setUp()
                if mode == "none":
                    self.rules["protection_rules"] = []
                elif mode == "bypass":
                    self.rules["can_admins_bypass"] = True
                elif mode == "self":
                    self.rules["protection_rules"][0]["prevent_self_review"] = False
                elif mode == "user":
                    self.rules["protection_rules"][0]["reviewers"][0]["type"] = "User"
                else:
                    self.policies.append({"name": "release/*", "type": "branch"})
                with self.assertRaises(p.PromotionError):
                    self.github.gates(config(), self.context)

    def test_enabled_competing_release_and_stale_default(self):
        self.workflow["state"] = "active"
        self.definition["jobs"]["release"].pop("if")
        with self.assertRaises(p.PromotionError):
            self.github.gates(config(), self.context)
        self.workflow["state"] = "disabled_manually"
        self.branch["commit"]["sha"] = "b" * 40
        with self.assertRaises(p.PromotionError):
            self.github.gates(config(), self.context)

    def test_active_legacy_publisher_requires_common_guard_and_concurrency(self):
        self.workflow["state"] = "active"
        self.github.gates(config(), self.context)
        for section, value in [
            ("jobs", {"release": {"if": "true"}}),
            ("jobs", {"release": self.definition["jobs"]["release"], "unguarded": {}}),
            ("concurrency", {"group": "different", "cancel-in-progress": False}),
            ("concurrency", {"group": "dsx-stable-release-${{ github.repository }}",
                             "cancel-in-progress": True}),
        ]:
            before = copy.deepcopy(self.definition)
            self.definition[section] = value
            with self.subTest(section=section, value=value), self.assertRaises(p.PromotionError):
                self.github.gates(config(), self.context)
            self.definition = before


class ToolTests(unittest.TestCase):
    def test_image_reads_select_explicit_role_with_distinct_credentials(self):
        source = "example/components-dev"
        env = {"SOURCE_DOCKER_CONFIG": "/tmp/source-auth",
               "DESTINATION_DOCKER_CONFIG": "/tmp/destination-auth"}
        for destination in (source, "example/components"):
            registry = p.Registry(source, destination)
            for role, namespace in (("source", source), ("destination", destination)):
                with self.subTest(destination=destination, role=role), \
                        patch.dict(os.environ, env), patch.object(p, "command", side_effect=[
                            b'{"schemaVersion":2}', p.canonical({"digest": IMAGE}),
                        ]) as command:
                    self.assertEqual(registry.image_digest(role, "service", RC), IMAGE)
                    self.assertEqual(command.call_count, 2)
                    for call in command.call_args_list:
                        self.assertEqual(call.args[0][-1], f"nvcr.io/{namespace}/service:{RC}")
                        self.assertEqual(call.kwargs["env"]["DOCKER_CONFIG"],
                                         env[f"{role.upper()}_DOCKER_CONFIG"])
            with patch.dict(os.environ, env), patch.object(p, "command") as command:
                registry.verify_image("service", IMAGE, COMMIT)
                self.assertEqual(command.call_args.kwargs["env"]["DOCKER_CONFIG"],
                                 env["SOURCE_DOCKER_CONFIG"])

    def test_chart_reads_select_explicit_role_with_equal_namespaces(self):
        registry = p.Registry("example/components-dev", "example/components-dev")
        payload = b"verified chart bytes"
        for role in ("source", "destination"):
            alias = f"promotion-{role}"

            def response(args, **kwargs):
                if args[:3] == ["helm", "repo", "update"]:
                    self.assertEqual(args[3], alias)
                    return b""
                if args[:3] == ["helm", "search", "repo"]:
                    self.assertEqual(args[3], f"{alias}/service")
                    return p.canonical([{"name": f"{alias}/service", "version": RC}])
                self.assertEqual(args[:3], ["helm", "pull", f"{alias}/service"])
                (Path(args[-1]) / f"service-{RC}.tgz").write_bytes(payload)
                return b""

            with self.subTest(role=role), patch.object(p, "command", side_effect=response):
                self.assertEqual(registry.chart(role, "service", RC), payload)

    def test_registry_reads_reject_unknown_roles_before_io(self):
        registry = p.Registry("example/components-dev")
        with patch.object(p, "command") as command:
            for operation in (registry.image_digest, registry.chart):
                with self.subTest(operation=operation.__name__), self.assertRaises(p.PromotionError):
                    operation("example/components-dev", "service", RC)
            command.assert_not_called()

    def test_registry_authentication_writes_private_docker_credentials(self):
        for capture_only in (True, False):
            with self.subTest(capture_only=capture_only), tempfile.TemporaryDirectory() as directory:
                env = {
                    "PROMOTION_AUTH_ROOT": directory,
                    "RC_PROMOTION_SOURCE_NGC_KEY": "source-test-key",
                }
                credentials = {"SOURCE": "source-test-key"}
                if not capture_only:
                    env["RC_PROMOTION_DESTINATION_NGC_KEY"] = "destination-test-key"
                    credentials["DESTINATION"] = "destination-test-key"
                with patch.dict(os.environ, env, clear=True), patch.object(p, "command") as command:
                    p.authenticate_registries(config(), capture_only=capture_only)
                    if not capture_only:
                        self.assertEqual(command.call_count, 2)
                        for call, role in zip(command.call_args_list, ("SOURCE", "DESTINATION")):
                            self.assertEqual(call.args[0], [
                                "helm", "repo", "add", f"promotion-{role.lower()}",
                                "https://helm.ngc.nvidia.com/example/components-dev",
                                "--username", "$oauthtoken", "--password-stdin",
                            ])
                            self.assertEqual(call.kwargs["data"], (credentials[role] + "\n").encode())
                    for label, key in credentials.items():
                        path = Path(directory) / label.lower() / "config.json"
                        self.assertEqual(os.environ[f"{label}_DOCKER_CONFIG"], str(path.parent))
                        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                        self.assertEqual(json.loads(path.read_text()), {"auths": {"nvcr.io": {
                            "auth": base64.b64encode(f"$oauthtoken:{key}".encode()).decode(),
                        }}})

    def test_image_copy_preserves_all_manifests_from_the_verified_digest(self):
        env = {"SOURCE_DOCKER_CONFIG": "/tmp/source-auth",
               "DESTINATION_DOCKER_CONFIG": "/tmp/destination-auth"}
        with patch.dict(os.environ, env), patch.object(p, "command") as command:
            p.Registry("example/components-dev").copy_image("service", IMAGE, "1.2.3")
        command.assert_called_once_with([
            "skopeo", "copy", "--all", "--preserve-digests",
            "--src-authfile", "/tmp/source-auth/config.json",
            "--dest-authfile", "/tmp/destination-auth/config.json",
            f"docker://nvcr.io/example/components-dev/service@{IMAGE}",
            "docker://nvcr.io/example/components-dev/service:1.2.3",
        ])

    def test_registry_absence_is_narrow(self):
        registry = p.Registry("example/components-dev", "example/components")
        ref = "nvcr.io/example/components/service:1.2.3"
        env = {"DESTINATION_DOCKER_CONFIG": "/tmp/no-credentials"}
        with patch.dict(os.environ, env):
            for message, absent in [(f"ERROR: {ref}: not found", True),
                                    ("unauthorized: not found", False),
                                    ("proxy HTTP 404", False)]:
                result = subprocess.CompletedProcess([], 1, b"", message.encode())
                with patch("subprocess.run", return_value=result), self.subTest(message=message):
                    if absent:
                        self.assertIsNone(registry.image_digest("destination", "service", "1.2.3", True))
                    else:
                        with self.assertRaises(p.PromotionError):
                            registry.image_digest("destination", "service", "1.2.3", True)

    def test_registry_digest_uses_descriptor_not_display_newline(self):
        with patch.dict(os.environ, {"SOURCE_DOCKER_CONFIG": "/tmp/no-credentials"}):
            outputs = [
                subprocess.CompletedProcess([], 0, b'{"schemaVersion":2}\n', b""),
                subprocess.CompletedProcess([], 0, json.dumps({"digest": IMAGE}).encode(), b""),
            ]
            with patch("subprocess.run", side_effect=outputs):
                self.assertEqual(p.Registry("example/components-dev").image_digest(
                    "source", "service", RC), IMAGE)

    def test_warning_is_not_absence(self):
        result = subprocess.CompletedProcess([], 0, b"[]", b"cannot load index")
        with patch("subprocess.run", return_value=result), self.assertRaises(p.PromotionError):
            p.Registry("example/components-dev").chart("source", "service", RC, True)


if __name__ == "__main__":
    unittest.main()
