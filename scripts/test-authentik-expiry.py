#!/usr/bin/env python3
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("expiry", Path(__file__).with_name("check-authentik-expiry.py"))
expiry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(expiry)


class ExpiryTests(unittest.TestCase):
    now = datetime(2026, 11, 12, tzinfo=timezone.utc)

    def check(self, content):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.yml"
            path.write_text(content)
            return expiry.check_file(path, self.now)

    def test_near_expiry(self):
        self.assertTrue(self.check('identities: [{expires: "2026-11-20T00:00:00Z"}]')[0][0])

    def test_far_expiry(self):
        self.assertFalse(self.check('identities: [{expires: "2027-02-15T00:00:00Z"}]')[0][0])

    def test_shared_reference_and_optional_identity(self):
        results = self.check('deadline: "2026-11-20T00:00:00Z"\noptional: {expires: "{{ deadline }}"}\n')
        self.assertTrue(results[0][0])

    def test_boundary_and_expired(self):
        for date in ("2026-12-04T00:00:00Z", "2026-11-01T00:00:00Z"):
            with self.subTest(date=date):
                self.assertTrue(self.check(f'expires: "{date}"')[0][0])

    def test_bad_or_unresolved_timestamp_fails(self):
        for value in ("tomorrow", "{{ missing }}", "2026-11-20T00:00:00"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.check(f'expires: "{value}"')

    def test_empty_operator_input_is_visible(self):
        due, message = self.check('expires: ""')[0]
        self.assertFalse(due)
        self.assertIn("not monitored", message)

    def test_yaml_timestamp(self):
        self.assertTrue(self.check('expires: 2026-11-20T00:00:00Z')[0][0])


if __name__ == "__main__":
    unittest.main()
