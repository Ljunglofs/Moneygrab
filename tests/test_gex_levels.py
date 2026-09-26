"""Tester för GEX-nivåernas översättning till futures.

Kör:  python -m unittest discover -s tests -v

Fallet som fick det här att skrivas: den 25 september 13:07 låg NQ på
30 920 men Call Wall rapporterades till 30 495 och expected move till ±24.
Två fel samverkade — en sparad basis med fel tecken (-174,75) och att
expected move/IV-range fick basisen påslagen som om de vore prisnivåer.
"""
import os
import sys
import unittest
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gex as GX  # noqa: E402


def _levels(spot=30418.65, **kw):
    """Minimal nivåtabell i indexpunkter, som gex_from_chain lämnar ifrån sig."""
    lv = {"spot": spot, "net_gex": 1.0, "net_gex_all": 1.0, "regime": "positiv",
          "call_wall": 30670.0, "put_wall": 29500.0, "zero_gamma": 30310.5,
          "top": [], "hgex": 30670.0, "gpos": [], "gneg": [],
          "call_wall_0": 30670.0, "put_wall_0": 30275.0, "max_pain": 30260.0,
          "em_straddle": 199.07, "iv_1d": 352.0, "near_expiry": "2026-09-25"}
    lv.update(kw)
    return lv


class FrontExpiry(unittest.TestCase):
    def test_third_friday(self):
        self.assertEqual(GX._third_friday(2026, 12), date(2026, 12, 18))
        self.assertEqual(GX._third_friday(2026, 9), date(2026, 9, 18))
        self.assertEqual(GX._third_friday(2027, 3), date(2027, 3, 19))

    def test_front_contract(self):
        # Efter septemberförfallet: december.
        self.assertEqual(GX.front_expiry(date(2026, 9, 25)), date(2026, 12, 18))
        # Mindre än en vecka kvar till december: marsen har tagit över.
        self.assertEqual(GX.front_expiry(date(2026, 12, 14)), date(2027, 3, 19))


class Basis(unittest.TestCase):
    TODAY = date(2026, 9, 25)
    SPOT = 30418.65

    def test_carry_is_positive_and_sized_right(self):
        b = GX.carry_basis(self.SPOT, self.TODAY)
        # 30 419 * (4 % - 0,7 %) * 84/365 ~ 231
        self.assertGreater(b, 150)
        self.assertLess(b, 320)

    def test_wrong_sign_basis_is_rejected(self):
        self.assertFalse(GX.basis_ok(-174.75, self.SPOT, self.TODAY))

    def test_plausible_basis_is_kept(self):
        good = GX.carry_basis(self.SPOT, self.TODAY) + 40
        self.assertTrue(GX.basis_ok(good, self.SPOT, self.TODAY))
        self.assertEqual(GX.pick_basis(good, self.SPOT, self.TODAY), (good, "live"))

    def test_pick_basis_falls_back_to_carry(self):
        b, src = GX.pick_basis(-174.75, self.SPOT, self.TODAY)
        self.assertEqual(src, "carry")
        self.assertAlmostEqual(b, GX.carry_basis(self.SPOT, self.TODAY))

    def test_roll_week_accepts_either_contract(self):
        # Tre dagar före decemberförfallet kan serien fortfarande visa december
        # (liten basis) eller redan mars (stor basis) — båda är rätt mätningar.
        d = date(2026, 12, 15)
        near = GX.carry_basis(self.SPOT, d, date(2026, 12, 18))
        nxt = GX.carry_basis(self.SPOT, d, date(2027, 3, 19))
        self.assertTrue(GX.basis_ok(near, self.SPOT, d))
        self.assertTrue(GX.basis_ok(nxt, self.SPOT, d))

    def test_none_is_not_ok(self):
        self.assertFalse(GX.basis_ok(None, self.SPOT, self.TODAY))
        self.assertFalse(GX.basis_ok(100.0, None, self.TODAY))


class ToFutures(unittest.TestCase):
    def test_widths_are_not_shifted_by_basis(self):
        f = GX.to_futures(_levels(), 30920.75, 30418.65, basis=-174.75)
        # Prisnivåer flyttas med basisen ...
        self.assertAlmostEqual(f["call_wall"], 30670.0 - 174.75)
        # ... men expected move och IV-range är avstånd och ska vara orörda.
        self.assertAlmostEqual(f["em"], 199.07)
        self.assertAlmostEqual(f["iv_1d"], 352.0)

    def test_widths_scale_with_ratio(self):
        lv = _levels(spot=394.64, call_wall=400.0, em_straddle=4.0, iv_1d=6.0)
        f = GX.to_futures(lv, 4347.0, 394.64, ratio=10.9884)
        self.assertAlmostEqual(f["call_wall"], round(400.0 * 10.9884, 2))
        self.assertAlmostEqual(f["em"], round(4.0 * 10.9884, 2))
        self.assertAlmostEqual(f["iv_1d"], round(6.0 * 10.9884, 2))

    def test_regression_2026_09_25(self):
        """Samma indata som körningen 25/9 13:07, men med basisen avstämd."""
        today = date(2026, 9, 25)
        b, src = GX.pick_basis(-174.75, 30418.65, today)
        self.assertEqual(src, "carry")
        f = GX.to_futures(_levels(), 30920.75, 30418.65, basis=b)
        fut = 30920.75
        # Call Wall hamnar i närheten av priset, inte 425 punkter under det.
        self.assertLess(abs(f["call_wall"] - fut), 150)
        # Expected move är en rimlig dagsrörelse för NQ, inte ±24.
        self.assertGreater(f["em"], 100)
        s = GX.levels_string("NQ", {"futures": f}, today=today)
        em_hi = float(s.split("EM+")[0].rsplit(";", 1)[-1].rstrip(","))
        em_lo = float(s.split("EM-")[0].rsplit(";", 1)[-1].rstrip(","))
        self.assertGreater(em_hi, fut)
        self.assertLess(em_lo, fut)


class ChainSanity(unittest.TestCase):
    def test_call_wall_is_largest_positive_strike(self):
        rows = []
        for k, oi in ((100, 50), (105, 900), (110, 200)):
            rows.append({"strike": k, "oi": oi, "iv": 0.2, "type": "C", "expiry": "2099-01-15"})
        for k, oi in ((90, 800), (95, 100)):
            rows.append({"strike": k, "oi": oi, "iv": 0.2, "type": "P", "expiry": "2099-01-15"})
        lv = GX.gex_from_chain(100.0, rows)
        self.assertEqual(lv["call_wall"], 105)
        self.assertEqual(lv["put_wall"], 90)


if __name__ == "__main__":
    unittest.main()
