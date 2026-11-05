# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import base64
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import http.client
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import github_app as app


NOW = 1793836800
REPO = "example/product"
TOKEN = "test_installation_token_only"
KEY = "-----BEGIN RSA PRIVATE KEY-----\nTEST KEY ONLY\n-----END RSA PRIVATE KEY-----\n"
WRITE = {"contents": "write", "workflows": "write"}
READ = {"contents": "read", "actions": "read"}
ENV = {"RC_PROMOTION_APP_ID": "42", "RC_PROMOTION_APP_INSTALLATION_ID": "123",
       "RC_PROMOTION_APP_PRIVATE_KEY": KEY}


def expiry(offset=3600):
    return datetime.fromtimestamp(NOW + offset, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def installation():
    return {"id": 123, "app_id": 42, "account": {"login": "example"},
            "target_type": "Organization", "suspended_at": None,
            "permissions": {**WRITE, "actions": "read", "metadata": "read"}}


def minted(permissions=WRITE):
    return {"token": TOKEN, "expires_at": expiry(),
            "permissions": {**permissions, "metadata": "read"}}


def repositories():
    return {"total_count": 1, "repositories": [
        {"id": 12, "name": "product", "full_name": REPO, "owner": {"login": "example"}},
    ]}


class AppTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.key_paths = []
        self.installation = installation()
        self.token = minted()
        self.repositories = repositories()
        self.failures = {}
        self.statuses = {}
        self.responses = []
        self.connections = []
        self.environment = patch.dict(os.environ, ENV, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        # Do not patch global time.time used by subprocess machinery.
        clock = patch.object(app, "time", SimpleNamespace(time=lambda: NOW))
        clock.start()
        self.addCleanup(clock.stop)
        transport = patch.object(app.http.client, "HTTPSConnection", side_effect=self.connect)
        self.transport = transport.start()
        self.addCleanup(transport.stop)
        signer = patch.object(app.subprocess, "run", side_effect=self.sign)
        self.signer = signer.start()
        self.addCleanup(signer.stop)

    def sign(self, args, **kwargs):
        flag = "-in" if args[1] == "rsa" else "-sign"
        path = Path(args[args.index(flag) + 1])
        self.key_paths.append(path)
        self.assertEqual(path.read_text(), KEY)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertNotIn(KEY, repr(args))
        self.assertNotIn(TOKEN, repr(args))
        self.assertEqual(args[0], "openssl")
        self.assertEqual(args[-2:], ["-passin", "pass:"])
        self.assertEqual(set(kwargs["env"]), {"PATH", "LC_ALL"})
        self.assertEqual(kwargs["timeout"], app.TIMEOUT)
        self.assertTrue(kwargs["capture_output"])
        self.assertFalse(kwargs["check"])
        if args[1] == "dgst":
            self.assertEqual(args[2], "-sha256")
        return subprocess.CompletedProcess(args, 0, b"s" * 256, b"")

    def connect(self, host, **kwargs):
        self.assertEqual(host, "api.github.com")
        self.assertEqual(kwargs, {"timeout": app.TIMEOUT})
        connection = Mock()
        self.connections.append(connection)

        def request(method, path, *, body, headers):
            self.assertTrue(all(not key.exists() for key in self.key_paths))
            self.requests.append({"method": method, "path": path, "body": body, "headers": headers})
            response = Mock()
            self.responses.append(response)
            response.status = self.statuses.get((method, path), {"GET": 200, "POST": 201, "DELETE": 204}[method])
            value = {
                ("GET", f"/repos/{REPO}/installation"): self.installation,
                ("POST", "/app/installations/123/access_tokens"): self.token,
                ("GET", "/installation/repositories?per_page=2&page=1"): self.repositories,
                ("DELETE", "/installation/token"): None,
            }[(method, path)]
            raw = value if isinstance(value, bytes) else json.dumps(value).encode()
            response.read.side_effect = lambda limit: raw[:limit]
            failure = self.failures.get((method, path))
            if failure:
                connection.getresponse.side_effect = failure
            else:
                connection.getresponse.return_value = response

        connection.request.side_effect = request
        return connection

    def assert_revoked(self):
        self.assertEqual(self.requests[-1]["method"], "DELETE")
        self.assertEqual(self.requests[-1]["path"], "/installation/token")
        self.assertEqual(self.requests[-1]["headers"]["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(sum(item["method"] == "DELETE" for item in self.requests), 1)

    def assert_closed(self):
        for connection in self.connections:
            connection.close.assert_called_once_with()
        for path in self.key_paths:
            self.assertFalse(path.exists())

    def test_write_token_is_scoped_to_one_repository_and_revoked(self):
        with app.installation_token(REPO, mode="write") as token:
            self.assertEqual(token, TOKEN)
            self.assertEqual(len(self.requests), 3)
        self.assertEqual(json.loads(self.requests[1]["body"]),
                         {"repositories": ["product"], "permissions": WRITE})
        self.assert_revoked()
        self.assert_closed()

    def test_read_mode_is_default_and_does_not_request_write_permissions(self):
        self.token = minted(READ)
        with app.installation_token(REPO) as token:
            self.assertEqual(token, TOKEN)
        self.assertEqual(json.loads(self.requests[1]["body"]),
                         {"repositories": ["product"], "permissions": READ})
        self.assert_revoked()

    def test_explicit_arguments_override_environment_without_mutating_it(self):
        before = dict(os.environ)
        with app.installation_token(REPO, mode="write", app_id=42, installation_id=123,
                                    private_key=KEY) as token:
            self.assertEqual(token, TOKEN)
        self.assertEqual(dict(os.environ), before)
        self.assert_revoked()

    def test_no_token_key_or_jwt_is_printed(self):
        output, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            with app.installation_token(REPO, mode="write"):
                pass
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "")

    def test_rsa_jwt_has_fixed_algorithm_issuer_and_bounded_lifetime(self):
        with app.installation_token(REPO, mode="write"):
            pass
        jwt = self.requests[0]["headers"]["Authorization"].removeprefix("Bearer ")
        header, payload, signature = jwt.split(".")
        decode = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        self.assertEqual(json.loads(decode(header)), {"alg": "RS256", "typ": "JWT"})
        claims = json.loads(decode(payload))
        self.assertEqual(claims, {"iat": NOW - 60, "exp": NOW + 540, "iss": "42"})
        self.assertLessEqual(claims["exp"] - claims["iat"], 600)
        self.assertEqual(decode(signature), b"s" * 256)
        self.assertEqual(self.requests[1]["headers"]["Authorization"], "Bearer " + jwt)
        self.assertEqual(self.requests[2]["headers"]["Authorization"], "Bearer " + TOKEN)
        self.assert_closed()

    def test_wrong_installation_app_account_or_suspension_prevents_minting(self):
        for change in ({"id": 124}, {"id": "123"}, {"app_id": 43}, {"app_id": "42"},
                       {"account": {"login": "elsewhere"}}, {"account": None},
                       {"target_type": "Enterprise"}, {"suspended_at": "2026-01-01T00:00:00Z"},
                       {"permissions": {}}, {"permissions": {"contents": "write"}}):
            with self.subTest(change=change):
                self.requests.clear()
                self.installation = {**installation(), **change}
                with self.assertRaises(app.GitHubAppError):
                    with app.installation_token(REPO, mode="write"):
                        self.fail("must not yield a token")
                self.assertEqual([item["method"] for item in self.requests], ["GET"])
                self.assert_closed()

    def test_wrong_or_excessive_token_permissions_are_revoked(self):
        for permissions in ({}, READ, {**WRITE, "actions": "read"},
                            {**WRITE, "administration": "write"}, {**WRITE, "metadata": "write"},
                            {"contents": "write", "workflows": "read"}, None):
            with self.subTest(permissions=permissions):
                self.requests.clear()
                self.token = {**minted(), "permissions": permissions}
                with self.assertRaisesRegex(app.GitHubAppError, "permissions"):
                    with app.installation_token(REPO, mode="write"):
                        self.fail("must not yield a token")
                self.assert_revoked()

    def test_read_mode_rejects_write_token(self):
        with self.assertRaisesRegex(app.GitHubAppError, "permissions"):
            with app.installation_token(REPO, mode="read"):
                self.fail("must not yield a token")
        self.assert_revoked()

    def test_implicit_metadata_read_is_optional_in_token_response(self):
        self.token["permissions"] = WRITE.copy()
        with app.installation_token(REPO, mode="write"):
            pass
        self.assert_revoked()

    def test_expired_short_lived_long_lived_and_invalid_expiry_are_revoked(self):
        for value in (expiry(-1), expiry(0), expiry(60), expiry(3661), None, 12,
                      "2026-13-01T00:00:00Z", "2026-11-05T00:00:00", "not a date"):
            with self.subTest(expiry=value):
                self.requests.clear()
                self.token = {**minted(), "expires_at": value}
                with self.assertRaisesRegex(app.GitHubAppError, "expiry"):
                    with app.installation_token(REPO, mode="write"):
                        self.fail("must not yield a token")
                self.assert_revoked()

    def test_wrong_or_multiple_repository_scope_is_revoked(self):
        for value in (
            {"total_count": 0, "repositories": []},
            {"total_count": 2, "repositories": repositories()["repositories"] * 2},
            {"total_count": True, "repositories": repositories()["repositories"]},
            {"total_count": 1, "repositories": []},
            {"total_count": 1, "repositories": [None]},
            {"total_count": 1, "repositories": [{"full_name": "other/product"}]},
        ):
            with self.subTest(scope=value):
                self.requests.clear()
                self.repositories = value
                with self.assertRaises(app.GitHubAppError):
                    with app.installation_token(REPO, mode="write"):
                        self.fail("must not yield a token")
                self.assert_revoked()

    def test_repository_name_full_name_and_account_must_all_match(self):
        for field, value in (("name", "other"), ("full_name", "other/product"),
                             ("owner", {"login": "other"}), ("owner", None)):
            with self.subTest(field=field):
                self.requests.clear()
                self.repositories = repositories()
                self.repositories["repositories"][0][field] = value
                with self.assertRaisesRegex(app.GitHubAppError, "repository"):
                    with app.installation_token(REPO, mode="write"):
                        self.fail("must not yield a token")
                self.assert_revoked()

    def test_mint_response_repository_metadata_must_not_contradict_scope(self):
        for items in (None, [], repositories()["repositories"] * 2,
                      [{"name": "other", "full_name": "example/other", "owner": {"login": "example"}}]):
            with self.subTest(items=items):
                self.requests.clear()
                self.token = {**minted(), "repositories": items}
                with self.assertRaises(app.GitHubAppError):
                    with app.installation_token(REPO, mode="write"):
                        self.fail("must not yield a token")
                self.assert_revoked()

    def test_mint_response_repository_metadata_does_not_replace_scope_check(self):
        self.token["repositories"] = repositories()["repositories"]
        with app.installation_token(REPO, mode="write"):
            pass
        self.assertEqual(self.requests[2]["path"], "/installation/repositories?per_page=2&page=1")
        self.assert_revoked()

    def test_expiry_is_rechecked_after_repository_scope_request(self):
        with patch.object(app.time, "time", side_effect=[NOW, NOW, NOW + 3601]):
            with self.assertRaisesRegex(app.GitHubAppError, "expiry"):
                with app.installation_token(REPO, mode="write"):
                    self.fail("must not yield a token")
        self.assert_revoked()

    def test_case_insensitive_github_names_are_accepted(self):
        self.installation["account"]["login"] = "EXAMPLE"
        self.repositories["repositories"][0].update(
            name="PRODUCT", full_name="EXAMPLE/PRODUCT", owner={"login": "EXAMPLE"})
        with app.installation_token(REPO, mode="write"):
            pass
        self.assert_revoked()

    def test_caller_exception_and_baseexceptions_still_revoke(self):
        for error in (RuntimeError("caller failed"), KeyboardInterrupt(), SystemExit(2)):
            with self.subTest(error=type(error).__name__):
                self.requests.clear()
                with self.assertRaises(type(error)) as raised:
                    with app.installation_token(REPO, mode="write"):
                        raise error
                self.assertIs(raised.exception, error)
                self.assert_revoked()

    def test_repository_check_failure_still_revokes(self):
        self.failures[("GET", "/installation/repositories?per_page=2&page=1")] = OSError(TOKEN)
        with self.assertRaisesRegex(app.GitHubAppError, "^GitHub App API request failed$"):
            with app.installation_token(REPO, mode="write"):
                self.fail("must not yield a token")
        self.assert_revoked()
        self.assert_closed()

    def test_revoke_failure_is_not_silently_successful_or_leaky(self):
        self.failures[("DELETE", "/installation/token")] = OSError(TOKEN)
        with self.assertRaisesRegex(app.GitHubAppError, "^GitHub App token revocation failed$"):
            with app.installation_token(REPO, mode="write"):
                pass
        self.assert_revoked()
        self.assert_closed()

    def test_both_operation_and_revoke_failure_have_a_safe_diagnostic(self):
        self.failures[("DELETE", "/installation/token")] = OSError(TOKEN)
        with self.assertRaisesRegex(app.GitHubAppError, "operation failed and token revocation also failed"):
            with app.installation_token(REPO, mode="write"):
                raise RuntimeError(TOKEN)
        self.assert_revoked()
        self.assert_closed()

    def test_http_denials_and_redirects_never_retry_or_follow_location(self):
        for status in (301, 302, 303, 307, 308, 401, 403, 404, 422, 429, 500):
            with self.subTest(status=status):
                self.requests.clear()
                self.statuses[("GET", f"/repos/{REPO}/installation")] = status
                with self.assertRaisesRegex(app.GitHubAppError, "API rejected GET"):
                    with app.installation_token(REPO, mode="write"):
                        self.fail("must not yield a token")
                self.assertEqual(len(self.requests), 1)
                self.responses[-1].read.assert_not_called()
                self.assert_closed()

    def test_mint_denial_never_yields_or_attempts_an_unknown_token_revoke(self):
        self.statuses[("POST", "/app/installations/123/access_tokens")] = 403
        with self.assertRaisesRegex(app.GitHubAppError, "API rejected POST"):
            with app.installation_token(REPO, mode="write"):
                self.fail("must not yield a token")
        self.assertEqual([item["method"] for item in self.requests], ["GET", "POST"])
        self.assert_closed()

    def test_transport_failures_do_not_leak_response_or_credentials(self):
        for error in (TimeoutError(TOKEN), OSError(KEY), http.client.HTTPException(TOKEN)):
            with self.subTest(error=type(error).__name__):
                self.requests.clear()
                self.failures[("GET", f"/repos/{REPO}/installation")] = error
                with self.assertRaisesRegex(app.GitHubAppError, "^GitHub App API request failed$"):
                    with app.installation_token(REPO, mode="write"):
                        self.fail("must not yield a token")
                self.assert_closed()

    def test_malformed_and_oversized_json_are_bounded_and_sanitized(self):
        for raw in (b"bad-json", b"[1]", b"null", b"\xff", b"{" + b" " * app.MAX_RESPONSE_BYTES):
            with self.subTest(length=len(raw)):
                self.installation = raw
                with self.assertRaises(app.GitHubAppError):
                    with app.installation_token(REPO, mode="write"):
                        self.fail("must not yield a token")
                self.responses[-1].read.assert_called_once_with(app.MAX_RESPONSE_BYTES + 1)
                self.assert_closed()

    def test_connection_cleanup_errors_are_sanitized(self):
        original = self.connect

        def connect(host, **kwargs):
            connection = original(host, **kwargs)
            connection.close.side_effect = OSError(TOKEN)
            return connection

        self.transport.side_effect = connect
        with self.assertRaisesRegex(app.GitHubAppError, "^GitHub App API connection cleanup failed$"):
            with app.installation_token(REPO, mode="write"):
                self.fail("must not yield a token")

    def test_invalid_token_cannot_inject_headers(self):
        for token in (None, "", "a\r\nHost: attacker", "has space", "x" * 513, 42):
            with self.subTest(token=token):
                self.requests.clear()
                self.token = {**minted(), "token": token}
                with self.assertRaisesRegex(app.GitHubAppError, "invalid GitHub App installation token"):
                    with app.installation_token(REPO, mode="write"):
                        self.fail("must not yield a token")
                self.assertEqual(len(self.requests), 2)

    def test_invalid_input_never_signs_or_calls_github(self):
        for repository in ("", "../product", "example/..", "example/.", "example/a/b",
                           "//attacker/product", "example/product?other", "a_b/product",
                           "example/product%2fother", "example/product\r\nHost:x", "a" * 40 + "/x"):
            with self.subTest(repository=repository), self.assertRaises(app.GitHubAppError):
                with app.installation_token(repository, mode="write"):
                    self.fail("must not yield a token")
        for argument, value in (("mode", "admin"), ("app_id", "-1"), ("installation_id", "../2"),
                                ("app_id", True), ("installation_id", "01"), ("app_id", 0),
                                ("installation_id", "1" * 20)):
            with self.subTest(argument=argument, value=value), self.assertRaises(app.GitHubAppError):
                with app.installation_token(REPO, **{argument: value}):
                    self.fail("must not yield a token")
        self.signer.assert_not_called()
        self.transport.assert_not_called()

    def test_arbitrary_endpoints_methods_and_header_injection_are_rejected(self):
        for method, path, token in (
            ("GET", "https://attacker.example/repos/example/product/installation", TOKEN),
            ("GET", "//attacker.example/installation/token", TOKEN),
            ("GET", "/repos/example/../installation", TOKEN),
            ("GET", "/repos/example/product%2Fother/installation", TOKEN),
            ("GET", "/repos/example/product/installation?redirect=other", TOKEN),
            ("POST", "/app/installations/123/access_tokens?x", TOKEN),
            ("POST", "/app/installations/0/access_tokens", TOKEN),
            ("PUT", "/installation/token", TOKEN),
            ("DELETE", "/app/installations/123", TOKEN),
            ("DELETE", "/installation/token", "token\nHost:attacker"),
        ):
            with self.subTest(method=method, path=path), self.assertRaises(app.GitHubAppError):
                app._request(method, path, token)
        self.transport.assert_not_called()

    def test_no_ambient_pat_fallback(self):
        with patch.dict(os.environ, {"GH_TOKEN": "ambient-pat", "GITHUB_TOKEN": "other-pat"}, clear=True):
            with self.assertRaises(app.GitHubAppError):
                with app.installation_token(REPO, mode="write"):
                    self.fail("must not yield a token")
        self.signer.assert_not_called()
        self.transport.assert_not_called()

    def test_key_file_is_copied_to_private_temp_then_preserved_at_source(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pem"
            source.write_text(KEY)
            with app.installation_token(REPO, mode="write", private_key_file=source):
                self.assertEqual(source.read_text(), KEY)
            self.assertEqual(source.read_text(), KEY)
            self.assertNotIn(source, self.key_paths)
            self.assert_closed()

    def test_gitlab_file_variable_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "file-variable"
            source.write_text(KEY)
            env = {key: value for key, value in ENV.items() if key != "RC_PROMOTION_APP_PRIVATE_KEY"}
            env["RC_PROMOTION_APP_PRIVATE_KEY_FILE"] = str(source)
            with patch.dict(os.environ, env, clear=True):
                with app.installation_token(REPO, mode="write"):
                    pass
            self.assertEqual(source.read_text(), KEY)
        self.assert_revoked()

    def test_missing_ambiguous_empty_encrypted_and_large_keys_fail_before_signing(self):
        for kwargs in ({"private_key": ""}, {"private_key": "x" * (app.MAX_KEY_BYTES + 1)},
                       {"private_key": "-----BEGIN ENCRYPTED PRIVATE KEY-----\nx"},
                       {"private_key": "not PEM"}, {"private_key": KEY + "\0"},
                       {"private_key": KEY, "private_key_file": "/does-not-exist"}):
            with self.subTest(kwargs=list(kwargs)), self.assertRaises(app.GitHubAppError):
                with app.installation_token(REPO, mode="write", **kwargs):
                    self.fail("must not yield a token")
        with patch.dict(os.environ, {"RC_PROMOTION_APP_PRIVATE_KEY_FILE": "/other"}):
            with self.assertRaisesRegex(app.GitHubAppError, "exactly one"):
                with app.installation_token(REPO, mode="write"):
                    self.fail("must not yield a token")
        self.signer.assert_not_called()
        self.transport.assert_not_called()

    def test_symlink_fifo_directory_missing_and_large_file_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "source").write_text(KEY)
            (root / "symlink").symlink_to(root / "source")
            os.mkfifo(root / "fifo")
            (root / "large").write_bytes(b"x" * (app.MAX_KEY_BYTES + 1))
            for path in (root / "symlink", root / "fifo", root, root / "missing", root / "large"):
                with self.subTest(path=path.name), self.assertRaises(app.GitHubAppError):
                    with app.installation_token(REPO, mode="write", private_key_file=path):
                        self.fail("must not yield a token")
        self.signer.assert_not_called()
        self.transport.assert_not_called()

    def test_signer_failures_remove_temp_key_and_suppress_openssl_diagnostics(self):
        for fail_at in ("rsa", "dgst"):
            for error in (None, subprocess.TimeoutExpired("openssl", 30, output=KEY, stderr=TOKEN),
                          OSError(KEY)):
                with self.subTest(fail_at=fail_at, error=type(error).__name__):
                    def fail(args, **kwargs):
                        result = self.sign(args, **kwargs)
                        if args[1] == fail_at:
                            if error is not None:
                                raise error
                            result.returncode = 1
                            result.stderr = TOKEN.encode()
                        return result
                    self.signer.side_effect = fail
                    with self.assertRaises(app.GitHubAppError) as raised:
                        with app.installation_token(REPO, mode="write"):
                            self.fail("must not yield a token")
                    self.assertNotIn(KEY, str(raised.exception))
                    self.assertNotIn(TOKEN, str(raised.exception))
                    self.assert_closed()
        self.transport.assert_not_called()

    def test_interrupted_signer_still_removes_temporary_key(self):
        def interrupt(args, **kwargs):
            self.sign(args, **kwargs)
            raise KeyboardInterrupt()
        self.signer.side_effect = interrupt
        with self.assertRaises(KeyboardInterrupt):
            with app.installation_token(REPO, mode="write"):
                self.fail("must not yield a token")
        self.assert_closed()
        self.transport.assert_not_called()


@unittest.skipUnless(shutil.which("openssl"), "openssl is required for the local signature test")
class SignatureTests(unittest.TestCase):
    def test_rs256_signature_with_a_disposable_test_key(self):
        # Generated locally for this test, never a configured credential or API call.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            key = root / "key.pem"
            pub = root / "key.pub"
            subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"],
                           capture_output=True, check=True, timeout=30)
            subprocess.run(["openssl", "rsa", "-in", str(key), "-pubout", "-out", str(pub)],
                           capture_output=True, check=True, timeout=30)
            with patch.object(app.http.client, "HTTPSConnection") as network:
                jwt = app._jwt(42, key.read_bytes())
                network.assert_not_called()
            header, payload, signature = jwt.split(".")
            (root / "payload").write_bytes((header + "." + payload).encode("ascii"))
            (root / "signature").write_bytes(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)))
            verified = subprocess.run(
                ["openssl", "dgst", "-sha256", "-verify", str(pub), "-signature",
                 str(root / "signature"), str(root / "payload")],
                capture_output=True, check=False, timeout=30,
            )
            self.assertEqual(verified.returncode, 0)


if __name__ == "__main__":
    unittest.main()
