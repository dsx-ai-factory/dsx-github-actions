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

    <br> { <!-- ignored in indented code -->
"""
        )
        self.assertEqual(errors, [])

    def test_reports_each_unsafe_construct(self) -> None:
        errors = self.check("<br>\nBare { brace\n<!-- comment -->\n")
        self.assertEqual(len(errors), 3)
        self.assertIn("non-self-closing void element", errors[0])
        self.assertIn("bare {", errors[1])
        self.assertIn("HTML comment", errors[2])

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
