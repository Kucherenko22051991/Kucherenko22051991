import unittest

from omnis.research.illia_tracker import evaluate, summarize

H4 = 14_400_000
T0 = 1_000 * H4


def bar(i, o, h, l, c):
    return (T0 + i * H4, o, h, l, c)


def call(side="LONG", lo=95.0, hi=100.0, stop=90.0, target=None):
    return {"id": 1, "video_ms": T0, "video_utc": "2026-11-28T12:00", "symbol": "X",
            "side": side, "lo": lo, "hi": hi, "stop": stop, "target": target, "note": ""}


class TrackerTest(unittest.TestCase):
    def test_waits_until_zone_touched(self):
        r = evaluate(call(), [bar(0, 110, 112, 105, 108)])
        self.assertEqual(r["status"], "WAITING")
        self.assertEqual(r["last"], 108)

    def test_fill_at_mid_then_target_2r(self):
        bars = [bar(0, 110, 111, 97, 99), bar(1, 99, 120, 98, 118)]
        r = evaluate(call(), bars)
        self.assertEqual(r["entry"], 97.5)
        self.assertEqual(r["status"], "TARGET")
        self.assertAlmostEqual(r["target"], 97.5 + 2 * 7.5)
        self.assertEqual(r["r"], 2.0)

    def test_stop_wins_when_both_touched(self):
        bars = [bar(0, 110, 111, 99, 99), bar(1, 99, 130, 85, 100)]
        r = evaluate(call(), bars)
        self.assertEqual(r["status"], "STOP")
        self.assertEqual(r["r"], -1.0)

    def test_fill_candle_through_stop_is_stop(self):
        r = evaluate(call(), [bar(0, 110, 111, 80, 85)])
        self.assertEqual(r["status"], "STOP")

    def test_default_stop_and_short(self):
        c = call(side="SHORT", lo=100, hi=102, stop=None)
        r = evaluate(c, [bar(0, 95, 100.5, 94, 100), bar(1, 100, 100.5, 80, 81)])
        self.assertEqual(r["stop_src"], "умовний")
        self.assertAlmostEqual(r["stop"], 102 * 1.03)
        self.assertEqual(r["entry"], 100)
        self.assertEqual(r["status"], "TARGET")

    def test_open_marked_to_market_and_expiry(self):
        r = evaluate(call(), [bar(0, 110, 111, 99, 99), bar(1, 99, 104, 98, 103)])
        self.assertEqual(r["status"], "OPEN")
        self.assertEqual(r["entry"], 100)
        self.assertAlmostEqual(r["r"], 0.3, places=2)
        far = [bar(0, 110, 111, 105, 108), bar(22 * 6, 108, 109, 96, 97)]
        self.assertEqual(evaluate(call(), far)["status"], "EXPIRED")

    def test_summary(self):
        res = [{"status": "STOP", "r": -1.0, "max_r": 0.2},
               {"status": "TARGET", "r": 2.0, "max_r": 2.0},
               {"status": "WAITING", "r": None, "max_r": None}]
        s = summarize(res)
        self.assertEqual((s["targets"], s["stops"], s["waiting"]), (1, 1, 1))
        self.assertEqual(s["win_rate"], 50.0)
        self.assertEqual(s["closed_r"], 1.0)


if __name__ == "__main__":
    unittest.main()
