"""Tester för Binance-klientens reserv vid geospärr (HTTP 451).

Kör:  python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from unittest import mock

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import binance_client as BC  # noqa: E402


def _resp(status, body=None):
    r = requests.Response()
    r.status_code = status
    r._content = b"{}" if body is None else body.encode()
    return r


class SpotFallback(unittest.TestCase):
    def setUp(self):
        BC._blocked.clear()

    def test_451_falls_back_to_mirror_and_remembers(self):
        calls = []

        def fake_get(url, params=None, timeout=None):
            calls.append(url)
            return _resp(451) if "api.binance.com" in url else _resp(200, '{"ok": 1}')

        with mock.patch.object(BC.requests, "get", side_effect=fake_get):
            self.assertEqual(BC.spot_get("/api/v3/ping"), {"ok": 1})
            self.assertEqual(BC.spot_get("/api/v3/ping"), {"ok": 1})
        self.assertEqual(calls, [
            "https://api.binance.com/api/v3/ping",
            "https://data-api.binance.vision/api/v3/ping",
            "https://data-api.binance.vision/api/v3/ping",
        ])

    def test_other_errors_are_not_masked(self):
        with mock.patch.object(BC.requests, "get", return_value=_resp(400)):
            with self.assertRaises(requests.HTTPError):
                BC.spot_get("/api/v3/ticker/price", {"symbol": "NOPE"})
        self.assertEqual(BC._blocked, set())

    def test_futures_451_is_raised(self):
        with mock.patch.object(BC.requests, "get", return_value=_resp(451)):
            with self.assertRaises(requests.HTTPError):
                BC.futures_get("/fapi/v1/ping")


if __name__ == "__main__":
    unittest.main()
