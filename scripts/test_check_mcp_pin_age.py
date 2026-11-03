"""Tests for the pins check_mcp_pin_age.py reads off an MCP server's launch.

    python3 scripts/test_check_mcp_pin_age.py
"""

import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_mcp_pin_age  # noqa: E402
from check_mcp_pin_age import pins  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc)


class TestPins(unittest.TestCase):
    def test_a_scoped_npx_pin(self):
        self.assertEqual(pins("npx", ["-y", "@tealstreet/exchange-mcp@0.2.0"]),
                         [("npm", "@tealstreet/exchange-mcp", "0.2.0")])

    def test_an_unscoped_npx_pin(self):
        self.assertEqual(pins("npx", ["-y", "left-pad@1.3.0"]), [("npm", "left-pad", "1.3.0")])

    def test_uvx_pins_in_from_and_with(self):
        self.assertEqual(
            pins("uvx", ["--from", "some-server==1.2.0", "--with", "httpx[http2]==0.28.1",
                         "some-server"]),
            [("pypi", "some-server", "1.2.0"), ("pypi", "httpx", "0.28.1")])

    def test_our_own_packages_are_exempt(self):
        self.assertEqual(pins("uvx", ["--from", "mcp-paradigm==0.1.4", "--with",
                                      "paradex-py==0.8.2", "mcp-paradigm"]), [])

    def test_a_url_source_is_not_a_registry_pin(self):
        self.assertEqual(pins("uvx", ["--from", "x @ https://example.com/x.tar.gz", "x"]), [])



class TestMain(unittest.TestCase):
    """main() over one app pinning @x/server@1.0.0, with the registry stubbed."""

    def run_with(self, published, allowed: str = "") -> int:
        with tempfile.TemporaryDirectory() as d:
            exceptions = Path(d, "exceptions.txt")
            exceptions.write_text(allowed)
            app = Path(d, "apps", "x")
            app.mkdir(parents=True)
            (app / "app.yaml").write_text(
                "mcp_server:\n  command: npx\n  args: [\"-y\", \"@x/server@1.0.0\"]\n")
            cwd = os.getcwd()
            os.chdir(d)
            try:
                with mock.patch.object(check_mcp_pin_age, "published", published), \
                        mock.patch.object(check_mcp_pin_age, "EXCEPTIONS", exceptions), \
                        redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                    code = check_mcp_pin_age.main()
            finally:
                os.chdir(cwd)
        self.err = err.getvalue()
        self.out = out.getvalue()
        return code

    def test_an_old_pin_passes(self):
        self.assertEqual(self.run_with(lambda *_: NOW - dt.timedelta(days=30)), 0)

    def test_a_young_pin_fails(self):
        self.assertEqual(self.run_with(lambda *_: NOW - dt.timedelta(days=3)), 1)
        self.assertIn("@x/server 1.0.0", self.err)

    def test_a_young_pin_with_an_exception_passes_and_is_named(self):
        code = self.run_with(lambda *_: NOW - dt.timedelta(days=3),
                             allowed="@x/server 1.0.0 security fix\n")
        self.assertEqual(code, 0)
        self.assertIn("excepted", self.out)

    def test_an_exception_without_a_reason_is_refused(self):
        with self.assertRaises(SystemExit):
            check_mcp_pin_age.exceptions("@x/server 1.0.0\n")

    def test_an_unreadable_pin_fails(self):
        def boom(*_):
            raise OSError("registry down")
        self.assertEqual(self.run_with(boom), 1)
        self.assertIn("unreadable", self.err)


class TestPublished(unittest.TestCase):
    def test_a_pypi_release_is_dated_by_its_first_file(self):
        body = json.dumps({"urls": [
            {"upload_time_iso_8601": "2026-10-02T00:00:00Z"},
            {"upload_time_iso_8601": "2026-10-01T00:00:00Z"}]}).encode()
        with mock.patch("urllib.request.urlopen", return_value=io.BytesIO(body)):
            at = check_mcp_pin_age.published("pypi", "x", "1.0")
        self.assertEqual(at, dt.datetime(2026, 10, 1, tzinfo=dt.timezone.utc))


if __name__ == "__main__":
    unittest.main(verbosity=2)
