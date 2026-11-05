# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Short-lived, single-repository GitHub App credentials for release adapters.

Use ``with installation_token("owner/repo", mode="write") as token`` only after
the adapter has checked its protected deployment approval. Read mode is the
default and grants contents/actions read access; write mode grants only
contents/workflows write access. GitHub's implicit metadata read is accepted.

Configuration is explicit keyword arguments or RC_PROMOTION_APP_ID,
RC_PROMOTION_APP_INSTALLATION_ID, and exactly one of RC_PROMOTION_APP_PRIVATE_KEY
(PEM text) / RC_PROMOTION_APP_PRIVATE_KEY_FILE (a GitLab file variable path).
No ambient PAT, installation discovery, CLI, or environment mutation is used.
The token stays in memory and is revoked on context exit, including validation
or caller failure. A process killed without cleanup relies on GitHub's one-hour
expiry. Callers must never print or persist the yielded token.
"""

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import http.client
import json
import os
import re
import stat
import subprocess
import tempfile
import time


OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?"
REPOSITORY = rf"{OWNER}/[A-Za-z0-9_.-]{{1,100}}"
ID = r"[1-9][0-9]{0,18}"
MAX_KEY_BYTES = 16384
MAX_RESPONSE_BYTES = 65536
TIMEOUT = 30


class GitHubAppError(ValueError):
    """A bounded diagnostic without credentials or server/subprocess output."""


def _require(condition, message):
    if not condition:
        raise GitHubAppError(message)


def _matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None


def _identifier(value):
    if type(value) is int:
        value = str(value)
    _require(_matches(ID, value), "GitHub App and installation IDs must be positive integers")
    return int(value)


def _repository(value):
    _require(_matches(REPOSITORY, value) and value.split("/")[1] not in {".", ".."},
             "invalid GitHub product repository")
    return value.split("/")


def _key_bytes(private_key, private_key_file):
    _require((private_key is not None) != (private_key_file is not None),
             "configure exactly one GitHub App private key source")
    try:
        if private_key_file is not None:
            path = os.fspath(private_key_file)
            _require(isinstance(path, str) and 0 < len(path) <= 4096 and "\0" not in path,
                     "invalid GitHub App private key file")
            # Reject symlinks, FIFOs, and devices rather than blocking on a file variable.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                _require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= MAX_KEY_BYTES,
                         "invalid GitHub App private key file")
                raw = source.read(MAX_KEY_BYTES + 1)
        else:
            _require(isinstance(private_key, str) and 0 < len(private_key) <= MAX_KEY_BYTES,
                     "invalid GitHub App private key")
            raw = private_key.encode("ascii")
    except (OSError, TypeError, UnicodeError, ValueError) as error:
        if isinstance(error, GitHubAppError):
            raise
        raise GitHubAppError("could not read GitHub App private key") from None
    _require(0 < len(raw) <= MAX_KEY_BYTES and b"\0" not in raw and
             raw.startswith((b"-----BEGIN RSA PRIVATE KEY-----", b"-----BEGIN PRIVATE KEY-----")),
             "invalid GitHub App private key")
    return raw


def _encoded(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=")


def _jwt(app_id, key):
    now = int(time.time())
    header = _encoded(b'{"alg":"RS256","typ":"JWT"}')
    claims = _encoded(json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(app_id)},
                                separators=(",", ":")).encode("ascii"))
    payload = header + b"." + claims
    env = {"PATH": os.environ.get("PATH", os.defpath), "LC_ALL": "C"}
    try:
        with tempfile.NamedTemporaryFile(prefix="dsx-github-app-", suffix=".pem") as pem:
            os.fchmod(pem.fileno(), 0o600)
            pem.write(key)
            pem.flush()
            # Reject non-RSA or encrypted keys without prompting or exposing diagnostics.
            checked = subprocess.run(
                ["openssl", "rsa", "-in", pem.name, "-check", "-noout", "-passin", "pass:"],
                input=b"", capture_output=True, timeout=TIMEOUT, check=False, env=env,
            )
            _require(checked.returncode == 0, "GitHub App RSA key validation failed")
            signed = subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", pem.name, "-passin", "pass:"],
                input=payload, capture_output=True, timeout=TIMEOUT, check=False, env=env,
            )
            _require(signed.returncode == 0 and 256 <= len(signed.stdout) <= 1024,
                     "GitHub App JWT signing failed")
    except (OSError, subprocess.SubprocessError):
        raise GitHubAppError("GitHub App JWT signing failed") from None
    return (payload + b"." + _encoded(signed.stdout)).decode("ascii")


def _request(method, path, credential, data=None):
    allowed = (
        method == "GET" and (
            _matches(rf"/repos/({REPOSITORY})/installation", path) or
            path == "/installation/repositories?per_page=2&page=1"
        ) or
        method == "POST" and _matches(rf"/app/installations/{ID}/access_tokens", path) or
        method == "DELETE" and path == "/installation/token"
    )
    _require(allowed, "unsupported GitHub App API request")
    if path.startswith("/repos/"):
        _repository(path[len("/repos/"):-len("/installation")])
    _require(_matches(r"[A-Za-z0-9_.-]{1,4096}", credential), "invalid GitHub App credential")
    expected = {"GET": 200, "POST": 201, "DELETE": 204}[method]
    connection = None
    try:
        # HTTPSConnection never follows redirects, reads proxy env, or changes API host.
        connection = http.client.HTTPSConnection("api.github.com", timeout=TIMEOUT)
        connection.request(method, path,
                           body=json.dumps(data).encode("utf8") if data is not None else None,
                           headers={"Authorization": "Bearer " + credential,
                                    "Accept": "application/vnd.github+json",
                                    "Content-Type": "application/json",
                                    "User-Agent": "dsx-release-github-app",
                                    "X-GitHub-Api-Version": "2022-11-28"})
        response = connection.getresponse()
        _require(response.status == expected, f"GitHub App API rejected {method} request")
        if method == "DELETE":
            return None
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        _require(len(raw) <= MAX_RESPONSE_BYTES, "GitHub App API response is too large")
        value = json.loads(raw)
        _require(isinstance(value, dict), "invalid GitHub App API response")
        return value
    except (OSError, http.client.HTTPException, UnicodeError, ValueError, RecursionError) as error:
        if isinstance(error, GitHubAppError):
            raise
        raise GitHubAppError("GitHub App API request failed") from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except (OSError, http.client.HTTPException):
                raise GitHubAppError("GitHub App API connection cleanup failed") from None


def _installation(value, app_id, installation_id, owner, permissions):
    account = value.get("account")
    _require(type(value.get("id")) is int and value["id"] == installation_id and
             type(value.get("app_id")) is int and value["app_id"] == app_id and
             isinstance(account, dict) and isinstance(account.get("login"), str) and
             account["login"].lower() == owner.lower() and
             value.get("target_type") in {"Organization", "User"} and
             "suspended_at" in value and value["suspended_at"] is None,
             "GitHub App installation does not match the product account")
    granted = value.get("permissions")
    _require(isinstance(granted, dict) and all(
        granted.get(name) == level or (level == "read" and granted.get(name) == "write")
        for name, level in permissions.items()
    ), "GitHub App installation lacks required permissions")


def _token_metadata(value, permissions):
    granted = value.get("permissions")
    _require(isinstance(granted, dict) and
             {name: level for name, level in granted.items() if name != "metadata"} == permissions and
             granted.get("metadata", "read") == "read",
             "GitHub App token permissions differ from the requested scope")
    expiry = value.get("expires_at")
    _require(_matches(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", expiry),
             "invalid GitHub App token expiry")
    try:
        expires = datetime.strptime(expiry, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise GitHubAppError("invalid GitHub App token expiry") from None
    remaining = expires.timestamp() - time.time()
    _require(60 < remaining <= 3660, "GitHub App token expiry is outside the allowed lifetime")


def _repositories(value, repository):
    items = value.get("repositories")
    _require(type(value.get("total_count")) is int and value["total_count"] == 1 and
             isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict),
             "GitHub App token must cover exactly one product repository")
    owner, name = repository.split("/")
    item = items[0]
    account = item.get("owner")
    _require(isinstance(item.get("full_name"), str) and item["full_name"].lower() == repository.lower() and
             isinstance(item.get("name"), str) and item["name"].lower() == name.lower() and
             isinstance(account, dict) and isinstance(account.get("login"), str) and
             account["login"].lower() == owner.lower(),
             "GitHub App token repository does not match the configured product")


@contextmanager
def installation_token(repository, *, mode="read", app_id=None, installation_id=None,
                       private_key=None, private_key_file=None):
    """Yield a verified token; revoke it even if validation or the caller fails.

    Supply both IDs and one key source explicitly, or use the documented env
    variables. An explicit key source overrides both env key sources. The caller
    owns deployment authorization and must keep operations within token expiry.
    """
    owner, name = _repository(repository)
    _require(mode in ("read", "write"), "GitHub App mode must be read or write")
    permissions = ({"contents": "write", "workflows": "write"} if mode == "write" else
                   {"contents": "read", "actions": "read"})
    app_id = _identifier(app_id if app_id is not None else os.environ.get("RC_PROMOTION_APP_ID"))
    installation_id = _identifier(installation_id if installation_id is not None else
                                 os.environ.get("RC_PROMOTION_APP_INSTALLATION_ID"))
    if private_key is None and private_key_file is None:
        private_key = os.environ.get("RC_PROMOTION_APP_PRIVATE_KEY")
        private_key_file = os.environ.get("RC_PROMOTION_APP_PRIVATE_KEY_FILE")
    jwt = _jwt(app_id, _key_bytes(private_key, private_key_file))
    installation = _request("GET", f"/repos/{repository}/installation", jwt)
    _installation(installation, app_id, installation_id, owner, permissions)
    result = _request("POST", f"/app/installations/{installation_id}/access_tokens", jwt,
                      {"repositories": [name], "permissions": permissions})
    token = result.get("token")
    _require(_matches(r"[A-Za-z0-9_-]{1,512}", token), "invalid GitHub App installation token")
    failed = False
    try:
        _token_metadata(result, permissions)
        if "repositories" in result:
            items = result["repositories"]
            _repositories({"total_count": len(items) if isinstance(items, list) else 0,
                           "repositories": items}, repository)
        _repositories(_request("GET", "/installation/repositories?per_page=2&page=1", token),
                      repository)
        _token_metadata(result, permissions)
        yield token
    except BaseException:
        failed = True
        raise
    finally:
        try:
            _request("DELETE", "/installation/token", token)
        except GitHubAppError:
            if failed:
                raise GitHubAppError("GitHub App operation failed and token revocation also failed") from None
            raise GitHubAppError("GitHub App token revocation failed") from None
