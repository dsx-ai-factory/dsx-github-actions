# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Refuse release-tag reuse; only a GHCR MANIFEST_UNKNOWN response means absent."""

import base64
import json
import os
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def check_tags(image, tags, actor, token):
    if not re.fullmatch(r"ghcr\.io/[a-z0-9][a-z0-9._/-]+", image):
        raise RuntimeError("Expected a GHCR image name")
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
            with urlopen(request, timeout=30):
                pass
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
        except (URLError, OSError, ValueError):
            raise RuntimeError(f"Cannot reach registry for {image}:{tag}; refusing publication") from None
        else:
            raise RuntimeError(f"{image}:{tag} already exists; bump cds-containers/VERSION")
        print(f"Unused release tag: {image}:{tag}")


if __name__ == "__main__":
    try:
        if len(sys.argv) < 3:
            raise RuntimeError("Usage: check_cds_tags.py IMAGE TAG [TAG ...]")
        check_tags(sys.argv[1], sys.argv[2:], os.environ.get("GITHUB_ACTOR"), os.environ.get("GITHUB_TOKEN"))
    except RuntimeError as error:
        sys.exit(str(error))
