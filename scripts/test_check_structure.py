"""Tests for check_structure.py.

Each case starts from scripts/testdata, which the checker accepts, and breaks
exactly one thing. Without this the branding rules had no exercise at all: no
app in the store sets developer, tint or icon, so every one of those guards
could be deleted and the check would still pass.

    python3 scripts/test_check_structure.py
"""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "scripts" / "testdata" / "apps"
MANIFEST = "apps/example/app.yaml"
SKILL = "apps/example/skills/example-api/SKILL.md"


def check(edit=None, skill=None) -> tuple[int, str]:
    """Run the real checker over a copy of the fixture, optionally edited.

    `edit` rewrites the manifest and `skill` rewrites the skill body, because
    some rules are about the two agreeing.
    """
    with tempfile.TemporaryDirectory() as d:
        tree = Path(d)
        shutil.copytree(FIXTURE, tree / "apps")
        shutil.copytree(ROOT / "scripts", tree / "scripts")
        if edit is not None:
            man = tree / MANIFEST
            man.write_text(edit(man.read_text()))
        if skill is not None:
            md = tree / SKILL
            md.write_text(skill(md.read_text()))
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


class TestScope(unittest.TestCase):
    """Required in the store, unlike the consumer, which defaults them.

    The default is the PERMISSIVE pair, so an author who leaves these out is
    publishing a writable, stable-looking app without having said so. Making
    it a refusal is the only way that stays a decision.
    """

    def drop(self, key):
        return lambda src: re.sub(rf"^{key}: .*$\n", "", src, flags=re.M)

    def test_access_is_required(self):
        code, out = check(self.drop("access"))
        self.assertNotEqual(code, 0, out)
        self.assertIn("access is required", out)

    def test_maturity_is_required(self):
        code, out = check(self.drop("maturity"))
        self.assertNotEqual(code, 0, out)
        self.assertIn("maturity is required", out)

    def test_access_must_be_one_of_the_two(self):
        code, out = check(
            lambda src: src.replace("access: read-write", "access: write-only")
        )
        self.assertNotEqual(code, 0, out)
        self.assertIn("is not one of", out)

    def test_maturity_must_be_one_of_the_two(self):
        code, out = check(
            lambda src: src.replace("maturity: stable", "maturity: alpha")
        )
        self.assertNotEqual(code, 0, out)
        self.assertIn("is not one of", out)

    def test_the_other_values_are_accepted(self):
        code, out = check(
            lambda src: src.replace("access: read-write", "access: read-only").replace(
                "maturity: stable", "maturity: beta"
            )
        )
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

    # The consumer's checks are `!= ""`, so an explicitly empty field loads
    # there as absent. Refusing it here blocks a manifest that is fine.
    def test_an_empty_tint_is_no_tint(self):
        self.accepts("tint: ''\n")

    def test_an_empty_developer_url_is_no_url(self):
        self.accepts("developer_url: ''\n")

    # Three rounds of review found this one field at a time: tint, then the
    # icon, then developer_url. The consumer's guards are all `!= ""`, so
    # every optional field has to read empty as absent, and enumerating them
    # is what stops a fourth.
    def test_every_optional_field_reads_empty_as_absent(self):
        for field, empty in (
            ("developer", "developer: ''\n"),
            ("developer_url", "developer_url: ''\n"),
            ("tint", "tint: ''\n"),
            ("icon", "icon: {}\n"),
            ("icon.path and icon.view_box", "icon:\n  path: ''\n  view_box: ''\n"),
        ):
            with self.subTest(field=field):
                code, out = check(append(empty))
                self.assertEqual(code, 0, f"empty {field} refused:\n{out}")

    # YAML is typed and Go's decoder coerces, so a field's TYPE can differ
    # here from the string the consumer sees: `path: 0` is falsy in Python
    # and the non-empty "0" in Go. Vary the type, not just the emptiness.
    def test_a_scalar_is_read_as_the_string_the_consumer_sees(self):
        # The consumer sees the non-empty "0", so this is a whole icon whose
        # path happens to be a digit — accepted there, and now accepted here.
        # It was refused as half an icon while `not 0` decided the question.
        self.accepts("icon:\n  path: 0\n  view_box: '0 0 1 1'\n")
        # And "0" is not a colour, rather than an absent tint.
        self.refuses("tint: 0\n", "hex colour")
        self.refuses("developer: X\ndeveloper_url: 0\n", "https URL")

    def test_a_mapping_where_a_string_belongs_is_refused(self):
        self.refuses("tint:\n  r: 1\n", "hex colour")
        self.refuses(
            "icon:\n  path:\n    d: M0 0\n  view_box: '0 0 1 1'\n", "must be strings"
        )

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


