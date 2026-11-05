# Copyright 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("check_docs_mdx.py")
SPEC = importlib.util.spec_from_file_location("check_docs_mdx", SCRIPT)
assert SPEC and SPEC.loader
CHECKER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHECKER)


class CheckDocsMdxTest(unittest.TestCase):
    def check(self, content: str) -> list[str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "page.mdx"
            path.write_text(content, encoding="utf-8")
            return CHECKER.check_file(path)

    def test_accepts_mdx_safe_constructs(self) -> None:
        errors = self.check(
            """# Safe page

<br />
Escaped \\{brace and `inline { code`.
Double-backtick ``inline <br> { code``.
{/* JSX comment { } */}

````html
<br>
<!-- ignored in a fence -->
```
````

    <br /> Indented text with an escaped \\{brace.
"""
        )
        self.assertEqual(errors, [])

    def test_accepts_indented_fences(self) -> None:
        cases = [
            ("- Parent\n  - Child\n\n", "    ", "    "),
            ("1. Item\n\n", "    ", "    "),
            ("- Parent\n  - Child\n    - Grandchild\n\n", "      ", "      "),
            ("- Parent\n", "  - ", "    "),
            ("", "1. ", "   "),
            ("", "    ", "    "),
            ("", "\t", "\t"),
            ("", "    ", ""),
        ]
        for prefix, opening, indent in cases:
            for marker in ["```", "~~~", "````"]:
                with self.subTest(prefix=prefix, opening=opening, marker=marker):
                    content = (
                        prefix + opening + marker + "text\n"
                        + indent + '{"ok": true} <br> <!-- example -->\n'
                        + indent + marker + "\n"
                    )
                    self.assertEqual(self.check(content), [])
                    errors = self.check(content + "\n" + indent + "Bare { brace\n")
                    self.assertEqual(len(errors), 1)
                    self.assertTrue(errors[0].endswith("bare { outside code fence"))

    def test_indented_fence_closer_matches_marker_and_length(self) -> None:
        for closer in ["```", "~~~~", "````not-a-close"]:
            with self.subTest(closer=closer):
                errors = self.check(
                    "- Parent\n  - Child\n\n"
                    "    ````text\n"
                    f"    {closer}\n"
                    "    { ignored <br> <!-- example -->\n"
                    "    `````\n"
                    "\nBare { brace\n"
                )
                self.assertEqual(len(errors), 1)
                self.assertTrue(errors[0].endswith("bare { outside code fence"))

    def test_unclosed_list_fence_ends_at_list_boundary(self) -> None:
        cases = [
            (
                "- Parent\n  - Child\n\n    ```text\n    { ignored\n",
                ["Bare { brace", "  - Sibling { brace", "  Parent { brace"],
            ),
            (
                "1. Item\n\n    ```text\n    { ignored\n",
                ["Bare { brace", "2. Sibling { brace", "  Outside { brace"],
            ),
            (
                "- Parent\n  - Child\n    - Grandchild\n\n      ```text\n      { ignored\n",
                ["Bare { brace", "    - Sibling { brace", "  Parent { brace"],
            ),
        ]
        for prefix, following_lines in cases:
            self.assertEqual(self.check(prefix), [])
            for following in following_lines:
                with self.subTest(prefix=prefix, following=following):
                    errors = self.check(prefix + "\n" + following + "\n")
                    self.assertEqual(len(errors), 1)
                    self.assertTrue(errors[0].endswith("bare { outside code fence"))

    def test_accepts_quoted_fences(self) -> None:
        cases = [
            ("- Item\n", "    > "),
            ("- Parent\n  - Child\n\n", "    > "),
            ("- Item\n", "    > > "),
            ("", "> "),
            ("", "    > "),
            ("> - Item\n", ">     "),
        ]
        for prefix, quote in cases:
            for marker in ["```", "~~~"]:
                with self.subTest(prefix=prefix, quote=quote, marker=marker):
                    content = (
                        prefix + quote + marker + "text\n"
                        + quote + '{"ok": true} <br> <!-- example -->\n'
                        + quote + marker + "\n"
                    )
                    self.assertEqual(self.check(content), [])
                    errors = self.check(content + quote + "Bare { brace\n")
                    self.assertEqual(len(errors), 1)
                    self.assertTrue(errors[0].endswith("bare { outside code fence"))

    def test_quoted_fence_ends_at_quote_boundary(self) -> None:
        cases = [
            ("    > ", "    Bare { brace\n"),
            ("    > ", "\n    > Bare { brace\n"),
            ("    > > ", "    > Bare { brace\n"),
        ]
        for quote, following in cases:
            with self.subTest(quote=quote, following=following):
                errors = self.check(
                    "- Item\n" + quote + "```text\n" + quote + "{ ignored\n" + following
                )
                self.assertEqual(len(errors), 1)
                self.assertTrue(errors[0].endswith("bare { outside code fence"))

    def test_extra_quote_marker_does_not_close_fence(self) -> None:
        errors = self.check(
            "- Item\n"
            "    > ```text\n"
            "    > > ```\n"
            "    > { ignored\n"
            "    > ```\n"
            "    > Bare { brace\n"
        )
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].endswith("bare { outside code fence"))

    def test_quoted_blank_line_keeps_list_fence_open(self) -> None:
        self.assertEqual(self.check(
            "> - Item\n>   ```text\n>   { ignored\n>\n"
            ">   { still ignored\n>   ```\n"
        ), [])

    def test_unquoted_blank_line_ends_quoted_list(self) -> None:
        self.assertEqual(self.check(
            "> - Item\n>   ```text\n>   { ignored\n\n"
            ">     ```text\n> { ignored\n>     ```\n"
        ), [])

    def test_blank_list_item_uses_one_content_space(self) -> None:
        for marker in ["-", "1."]:
            for padding in [" ", "  ", "   ", "    ", "\t"]:
                with self.subTest(marker=marker, padding=padding):
                    content = f"{marker}{padding}\n    ```text\n    {{ ignored\n"
                    self.assertEqual(self.check(content), [])
                    errors = self.check(content + "Bare { brace\n")
                    self.assertEqual(len(errors), 1)
                    self.assertTrue(errors[0].endswith("bare { outside code fence"))

    def test_reports_each_unsafe_construct(self) -> None:
        errors = self.check("<br>\nBare { brace\n<!-- comment -->\n")
        self.assertEqual(len(errors), 3)
        self.assertIn("non-self-closing void element", errors[0])
        self.assertIn("bare {", errors[1])
        self.assertIn("HTML comment", errors[2])

    def test_checks_indented_mdx_content(self) -> None:
        for indent in ["  ", "    ", "\t"]:
            with self.subTest(indent=repr(indent)):
                errors = self.check("- Parent\n" + indent + "- Retry {request <br> <!-- note -->\n")
                self.assertEqual(len(errors), 3)
                self.assertTrue(any("bare {" in error for error in errors))
                self.assertTrue(any("non-self-closing" in error for error in errors))
                self.assertTrue(any("HTML comment" in error for error in errors))

    def test_supports_multiline_jsx_comments(self) -> None:
        errors = self.check("{/*\nIgnored { content and <br>\n*/}\nSafe text\n")
        self.assertEqual(errors, [])

    def test_inline_code_does_not_open_jsx_comment(self) -> None:
        errors = self.check("`literal {/*`\nBare { brace\n")
        self.assertEqual(len(errors), 1)
        self.assertIn("bare {", errors[0])

    def test_fences_inside_jsx_comments_do_not_change_fence_state(self) -> None:
        errors = self.check("{/*\n```\n    */}\nBare { brace\n")
        self.assertEqual(len(errors), 1)
        self.assertIn("bare {", errors[0])

    def test_fence_closer_rejects_trailing_text(self) -> None:
        errors = self.check("````\n```not-a-close\n{ ignored\n````\nBare { brace\n")
        self.assertEqual(len(errors), 1)
        self.assertIn("bare {", errors[0])

    def test_multiline_code_span_is_checked_conservatively(self) -> None:
        errors = self.check("`code\n{ ignored\n`\nBare { brace\n")
        self.assertEqual(len(errors), 2)
        self.assertIn("bare {", errors[0])
        self.assertIn("bare {", errors[1])

    def test_unmatched_backticks_do_not_hide_violations(self) -> None:
        errors = self.check("Unclosed ` code\nBare { brace\n")
        self.assertEqual(len(errors), 1)
        self.assertIn("bare {", errors[0])

    def test_reports_unterminated_jsx_comment(self) -> None:
        errors = self.check("{/* unfinished\n")
        self.assertEqual(len(errors), 1)
        self.assertIn("unterminated JSX comment", errors[0])

    def test_code_span_does_not_cross_paragraphs(self) -> None:
        errors = self.check("Unclosed ` code\n\nBare { brace\n\n` later paragraph\n")
        self.assertEqual(len(errors), 1)
        self.assertIn("bare {", errors[0])

    def test_code_span_does_not_cross_into_heading(self) -> None:
        errors = self.check("Unclosed ` code\n# Bare { ` heading\n")
        self.assertEqual(len(errors), 1)
        self.assertIn("bare {", errors[0])


if __name__ == "__main__":
    unittest.main()
