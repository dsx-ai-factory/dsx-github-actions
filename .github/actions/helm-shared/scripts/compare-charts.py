#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Compare Helm chart files without depending on archive creation timestamps."""

import argparse
import gzip
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import zlib


def unique_mapping(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate lock field")
        result[key] = value
    return result


def lock_contents(contents, name):
    try:
        result = subprocess.run(
            ["yq", "eval", "-o=json", "-I=0", ".", "-"],
            input=contents, capture_output=True, check=False,
        )
        if result.returncode:
            raise ValueError("YAML parsing failed")
        lock = json.loads(result.stdout, object_pairs_hook=unique_mapping)
        if not isinstance(lock, dict):
            raise ValueError("lock document is not a mapping")
        # Helm regenerates this timestamp during dependency updates. The
        # dependency declarations, digest, and any other fields still match.
        lock.pop("generated", None)
        return json.dumps(lock, sort_keys=True, allow_nan=False).encode()
    except (OSError, ValueError) as error:
        # yq's parse errors can include the input; do not print chart contents.
        raise ValueError(f"cannot parse chart lock {name[:200]!r}") from error


def chart_contents(path):
    files = {}
    paths = set()
    try:
        with gzip.open(path, "rb") as compressed:
            with tarfile.open(fileobj=compressed, mode="r:") as archive:
                for member in archive:
                    name = member.name.rstrip("/") if member.isdir() else member.name
                    parts = name.split("/")
                    if any(part in ("", ".", "..") for part in parts) or "\\" in name:
                        raise ValueError(f"unsafe chart path {name[:200]!r}")
                    if name in paths:
                        raise ValueError(f"duplicate chart path {name[:200]!r}")
                    paths.add(name)
                    if member.isdir():
                        continue
                    if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or member.sparse is not None:
                        raise ValueError(f"unsupported chart entry {name[:200]!r}")
                    with archive.extractfile(member) as source:
                        contents = source.read()
                    # A files/Chart.lock application file is not Helm metadata.
                    is_chart_lock = (
                        len(parts) >= 2 and len(parts) % 2 == 0
                        and parts[-1] == "Chart.lock"
                        and all(parts[index] == "charts" for index in range(1, len(parts) - 1, 2))
                    )
                    files[name] = lock_contents(contents, name) if is_chart_lock else contents
                # Read through the gzip trailer so truncated or corrupt gzip
                # streams cannot compare equal merely because tar reached EOF.
                while compressed.read(1024 * 1024):
                    pass
    except (OSError, EOFError, tarfile.TarError, zlib.error) as error:
        raise ValueError(f"cannot read chart archive {Path(path).name[:200]!r}") from error
    if not files:
        raise ValueError("chart archive contains no files")
    for name in paths:
        parts = name.split("/")
        if any("/".join(parts[:index]) in files for index in range(1, len(parts))):
            raise ValueError(f"chart file is also a parent directory of {name[:200]!r}")
    # Python's tar reader can silently stop at a corrupt later header. Helm
    # must also accept the archive and metadata before normalization is safe.
    try:
        result = subprocess.run(
            ["helm", "show", "chart", str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if result.returncode:
            raise ValueError("Helm rejected the chart")
    except (OSError, ValueError) as error:
        raise ValueError(f"Helm cannot load chart archive {Path(path).name[:200]!r}") from error
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("local_chart")
    parser.add_argument("remote_chart")
    args = parser.parse_args()
    try:
        local = chart_contents(args.local_chart)
        remote = chart_contents(args.remote_chart)
        if local.keys() != remote.keys():
            raise ValueError("chart file paths differ")
        for name in sorted(local):
            if local[name] != remote[name]:
                raise ValueError(f"chart contents differ at {name[:200]!r}")
    except ValueError as error:
        print(f"Chart comparison failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
