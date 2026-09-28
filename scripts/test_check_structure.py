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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_structure import MAX_SKILLS  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "scripts" / "testdata" / "apps"
MANIFEST = "apps/example/app.yaml"


def skill_md(name: str) -> str:
    return f"---\nname: {name}\ndescription: What this skill is for.\n---\n\nBody.\n"


def check(edit=None, extra=None, remove=None) -> tuple[int, str]:
    """Run the real checker over a copy of the fixture, optionally edited.

    `edit` rewrites the manifest. `extra` writes additional files, keyed by path
    relative to the tree, which is how a case adds a second skill. `remove`
    deletes paths, which is how a case takes one away.
    """
    with tempfile.TemporaryDirectory() as d:
        tree = Path(d)
        shutil.copytree(FIXTURE, tree / "apps")
        shutil.copytree(ROOT / "scripts", tree / "scripts")
        if edit is not None:
            man = tree / MANIFEST
            man.write_text(edit(man.read_text()))
        for rel, body in (extra or {}).items():
            f = tree / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(body)
        for rel in remove or ():
            target = tree / rel
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
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


class TestSeveralSkills(unittest.TestCase):
    """An app may teach more than one thing.

    The cap used to be one, because the agent's publish path wrote an app's files
    to <skills>/apps/<id>/ and looked for SKILL.md at that root. It takes nested
    paths now, so each skill's files are published under its own name.
    """

    def skills(self, *names):
        return {
            f"apps/example/skills/{n}/SKILL.md": skill_md(n) for n in names
        }

    def test_a_second_skill_is_accepted(self):
        code, out = check(extra=self.skills("example-mcp"))
        self.assertEqual(code, 0, out)
        self.assertIn("example-api", out)
        self.assertIn("example-mcp", out)

    def test_exactly_the_limit_is_accepted(self):
        # The fixture already ships one, so the limit is that plus MAX_SKILLS-1.
        names = [f"example-s{i:02d}" for i in range(MAX_SKILLS - 1)]
        code, out = check(extra=self.skills(*names))
        self.assertEqual(code, 0, out)

    def test_one_over_the_limit_is_refused(self):
        names = [f"example-s{i:02d}" for i in range(MAX_SKILLS)]
        code, out = check(extra=self.skills(*names))
        self.assertNotEqual(code, 0, out)
        self.assertIn(f"limit {MAX_SKILLS}", out)

    def test_a_second_skill_still_needs_its_entrypoint(self):
        """Every skill directory is checked, and not only the first."""
        code, out = check(extra={"apps/example/skills/example-mcp/notes.md": "no entrypoint"})
        self.assertNotEqual(code, 0, out)
        self.assertIn("SKILL.md", out)

    def test_a_second_skill_must_declare_its_own_name(self):
        code, out = check(
            extra={"apps/example/skills/example-mcp/SKILL.md": skill_md("something-else")}
        )
        self.assertNotEqual(code, 0, out)
        self.assertIn("something-else", out)

    def test_zero_skills_is_still_refused(self):
        """A publish is a full replacement, so an app with no files is dropped
        from the set, and that is byte-identical to one whose files did not
        survive the fetch."""
        code, out = check(remove=["apps/example/skills"])
        self.assertNotEqual(code, 0, out)
        self.assertIn("no skills/", out)


class TestDefaultInstall(unittest.TestCase):
    """A key the consumer knows, so the checker has to know it too.

    The decoder over there runs with KnownFields, so a key this accepts and a
    deployed build does not drops the whole app from that build's catalogue.
    """

    def test_default_install_is_accepted(self):
        code, out = check(append("default_install: true\n"))
        self.assertEqual(code, 0, out)

    def test_false_is_accepted(self):
        code, out = check(append("default_install: false\n"))
        self.assertEqual(code, 0, out)

    def test_a_key_the_consumer_does_not_know_is_still_refused(self):
        """The guard this relies on, so relaxing one key did not open the set."""
        code, out = check(append("default_instal: true\n"))
        self.assertNotEqual(code, 0, out)
        self.assertIn("default_instal", out)

    # A known key whose VALUE is the wrong kind is the same failure as an unknown
    # key: the consumer's decoder refuses the whole app and it goes missing from
    # the catalogue. The known-key check alone does not see it.
    def test_a_value_the_consumer_cannot_decode_is_refused(self):
        for bad in ("banana", "1", "0", '"true"', "tRuE"):
            with self.subTest(value=bad):
                code, out = check(append(f"default_install: {bad}\n"))
                self.assertNotEqual(code, 0, out)
                self.assertIn("default_install", out)

    # Measured by running yaml.v3 over each token. It takes all three case forms
    # of true and false, and also yes, no, on and off.
    def test_every_token_the_consumer_reads_as_a_boolean(self):
        for good in ("true", "False", "TRUE", "yes", "no", "on", "off"):
            with self.subTest(value=good):
                code, out = check(append(f"default_install: {good}\n"))
                self.assertEqual(code, 0, out)

    # THE ONE DIVERGENCE, pinned so it is a decision and not a surprise. yaml.v3
    # reads bare y and n as booleans and PyYAML does not, so these are refused
    # here and would be accepted there. Stricter costs a legitimate app, and a
    # one-letter boolean is not a style anyone writes; matching it would need a
    # custom loader to see the raw token. Named in BOOLEAN_KEYS' comment too.
    def test_single_letter_booleans_are_refused_here_and_that_is_known(self):
        for letter in ("y", "n"):
            with self.subTest(value=letter):
                code, out = check(append(f"default_install: {letter}\n"))
                self.assertNotEqual(code, 0, out)

    # null and ~ decode to false over there, so blocking them would refuse an app
    # the consumer is happy with.
    def test_null_is_accepted_because_the_consumer_reads_it_as_false(self):
        for empty in ("null", "~"):
            with self.subTest(value=empty):
                code, out = check(append(f"default_install: {empty}\n"))
                self.assertEqual(code, 0, out)

    # The same gap existed on the other boolean, and it is the same one line.
    def test_exclusive_credential_types_is_checked_too(self):
        code, out = check(
            lambda s: s.replace("exclusive_credential_types: true",
                                "exclusive_credential_types: banana")
        )
        self.assertNotEqual(code, 0, out)
        self.assertIn("exclusive_credential_types", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
