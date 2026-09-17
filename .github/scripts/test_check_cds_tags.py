# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from check_cds_tags import check_tags


def response(payload):
    return io.BytesIO(json.dumps(payload).encode())


def registry_error(status, payload):
    return HTTPError("https://ghcr.io/", status, "Registry error", {}, response(payload))


class ReleaseTagGuardTest(unittest.TestCase):
    def check(self, responses):
        with patch("check_cds_tags.urlopen", side_effect=responses), redirect_stdout(io.StringIO()):
            check_tags("ghcr.io/example/image", ["1.2.3", "sha-" + "a" * 40], "actor", "test-secret")

    def test_only_explicit_manifest_unknown_allows_publication(self):
        absent = {"errors": [{"code": "MANIFEST_UNKNOWN"}]}
        self.check([
            response({"token": "test-bearer"}),
            registry_error(404, absent),
            registry_error(404, absent),
        ])

    def test_existing_version_or_sha_blocks_publication(self):
        for existing in ("version", "sha"):
            with self.subTest(tag=existing):
                replies = [response({"token": "test-bearer"})]
                if existing == "sha":
                    replies.append(registry_error(404, {"errors": [{"code": "MANIFEST_UNKNOWN"}]}))
                replies.append(response({}))
                with self.assertRaisesRegex(RuntimeError, "already exists"):
                    self.check(replies)

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