# Nothing in the fixture signs, which is how a scheme the server accepts sat
# refused here until a real app tried to use it. These swap the fixture's
# inject delivery for a signing one.
class TestSigning(unittest.TestCase):
    INJECT = (
        "    delivery:\n"
        "      mode: inject\n"
        "      header: X-Api-Key\n"
    )

    def sign(self, scheme, encoding="hex"):
        block = (
            "    delivery:\n"
            "      mode: sign\n"
            f"      scheme: {scheme}\n"
            f"      encoding: {encoding}\n"
            "      match_body: true\n"
        )
        return lambda src: src.replace(self.INJECT, block)

    def test_every_scheme_the_server_dispatches_on(self):
        # Mirrors datastore.ValidSignScheme. A name here the server refuses is
        # an app that merges and vanishes; one there that is missing here is a
        # legitimate app blocked.
        for scheme in ("hmac-sha256", "ecdsa-p256", "stark", "secp256k1"):
            with self.subTest(scheme=scheme):
                code, out = check(self.sign(scheme))
                self.assertEqual(code, 0, out)

    def test_every_encoding_the_server_writes_back(self):
        for encoding in ("hex", "base64", "felt-pair"):
            with self.subTest(encoding=encoding):
                code, out = check(self.sign("stark", encoding))
                self.assertEqual(code, 0, out)

    def test_a_scheme_the_server_does_not_know_is_refused(self):
        code, out = check(self.sign("ed25519"))
        self.assertNotEqual(code, 0, out)
        self.assertIn("ed25519", out)

    def test_an_encoding_the_server_does_not_know_is_refused(self):
        code, out = check(self.sign("stark", "der"))
        self.assertNotEqual(code, 0, out)
        self.assertIn("der", out)


class TestScopeCaps(unittest.TestCase):
    """Set where the number stops being possible, mirroring the server."""

    def envs(self, n):
        rows = "".join(
            f"  - id: env{i}\n    label: Env {i}\n    host: h{i}.example.com\n"
            for i in range(n)
        )
        return lambda src: src.replace(
            "environments:\n  - id: mainnet\n    label: Mainnet\n    host: api.example.com\n",
            "environments:\n" + rows,
        )

    def test_a_hundred_hosts_is_fine(self):
        code, out = check(self.envs(100))
        self.assertEqual(code, 0, out)

    def test_one_host_over_is_refused(self):
        code, out = check(self.envs(101))
        self.assertNotEqual(code, 0, out)
        self.assertIn("101 environments", out)


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


class TestHelperVersion(unittest.TestCase):
    """The cached helper's filename carries the app version, and that IS the
    staleness mechanism.

    A skill tells the agent to write a helper to
    ~/.openclaw/workspace/tools/<app>/<skill>/<skill>-<version>.mjs and to reuse
    it when the name matches. Bump `version:` and leave the filename alone and
    every agent that already has one keeps a helper built from skill text that
    has since changed, for good, because nothing ever looks at it again. Nothing
    in the consumer reads this path, so this check is the only thing that can
    catch it.
    """

    HELPER = "tools/example/example-api/example-api-{}.mjs"

    def helper(self, version):
        return append(
            "\n## Helper\n\n```sh\n"
            "cat > ~/.openclaw/workspace/" + self.HELPER.format(version) + " <<'EOF'\n"
            "EOF\n```\n"
        )

    def test_a_helper_naming_the_manifest_version_is_accepted(self):
        code, out = check(skill=self.helper("1.0.0"))
        self.assertEqual(code, 0, out)

    def test_a_helper_naming_another_version_is_refused(self):
        code, out = check(skill=self.helper("0.9.0"))
        self.assertNotEqual(code, 0, out)
        self.assertIn("named for version '0.9.0'", out)
        self.assertIn("says '1.0.0'", out)

    def test_a_bumped_manifest_with_an_unchanged_filename_is_refused(self):
        code, out = check(
            edit=lambda s: s.replace("version: 1.0.0", "version: 1.0.1"),
            skill=self.helper("1.0.0"),
        )
        self.assertNotEqual(code, 0, out)
        self.assertIn("named for version '1.0.0'", out)

    def test_a_helper_under_another_skill_is_refused(self):
        code, out = check(
            skill=append(
                "\n```sh\n"
                "node ~/.openclaw/workspace/tools/example/other-api/other-api-1.0.0.mjs\n"
                "```\n"
            )
        )
        self.assertNotEqual(code, 0, out)
        self.assertIn("tools/example/other-api/", out)

    def test_a_helper_under_another_app_is_refused(self):
        """The skill name and the version can both be right and the app wrong.

        Nothing else catches this. The global skill-name check only refuses two
        apps CLAIMING one name, and a path naming an app that does not exist is
        not a claim. The helper would be written under a directory this skill
        never reads and re-derived on every chat.
        """
        code, out = check(
            skill=append(
                "\n```sh\n"
                "node ~/.openclaw/workspace/tools/other-app/example-api/example-api-1.0.0.mjs\n"
                "```\n"
            )
        )
        self.assertNotEqual(code, 0, out)
        self.assertIn("tools/other-app/example-api/", out)

    def test_one_failure_however_often_the_path_appears(self):
        """A skill names its helper on every call it demonstrates."""
        code, out = check(skill=lambda s: s + (
            "\n```sh\n" + ("node ~/.openclaw/workspace/"
                            + self.HELPER.format("0.9.0") + "\n") * 5 + "```\n"
        ))
        self.assertNotEqual(code, 0, out)
        self.assertEqual(out.count("named for version '0.9.0'"), 1, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
