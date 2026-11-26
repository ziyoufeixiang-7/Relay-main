from __future__ import annotations

import math
import unittest

from diffmath.output import peak_to_rms, rms_to_peak
from diffmath.ratings import ct_ratio, kp_from_tap, tap_secondary_amperes


class RatingAndOutputTests(unittest.TestCase):
    def test_sel_style_tap_and_kp(self):
        tap = tap_secondary_amperes(
            power_mva=100.0,
            line_voltage_kv=110.0,
            ct_primary_amperes=1000.0,
            ct_secondary_amperes=1.0,
            ct_connection="wye",
        )
        expected = 100.0 * 1000.0 / (math.sqrt(3.0) * 110.0 * 1000.0)
        self.assertAlmostEqual(tap, expected)
        self.assertAlmostEqual(kp_from_tap(tap) * tap, 1.0)
        self.assertEqual(ct_ratio(1000.0, 1.0), 1000.0)

    def test_rms_peak_round_trip_is_explicit(self):
        value = 3.25
        self.assertAlmostEqual(peak_to_rms(rms_to_peak(value)), value)
        self.assertAlmostEqual(rms_to_peak(value), value * math.sqrt(2.0))


if __name__ == "__main__":
    unittest.main()
