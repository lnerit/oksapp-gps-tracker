import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import gps_sender


class GpsTests(unittest.TestCase):
    def test_valid_nmea_rmc(self):
        # Standard NMEA example. The coordinates are fictional test values.
        fix = gps_sender.make_payload(
            '$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A'
        )
        self.assertIsNotNone(fix)
        self.assertAlmostEqual(fix['latitude'], 48.1173)
        self.assertAlmostEqual(fix['longitude'], 11.5166667, places=6)
        self.assertAlmostEqual(fix['speedKmh'], 41.48, places=2)
        self.assertEqual(fix['recordedAtUtc'], '1994-03-23T12:35:19Z')

    def test_invalid_nmea_ignored(self):
        self.assertIsNone(gps_sender.make_payload('$GPRMC,bad'))
        self.assertIsNone(gps_sender.make_payload('$GPRMC,123519,V,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*7D'))

    def test_queue_is_persistent_until_deleted(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(gps_sender, 'DATABASE_PATH', Path(d) / 'queue.db'):
                gps_sender.init_db()
                payload = {"eventId": "test-id", "latitude": -6.3}
                gps_sender.enqueue(payload)
                row = gps_sender.oldest_pending()
                self.assertEqual(json.loads(row[1]), payload)
                self.assertEqual(gps_sender.pending_count(), 1)
                gps_sender.delete_uploaded(row[0])
                self.assertEqual(gps_sender.pending_count(), 0)


if __name__ == '__main__':
    unittest.main()
