#!/usr/bin/env python3
# Copyright 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Check Markdown files for constructs that Fern's MDX parser rejects."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


VOID_ELEMENT = re.compile(
    r"<(?:area|base|br|col|embed|hr|img|input|link|meta|param|source|track|wbr)"
    r"(?:\s[^<>]*?)?>",
    re.IGNORECASE,
)
BARE_OPEN_BRACE = re.compile(r"(?<!\\)\{")
FENCE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})")


def markdown_files(paths: list[Path]) -> list[Path]:
    files: set[Path] = set()
    for path in paths:
        if path.is_file() and path.suffix.lower() in {".md", ".mdx"}:
            files.add(path)
        elif path.is_dir():
            files.update(path.rglob("*.md"))
            files.update(path.rglob("*.mdx"))
    return sorted(files)


def check_file(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    errors: list[str] = []
    in_fence = False
    fence_marker = ""
    in_jsx_comment = False

    def strip_code_and_comments(line: str) -> str:
        nonlocal in_jsx_comment
        visible: list[str] = []
        index = 0

        while index < len(line):
            if in_jsx_comment:
                end = line.find("*/}", index)
                if end < 0:
                    return "".join(visible)
                in_jsx_comment = False
                index = end + 3
                continue

            if line.startswith("{/*", index):
                in_jsx_comment = True
                index += 3
                continue

            if line[index] == "`":
                end = index
                while end < len(line) and line[end] == "`":
                    end += 1
                delimiter = end - index
                close = re.search(
                    rf"(?<!`)`{{{delimiter}}}(?!`)", line[end:]
                )
                if close is None:
                    visible.extend(line[index:end])
                    index = end
                    continue
                index = end + close.end()
                continue

            visible.append(line[index])
            index += 1

        return "".join(visible)

    for line_number, raw_line in enumerate(lines, 1):
        if in_fence:
            close = re.fullmatch(
                rf" {{0,3}}{re.escape(fence_marker[0])}"
                rf"{{{len(fence_marker)},}}[ \t]*",
                raw_line,
            )
            if close:
                in_fence = False
                fence_marker = ""
            continue

        if in_jsx_comment:
            end = raw_line.find("*/}")
            if end < 0:
                continue
            raw_line = raw_line[end + 3 :]
            in_jsx_comment = False

        fence = FENCE.match(raw_line)
        if fence:
            in_fence = True
            fence_marker = fence.group("marker")
            continue

        if raw_line.startswith(("    ", "\t")):
            continue

        line_without_code = strip_code_and_comments(raw_line)

        for match in VOID_ELEMENT.finditer(line_without_code):
            if not match.group(0).rstrip().endswith("/>"):
                errors.append(
                    f"{path}:{line_number}: non-self-closing void element: "
                    f"{match.group(0)}"
                )

        if BARE_OPEN_BRACE.search(line_without_code):
            errors.append(f"{path}:{line_number}: bare {{ outside code fence")

        if "<!--" in line_without_code:
            errors.append(f"{path}:{line_number}: HTML comment outside code fence")

    if in_jsx_comment:
        errors.append(f"{path}: unterminated JSX comment")

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Markdown files or directories to check",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    missing = [str(path) for path in args.paths if not path.exists()]
    if missing:
        for path in missing:
            print(f"MDX: path does not exist: {path}", file=sys.stderr)
        return 2

    errors: list[str] = []
    files = markdown_files(args.paths)
    for path in files:
        errors.extend(check_file(path))

    if errors:
        for error in errors:
            print(f"MDX: {error}")
        print(f"MDX: {len(errors)} violation(s) found in {len(files)} file(s)")
        return 1

    print(f"MDX: {len(files)} file(s) are safe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
