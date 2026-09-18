# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Return missing release tags, rejecting existing tags with a different digest."""

import base64
import hashlib
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def check_tags(image, expected_digest, tags, actor, token):
    if not re.fullmatch(r"ghcr\.io/[a-z0-9][a-z0-9._/-]+", image):
        raise RuntimeError("Expected a GHCR image name")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_digest):
        raise RuntimeError("Invalid tested image digest")
    if not tags or any(not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag) for tag in tags):
        raise RuntimeError("Invalid release tag")
    if not actor or not token:
        raise RuntimeError("Registry credentials are missing")

    repository = image.removeprefix("ghcr.io/")
    query = urlencode({"service": "ghcr.io", "scope": f"repository:{repository}:pull"})
    basic = base64.b64encode(f"{actor}:{token}".encode()).decode()
    try:
        request = Request(f"https://ghcr.io/token?{query}", headers={"Authorization": f"Basic {basic}"})
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
        bearer = payload.get("token") if isinstance(payload, dict) else None
        if not isinstance(bearer, str) or not bearer:
            raise ValueError("Missing registry token")
    except (HTTPError, URLError, OSError, ValueError) as error:
        # Do not print exceptions or response bodies: they may contain credentials.
        if isinstance(error, HTTPError):
            error.close()
        raise RuntimeError(f"Cannot authenticate to inspect {image}; refusing publication") from None

    missing_tags = []
    for tag in tags:
        request = Request(
            f"https://ghcr.io/v2/{repository}/manifests/{tag}",
            headers={
                "Authorization": f"Bearer {bearer}",
                "Accept": ", ".join([
                    "application/vnd.oci.image.index.v1+json",
                    "application/vnd.oci.image.manifest.v1+json",
                    "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.docker.distribution.manifest.v2+json",
                ]),
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                # Compare the actual manifest bytes to the digest tested by CI.
                actual_digest = "sha256:" + hashlib.sha256(response.read()).hexdigest()
        except HTTPError as error:
            absent = False
            if error.code == 404:
                try:
                    payload = json.load(error)
                    errors = payload.get("errors") if isinstance(payload, dict) else None
                    absent = isinstance(errors, list) and bool(errors) and all(
                        isinstance(item, dict) and item.get("code") == "MANIFEST_UNKNOWN"
                        for item in errors
                    )
                except (OSError, ValueError):
                    pass
            error.close()
            if not absent:
                raise RuntimeError(f"Cannot verify {image}:{tag} (HTTP {error.code}); refusing publication") from None
            missing_tags.append(tag)
        except (URLError, OSError, ValueError):
            raise RuntimeError(f"Cannot reach registry for {image}:{tag}; refusing publication") from None
        else:
            if actual_digest != expected_digest:
                raise RuntimeError(f"{image}:{tag} points to a different digest; bump cds-containers/VERSION")
    return missing_tags


if __name__ == "__main__":
    try:
        if len(sys.argv) < 4:
            raise RuntimeError("Usage: check_cds_tags.py IMAGE EXPECTED_DIGEST TAG [TAG ...]")
        missing_tags = check_tags(
            sys.argv[1], sys.argv[2], sys.argv[3:],
            os.environ.get("GITHUB_ACTOR"), os.environ.get("GITHUB_TOKEN"),
        )
        # Emit a promotion plan only after every tag has been checked successfully.
        for tag in missing_tags:
            print(tag)
    except RuntimeError as error:
        sys.exit(str(error))
