# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import hashlib
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from check_cds_tags import check_tags

MANIFEST = {"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json", "manifests": []}
TESTED_DIGEST = "sha256:" + hashlib.sha256(json.dumps(MANIFEST).encode()).hexdigest()
TAGS = ["1.2.3", "sha-" + "a" * 40]


def response(payload):
    return io.BytesIO(json.dumps(payload).encode())


def registry_error(status, payload):
    return HTTPError("https://ghcr.io/", status, "Registry error", {}, response(payload))


class ReleaseTagGuardTest(unittest.TestCase):
    def check(self, responses):
        with patch("check_cds_tags.urlopen", side_effect=responses):
            return check_tags("ghcr.io/example/image", TESTED_DIGEST, TAGS, "actor", "test-secret")

    def test_only_explicit_manifest_unknown_allows_publication(self):
        absent = {"errors": [{"code": "MANIFEST_UNKNOWN"}]}
        self.assertEqual(self.check([
            response({"token": "test-bearer"}),
            registry_error(404, absent),
            registry_error(404, absent),
        ]), TAGS)

    def test_retry_skips_matching_tags_and_returns_only_missing_tags(self):
        for existing in ([], [TAGS[0]], [TAGS[1]], TAGS):
            with self.subTest(existing=existing):
                replies = [response({"token": "test-bearer"})]
                for tag in TAGS:
                    replies.append(response(MANIFEST) if tag in existing else
                                   registry_error(404, {"errors": [{"code": "MANIFEST_UNKNOWN"}]}))
                self.assertEqual(self.check(replies), [tag for tag in TAGS if tag not in existing])

    def test_different_version_or_sha_digest_blocks_publication(self):
        for existing in ("version", "sha"):
            with self.subTest(tag=existing):
                replies = [response({"token": "test-bearer"})]
                if existing == "sha":
                    replies.append(response(MANIFEST))
                replies.append(response({}))
                with self.assertRaisesRegex(RuntimeError, "different digest"):
                    self.check(replies)

    def test_invalid_expected_digest_fails_before_registry_access(self):
        for digest in ("", "sha256:short", "sha256:" + "g" * 64):
            with self.subTest(digest=digest), patch("check_cds_tags.urlopen") as request:
                with self.assertRaisesRegex(RuntimeError, "Invalid tested image digest"):
                    check_tags("ghcr.io/example/image", digest, TAGS, "actor", "test-secret")
                request.assert_not_called()

    def test_authentication_errors_fail_closed(self):
        cases = [
            registry_error(401, {}), registry_error(403, {}),
            URLError("test-secret"), TimeoutError("test-secret"), ValueError("test-secret"),
            io.BytesIO(b"not json"), response({}), response([]), response({"token": 123}),
        ]
        for reply in cases:
            with self.subTest(reply=type(reply).__name__):
                with self.assertRaisesRegex(RuntimeError, "Cannot authenticate") as caught:
                    self.check([reply])
                self.assertNotIn("test-secret", str(caught.exception))

    def test_uncertain_manifest_responses_fail_closed(self):
        cases = [
            registry_error(code, {"errors": [{"code": "MANIFEST_UNKNOWN"}]})
            for code in (401, 403, 429, 500)
        ] + [
            registry_error(404, payload) for payload in (
                {}, [], {"errors": []}, {"errors": [None]},
                {"errors": [{"code": "NAME_UNKNOWN"}]},
                {"errors": [{"code": "MANIFEST_UNKNOWN"}, {"code": "DENIED"}]},
            )
        ] + [
            HTTPError("https://ghcr.io/", 404, "Not Found", {}, io.BytesIO(b"not json")),
            URLError("test-secret"), TimeoutError("test-secret"),
        ]
        for reply in cases:
            with self.subTest(reply=type(reply).__name__):
                with self.assertRaises(RuntimeError) as caught:
                    self.check([response({"token": "test-bearer"}), reply])
                self.assertNotIn("test-secret", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
