#!/usr/bin/env python3
"""Unexpected options must be refused before any protected signer state access."""
import subprocess
import sys
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "roles/authentik_signer/files/authentik-signer.py"

class SignerCliTest(unittest.TestCase):
    def test_options_never_run_commands(self):
        for command in ("snapshot", "rotate", "publish", "rollback", "approve-contracts"):
            for option in ("--help", "--typo"):
                result = subprocess.run([sys.executable, str(SOURCE), command, option],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("this command accepts no arguments", result.stderr)

if __name__ == "__main__":
    unittest.main()
