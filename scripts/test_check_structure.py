"""Tests for check_structure.py.

Each case starts from scripts/testdata, which the checker accepts, and breaks
exactly one thing. Without this the branding rules had no exercise at all: no
app in the store sets developer, tint or icon, so every one of those guards
could be deleted and the check would still pass.

    python3 scripts/test_check_structure.py
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "scripts" / "testdata" / "apps"
MANIFEST = "apps/example/app.yaml"


def check(edit=None) -> tuple[int, str]:
    """Run the real checker over a copy of the fixture, optionally edited."""
    with tempfile.TemporaryDirectory() as d:
        tree = Path(d)
        shutil.copytree(FIXTURE, tree / "apps")
        shutil.copytree(ROOT / "scripts", tree / "scripts")
        if edit is not None:
            man = tree / MANIFEST
            man.write_text(edit(man.read_text()))
        r = subprocess.run(
            [sys.executable, "scripts/check_structure.py"],
            cwd=tree, capture_output=True, text=True,
        )
        return r.returncode, r.stdout + r.stderr


def append(text: str):
    return lambda src: src + text


class TestFixture(unittest.TestCase):
    def test_the_fixture_is_accepted(self):
        code, out = check()
        self.assertEqual(code, 0, out)


class TestBranding(unittest.TestCase):
    """Optional fields, which means nothing in the store covers them."""

    def accepts(self, text):
        code, out = check(append(text))
        self.assertEqual(code, 0, out)

    def refuses(self, text, because):
        code, out = check(append(text))
        self.assertNotEqual(code, 0, f"accepted: {text!r}\n{out}")
        self.assertIn(because, out)

    def test_no_branding_at_all(self):
        self.accepts("")

    def test_a_full_set(self):
        self.accepts(
            "developer: Example Inc\n"
            "developer_url: https://example.com\n"
            "tint: '#5f74ff'\n"
            "icon:\n  path: M0 0L10 10Z\n  view_box: 0 0 24 24\n"
        )

    def test_tint_must_be_a_hex_colour(self):
        self.refuses("tint: red\n", "hex colour")

    def test_tint_cannot_smuggle_css(self):
        self.refuses(
            'tint: "red; background: url(https://evil.example.com)"\n', "hex colour"
        )

    # startswith("https://") is not parsing. The consumer uses url.Parse and
    # requires a host, so a checker that does less lets an app merge and then
    # vanish from the catalogue.
    def test_developer_url_needs_a_host(self):
        self.refuses(
            "developer: Example Inc\ndeveloper_url: 'https:///evil'\n", "https URL"
        )

    def test_developer_url_must_be_https(self):
        self.refuses(
            "developer: Example Inc\ndeveloper_url: 'http://example.com'\n", "https URL"
        )

    def test_developer_url_needs_a_developer(self):
        self.refuses("developer_url: https://example.com\n", "needs something to sit on")

    def test_icon_path_is_geometry(self):
        self.refuses(
            "icon:\n  path: \"<script>alert(1)</script>\"\n  view_box: 0 0 24 24\n",
            "geometry",
        )

    # Go's RE2 \s is [\t\n\f\r ]. Python's is wider, and anything this accepts
    # that the consumer refuses is an app that merges and is then dropped.
    def test_icon_path_rejects_unicode_whitespace(self):
        self.refuses(
            "icon:\n  path: \"M0　0L1 1\"\n  view_box: 0 0 24 24\n", "geometry"
        )

    def test_view_box_rejects_unicode_whitespace(self):
        self.refuses(
            "icon:\n  path: M0 0L1 1\n  view_box: \"0　0 24 24\"\n", "four numbers"
        )

    def test_view_box_must_be_four_numbers(self):
        self.refuses(
            "icon:\n  path: M0 0L1 1\n  view_box: 0 0 24\n", "four numbers"
        )

    def test_half_an_icon_is_refused(self):
        self.refuses("icon:\n  path: M0 0L1 1\n", "or neither")

    # The consumer treats both-empty as "no icon", so refusing it here blocks
    # a manifest that would have been fine.
    def test_an_empty_icon_is_no_icon(self):
        self.accepts("icon: {}\n")

    def test_an_overlong_icon_path_is_refused(self):
        self.refuses(
            "icon:\n  path: " + "M0 0" * 3000 + "\n  view_box: 0 0 24 24\n",
            "limit",
        )

    def test_an_unknown_icon_field_is_refused(self):
        self.refuses(
            "icon:\n  path: M0 0L1 1\n  view_box: 0 0 24 24\n  colour: red\n",
            "colour",
        )


class TestManifest(unittest.TestCase):
    def test_an_unknown_top_level_field_is_refused(self):
        code, out = check(append("not_a_field: 1\n"))
        self.assertNotEqual(code, 0, out)
        self.assertIn("not_a_field", out)

    def test_a_missing_required_field_is_refused(self):
        code, out = check(lambda s: s.replace("name: Example\n", ""))
        self.assertNotEqual(code, 0, out)

    # The scanner this replaced read `summary: >-` with no body as the literal
    # ">-" and passed, while the consumer resolves it to "" and refuses.
    def test_an_empty_block_scalar_is_refused(self):
        code, out = check(
            lambda s: s.replace(
                "    summary: >-\n"
                "      Sets X-Api-Key on GET and POST /v1/orders, and on nothing else.\n",
                "    summary: >-\n",
            )
        )
        self.assertNotEqual(code, 0, out)

    def test_a_host_with_a_scheme_is_refused(self):
        code, out = check(
            lambda s: s.replace("host: api.example.com", "host: https://api.example.com")
        )
        self.assertNotEqual(code, 0, out)

    def test_a_relative_route_is_refused(self):
        code, out = check(lambda s: s.replace("path: /v1/orders", "path: v1/orders"))
        self.assertNotEqual(code, 0, out)

    def test_a_method_outside_the_vocabulary_is_refused(self):
        code, out = check(
            lambda s: s.replace("methods: [GET, POST]", "methods: [TELEPORT]")
        )
        self.assertNotEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
