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
# MDX permits indented fences because indented code blocks are disabled.
FENCE = re.compile(r"^[ \t]*(?P<marker>`{3,}|~{3,})")
LIST_ITEM = re.compile(r"^ *(?:[-+*]|[0-9]{1,9}[.)])(?P<space> +|$)")
BLOCKQUOTE = re.compile(r"^(?P<indent> *)> ?")


def quote_contents(line: str) -> list[str]:
    contents = [line.expandtabs(4)]
    while quote := BLOCKQUOTE.match(contents[-1]):
        contents.append(quote.group("indent") + contents[-1][quote.end():])
    return contents


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
    fence_quote_depth = 0
    list_indents: list[tuple[int, int]] = []  # Quote depth and content indent.
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
            contents = quote_contents(raw_line)
            quoted = len(contents) > fence_quote_depth
            fence_line = contents[fence_quote_depth] if quoted else contents[-1]
            outside_list = quoted and any(
                contents[depth].strip()
                and len(contents[depth]) - len(contents[depth].lstrip(" ")) < indent
                for depth, indent in list_indents
            )
            # Leaving a blockquote or list item ends its unclosed fenced block.
            if not quoted or outside_list:
                in_fence = False
                fence_marker = ""
            else:
                close = re.fullmatch(
                    rf"[ \t]*{re.escape(fence_marker[0])}"
                    rf"{{{len(fence_marker)},}}[ \t]*",
                    fence_line,
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

        contents = quote_contents(raw_line)
        quote_depth = len(contents) - 1
        fence_line = contents[-1]
        indent = len(fence_line) - len(fence_line.lstrip(" "))
        while list_indents and (
            quote_depth < list_indents[-1][0]
            or (fence_line.strip() and indent < list_indents[-1][1])
        ):
            list_indents.pop()
        # Lists and blockquotes can alternate on the same line. Preserve list
        # indentation so subsequent quotes and continuation lines share columns.
        while fence_line.strip():
            item = LIST_ITEM.match(fence_line)
            if item:
                padding = len(item.group("space"))
                # Blank items use one space. Excess padding belongs to content.
                if item.end() == len(fence_line) or not 1 <= padding <= 4:
                    padding = 1
                content_indent = item.start("space") + padding
                list_indents.append((quote_depth, content_indent))
                fence_line = " " * content_indent + fence_line[content_indent:]
            elif quote := BLOCKQUOTE.match(fence_line):
                quote_depth += 1
                fence_line = quote.group("indent") + fence_line[quote.end():]
            else:
                break

        fence = FENCE.match(fence_line)
        if fence:
            in_fence = True
            fence_marker = fence.group("marker")
            fence_quote_depth = quote_depth
            continue

        # MDX does not treat indentation as a code block.
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
