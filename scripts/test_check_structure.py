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
from check_structure import MAX_SKILLS, MCP_RESERVED_ENV  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "scripts" / "testdata" / "apps"
MANIFEST = "apps/example/app.yaml"
SKILL = "apps/example/skills/example-api/SKILL.md"


def skill_md(name: str) -> str:
    return f"---\nname: {name}\ndescription: What this skill is for.\n---\n\nBody.\n"


def check(edit=None, skill=None, extra=None, remove=None) -> tuple[int, str]:
    """Run the real checker over a copy of the fixture, optionally edited.

    `edit` rewrites the manifest and `skill` rewrites the skill body, because
    some rules are about the two agreeing. `extra` writes additional files,
    keyed by path relative to the tree, which is how a case adds a second
    skill. `remove` deletes paths, which is how a case takes one away.
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

    def sign(self, scheme, encoding="hex", key_encoding=None):
        block = (
            "    delivery:\n"
            "      mode: sign\n"
            f"      scheme: {scheme}\n"
            f"      encoding: {encoding}\n"
            + (f"      key_encoding: {key_encoding}\n" if key_encoding else "")
            + "      match_body: true\n"
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

    # key_encoding is how the stored key is READ before signing with it, which
    # `encoding` does not cover: `encoding` is how the signature is written
    # back. Paradigm and Kraken issue a base64 key, Bybit and Binance issue one
    # whose characters are the key, and guessing is not available because a
    # printable secret whose length is a multiple of four is also valid base64
    # and decodes to unrelated bytes.
    def test_every_key_encoding_the_server_reads(self):
        for key_encoding in ("raw", "base64"):
            with self.subTest(key_encoding=key_encoding):
                code, out = check(self.sign("hmac-sha256", "base64", key_encoding))
                self.assertEqual(code, 0, out)

    def test_an_absent_key_encoding_is_accepted(self):
        """Absent means raw, so every credential stored before it existed works."""
        code, out = check(self.sign("hmac-sha256", "base64"))
        self.assertEqual(code, 0, out)

    def test_a_key_encoding_the_server_does_not_know_is_refused(self):
        code, out = check(self.sign("hmac-sha256", "base64", "rot13"))
        self.assertNotEqual(code, 0, out)
        self.assertIn("rot13", out)

    def test_a_key_encoding_on_a_scheme_that_never_reads_one_is_refused(self):
        """datastore.checkSignShape: only the MAC scheme reads a key encoding.

        The others sign with a key whose bytes are not a question, so a manifest
        naming one there describes a step that does not happen.
        """
        code, out = check(self.sign("stark", "felt-pair", "base64"))
        self.assertNotEqual(code, 0, out)
        self.assertIn("hmac-sha256", out)

    def test_a_key_encoding_outside_sign_mode_is_refused(self):
        """The other modes carry no key, so there is no encoding for one."""
        code, out = check(
            lambda src: src.replace(
                "      mode: inject\n      header: X-Api-Key\n",
                "      mode: inject\n      header: X-Api-Key\n      key_encoding: base64\n",
            )
        )
        self.assertNotEqual(code, 0, out)
        self.assertIn("key_encoding", out)


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


class TestMcpServer(unittest.TestCase):
    """mcp_server, checked against the app's own credential types and environments.

    The fixture's api-key injects its secret and so has no placeholder. The
    token type added here replaces one, which is what ${placeholder} needs,
    and declares public_key, which api-key does not.
    """

    TOKEN = (
        "  - id: token\n"
        "    label: Token\n"
        "    slug: token\n"
        "    secret_label: Token\n"
        "    detail_fields:\n"
        "      - key: account\n"
        "        label: Account\n"
        "      - key: public_key\n"
        "        label: Public key\n"
        "    delivery:\n"
        "      mode: replace\n"
        "    summary: Swaps the placeholder for the token.\n"
    )
    VALID = (
        "mcp_server:\n"
        "  command: uvx\n"
        "  args: [--from, 'git+https://example.com/x@abc', x-server]\n"
        "  tools: [x_read, x_write]\n"
        "  credential_types:\n"
        "    - type: token\n"
        "      env:\n"
        "        X_ACCOUNT: ${detail.account}\n"
        "        X_TOKEN: ${placeholder}\n"
        "        X_HEADER: X-Dime-Sign-${label}\n"
        "    - type: api-key\n"
        "      env:\n"
        "        X_ACCOUNT: ${detail.account}\n"
        "  environments:\n"
        "    - id: mainnet\n"
        "      env:\n"
        "        X_ENV: prod\n"
    )

    def run_with(self, old="", new=""):
        return check(append(self.TOKEN + self.VALID.replace(old, new)))

    def refuses(self, old, new, because):
        code, out = self.run_with(old, new)
        self.assertNotEqual(code, 0, out)
        self.assertIn(because, out)

    def test_a_full_server_is_accepted(self):
        code, out = self.run_with()
        self.assertEqual(code, 0, out)

    def test_a_bare_command_is_accepted(self):
        code, out = check(append("mcp_server:\n  command: x-server\n"))
        self.assertEqual(code, 0, out)

    def test_command_is_required(self):
        self.refuses("  command: uvx\n", "", "mcp_server.command is required")

    def test_args_must_be_strings(self):
        self.refuses("x-server]", "{a: b}]", "mcp_server.args must be a list of strings")

    def test_an_unknown_key_is_refused(self):
        self.refuses("  command: uvx\n", "  command: uvx\n  url: http://x\n",
                     "mcp_server.url is not a field the consumer knows")

    def test_an_unknown_nested_key_is_refused(self):
        self.refuses("    - id: mainnet\n", "    - id: mainnet\n      host: x\n",
                     "mcp_server.environments[0].host is not a field")

    def test_mcp_server_must_be_a_mapping(self):
        code, out = check(append("mcp_server: 5\n"))
        self.assertNotEqual(code, 0, out)
        self.assertIn("mcp_server is not a mapping", out)

    def test_an_unknown_key_in_a_credential_type_is_refused(self):
        self.refuses("    - type: api-key\n", "    - type: api-key\n      slug: key\n",
                     "mcp_server.credential_types[1].slug is not a field")

    def test_an_empty_tool_list_is_refused(self):
        self.refuses("  tools: [x_read, x_write]\n", "  tools: []\n",
                     "mcp_server.tools is empty: omit it")

    def test_a_value_cannot_hold_a_nul_or_a_line_break(self):
        for value in ('"a\\x00b"', '"a\\nb"', '"a\\rb"'):
            for old in ("X_ENV: prod", "X_HEADER: X-Dime-Sign-${label}"):
                with self.subTest(value=value, where=old):
                    self.refuses(old, old.split(":")[0] + ": " + value,
                                 "may not contain a NUL or a line break")

    def test_an_env_name_with_a_trailing_newline_is_refused(self):
        self.refuses("X_ENV: prod", '"X_ENV\\n": prod', "is not an environment variable name")

    def test_a_tool_name_cannot_be_empty(self):
        self.refuses("x_write]", "'']", "mcp_server.tools[1] must be a non-empty tool name")

    def test_a_tool_is_listed_once(self):
        self.refuses("x_write]", "x_read]", "mcp_server.tools[1] 'x_read' is listed twice")

    def test_an_env_name_must_be_upper_case(self):
        self.refuses("X_ENV:", "x_env:", "x_env is not an environment variable name")

    def test_an_env_name_has_at_most_64_characters(self):
        self.refuses("X_ENV:", "X" * 65 + ":", "is not an environment variable name")

    def test_a_reserved_env_name_is_refused(self):
        for name in sorted(MCP_RESERVED_ENV):
            with self.subTest(name=name):
                self.refuses("X_ENV:", f"{name}:", f"{name} is set by the sidecar")

    def test_a_credential_type_must_be_the_apps(self):
        self.refuses("type: api-key", "type: jwt", "credential_types[1].type 'jwt' is not one of")

    def test_a_credential_type_is_listed_once(self):
        self.refuses("type: api-key", "type: token", "credential_types.type[1] 'token' is listed twice")

    def test_an_environment_must_be_the_apps(self):
        self.refuses("id: mainnet", "id: testnet", "environments[0].id 'testnet' is not one of")

    def test_an_environment_is_listed_once(self):
        self.refuses("        X_ENV: prod\n",
                     "        X_ENV: prod\n    - id: mainnet\n",
                     "environments.id[1] 'mainnet' is listed twice")

    def test_a_detail_must_be_one_the_type_declares(self):
        self.refuses("X_ACCOUNT: ${detail.account}\n  environments",
                     "X_ACCOUNT: ${detail.public_key}\n  environments",
                     "names ${detail.public_key}, and 'api-key' has no such detail field")

    def test_a_placeholder_needs_a_mode_that_has_one(self):
        self.refuses("    - type: api-key\n      env:\n",
                     "    - type: api-key\n      env:\n        X_KEY: ${placeholder}\n",
                     "mode 'inject' has no placeholder")

    def test_any_other_dollar_is_refused(self):
        for value in ("$HOME", "${secret}", "${detail.account", "$${label}"):
            with self.subTest(value=value):
                self.refuses("X_TOKEN: ${placeholder}", f"X_TOKEN: '{value}'",
                             "has a $ outside")

    def test_an_environment_value_is_a_literal(self):
        self.refuses("X_ENV: prod", "X_ENV: ${label}", "env.X_ENV is a literal")


if __name__ == "__main__":
    unittest.main(verbosity=2)
