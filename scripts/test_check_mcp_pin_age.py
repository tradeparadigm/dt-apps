"""Tests for the pins check_mcp_pin_age.py reads off an MCP server's launch.

    python3 scripts/test_check_mcp_pin_age.py
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_mcp_pin_age import pins  # noqa: E402


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
