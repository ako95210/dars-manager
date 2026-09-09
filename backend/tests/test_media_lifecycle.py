from __future__ import annotations

import unittest
from datetime import timedelta

from backend.app.media_lifecycle import cumulative_storage_units
from backend.app.models import Asset, utc_now


class MediaLifecycleTests(unittest.TestCase):
    def test_one_decimal_gigabyte_for_thirty_days_is_one_gb_month(self) -> None:
        started = utc_now()
        asset = Asset(
            size_bytes=1_000_000_000,
            uploaded_at=started,
            expires_at=started + timedelta(days=60),
        )
        self.assertEqual(
            cumulative_storage_units(asset, started + timedelta(days=30)),
            1_000_000,
        )

    def test_meter_never_counts_beyond_expiration(self) -> None:
        started = utc_now()
        asset = Asset(
            size_bytes=500_000_000,
            uploaded_at=started,
            expires_at=started + timedelta(days=30),
        )
        self.assertEqual(
            cumulative_storage_units(asset, started + timedelta(days=90)),
            500_000,
        )


if __name__ == "__main__":
    unittest.main()
