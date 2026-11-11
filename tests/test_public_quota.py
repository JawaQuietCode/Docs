import tempfile
import time
import unittest
from pathlib import Path

from security import public_quota


class PublicQuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="kwee-public-quota-")
        self.config = {"root": str(Path(self.temp_dir.name))}

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_quota_is_separate_per_identity(self):
        first = "6281111111111@s.whatsapp.net"
        second = "6282222222222@s.whatsapp.net"
        now = 1_700_000_000

        self.assertEqual(public_quota.usage(first, config=self.config, now=now), 0)
        self.assertEqual(public_quota.record(first, 1200, config=self.config, now=now), 1200)
        self.assertEqual(public_quota.usage(second, config=self.config, now=now), 0)
        self.assertEqual(public_quota.usage(first, config=self.config, now=now + 1), 1200)

    def test_quota_uses_rolling_window(self):
        user = "6283333333333@s.whatsapp.net"
        now = int(time.time())

        public_quota.record(user, 500, config=self.config, now=now - 129601)
        public_quota.record(user, 250, config=self.config, now=now - 100)
        self.assertEqual(
            public_quota.usage(user, window_seconds=129600, config=self.config, now=now),
            250,
        )

    def test_status_returns_remaining_expiry_marker(self):
        user = "6284444444444@s.whatsapp.net"
        now = 1_700_000_000
        public_quota.record(user, 17, config=self.config, now=now)
        result = public_quota.status(user, window_seconds=129600, config=self.config, now=now + 1)
        self.assertEqual(result["used"], 17)
        self.assertEqual(result["reset_at"], now + 129600)


if __name__ == "__main__":
    unittest.main()
