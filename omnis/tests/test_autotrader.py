"""Tests for omnis.strategies.autotrader with a fake exchange (no network).

Run: python -m unittest omnis.tests.test_autotrader -v
"""
import json
import os
import tempfile
import time
import unittest
from unittest import mock

from omnis.strategies import autotrader as at
from omnis.strategies.donchian_live import BAR_MS, FILTERS_PATH, round_tick

FILTERS = json.load(open(FILTERS_PATH))


def trend_klines(n=300, start=1.0, step=0.002, drop_last=0.0):
    """n 4h bars ending with the still-forming bar; steady trend.
    drop_last: fractional drop applied to the last CLOSED bar (channel exit)."""
    cur = (int(time.time() * 1000) // BAR_MS) * BAR_MS
    out, px = [], start
    for i in range(n):
        o = px
        px = px * (1 + step)
        c = px
        if i == n - 2 and drop_last:
            c = px * (1 - drop_last)
        out.append({"time": cur - (n - 1 - i) * BAR_MS, "open": o,
                    "high": max(o, c) * 1.004, "low": min(o, c) * 0.996,
                    "close": c, "volume": 1000.0})
    return out


class FakeExchange:
    def __init__(self, klines, positions=None, algos=None, wallet=300.0, available=300.0,
                 fill_factor=0.99, fail=()):
        self.k = klines
        self.pos = positions or {}       # sym -> dict(amt, entry, mark, updateTime)
        self.algos = algos or []
        self.wallet, self.available = wallet, available
        self.fill_factor = fill_factor
        self.fail = set(fail)
        self.calls = []
        self.next_id = 1000

    # reads
    def sync_time(self):
        pass

    def usdt_balance(self):
        return self.wallet, 0.0, self.available

    def positions(self, symbol=None):
        out = []
        for s in at.ALPHA:
            if symbol and s != symbol:
                continue
            p = self.pos.get(s, {"amt": 0, "entry": 0, "mark": 0, "updateTime": 0})
            out.append({"symbol": s, "positionAmt": str(p["amt"]),
                        "entryPrice": str(p["entry"]), "markPrice": str(p["mark"]),
                        "updateTime": p["updateTime"]})
        return out

    def open_algos(self):
        return [dict(a) for a in self.algos]

    def klines(self, symbol):
        if symbol not in self.k:
            raise RuntimeError("no data")
        return self.k[symbol]

    # writes
    def set_leverage(self, symbol, lev):
        self.calls.append(("leverage", symbol, lev))

    def market(self, symbol, side, qty, reduce_only=False):
        self.calls.append(("market", symbol, side, float(qty), reduce_only))
        if reduce_only:
            self.pos[symbol] = {"amt": 0, "entry": 0, "mark": 0,
                                "updateTime": int(time.time() * 1000)}
        else:
            last_closed = self.k[symbol][-2]["close"]
            fill = last_closed * self.fill_factor
            amt = float(qty) if side == "BUY" else -float(qty)
            self.pos[symbol] = {"amt": amt, "entry": fill, "mark": fill,
                                "updateTime": int(time.time() * 1000)}
        return {"executedQty": str(qty), "status": "FILLED"}

    def new_algo(self, order):
        self.calls.append(("new_algo", order["symbol"], order["type"], float(order["triggerPrice"])))
        if order["type"] in self.fail:
            raise at.BinanceError(400, {"code": -1, "msg": "rejected"})
        self.next_id += 1
        a = {"algoId": self.next_id, "symbol": order["symbol"], "orderType": order["type"],
             "closePosition": True, "triggerPrice": at.fmt(order["triggerPrice"])}
        self.algos.append(a)
        return a

    def cancel_algo(self, algo_id):
        self.calls.append(("cancel_algo", algo_id))
        n = len(self.algos)
        self.algos = [a for a in self.algos if a["algoId"] != algo_id]
        if len(self.algos) == n:
            raise at.BinanceError(400, {"code": -2011, "msg": "Unknown order"})

    def cancel_all_algos(self, symbol):
        self.calls.append(("cancel_all_algos", symbol))
        self.algos = [a for a in self.algos if a["symbol"] != symbol]

    # helpers for assertions
    def writes(self):
        return self.calls

    def algo(self, symbol, typ):
        return [a for a in self.algos if a["symbol"] == symbol and a["orderType"] == typ]


def run(ex, live=True, universe=("BLESSUSDT",)):
    t = at.Trader(ex, FILTERS, live=live, log=lambda *_: None, universe=list(universe))
    return t, t.run_once()


class AutotraderTests(unittest.TestCase):
    def setUp(self):
        self.pause = mock.patch.object(at, "PAUSE_PATH", os.path.join(tempfile.mkdtemp(), "PAUSE"))
        self.pause.start()

    def tearDown(self):
        self.pause.stop()

    def test_dry_run_makes_no_write_calls(self):
        ex = FakeExchange({"BLESSUSDT": trend_klines()})
        t, rec = run(ex, live=False)
        self.assertEqual(ex.writes(), [])
        self.assertTrue(any("BUY" in a["action"] for a in rec["actions"]))
        self.assertTrue(all(a.get("dry_run") for a in rec["actions"]))

    def test_live_entry_places_stop_tp_and_rebases_stop(self):
        ex = FakeExchange({"BLESSUSDT": trend_klines()})
        t, rec = run(ex)
        kinds = [c[0] for c in ex.calls]
        self.assertEqual(kinds[:3], ["leverage", "market", "new_algo"])
        pos = ex.pos["BLESSUSDT"]
        self.assertGreater(pos["amt"], 0)
        stops, tps = ex.algo("BLESSUSDT", "STOP_MARKET"), ex.algo("BLESSUSDT", "TAKE_PROFIT_MARKET")
        self.assertEqual(len(stops), 1)
        self.assertEqual(len(tps), 1)
        stop, tp = float(stops[0]["triggerPrice"]), float(tps[0]["triggerPrice"])
        entry = pos["entry"]
        tick = FILTERS["BLESSUSDT"]["tick"]
        # take profit = 2 x stop distance from the real fill; stop re-based to the fill
        self.assertAlmostEqual(tp - entry, 2 * (entry - stop), delta=3 * float(tick))
        self.assertLess(stop, entry)
        self.assertEqual(stop, round_tick(stop, tick, up=False))

    def test_stop_rejected_closes_position_at_once(self):
        ex = FakeExchange({"BLESSUSDT": trend_klines()}, fail={"STOP_MARKET"})
        run(ex)
        self.assertEqual(ex.pos["BLESSUSDT"]["amt"], 0)
        self.assertIn(True, [c[4] for c in ex.calls if c[0] == "market"])   # reduceOnly close

    def test_audit_adds_missing_take_profit_at_2r(self):
        k = trend_klines()
        now = int(time.time() * 1000)
        ex = FakeExchange({"BLESSUSDT": k},
                          positions={"BLESSUSDT": {"amt": 100, "entry": 1.5, "mark": 1.52,
                                                   "updateTime": now - 3 * BAR_MS}},
                          algos=[{"algoId": 1, "symbol": "BLESSUSDT", "orderType": "STOP_MARKET",
                                  "closePosition": True, "triggerPrice": "1.4"}])
        run(ex)
        tps = ex.algo("BLESSUSDT", "TAKE_PROFIT_MARKET")
        self.assertEqual(len(tps), 1)
        self.assertAlmostEqual(float(tps[0]["triggerPrice"]), 1.7, places=6)
        self.assertEqual([c for c in ex.calls if c[0] == "market"], [])   # no new trades

    def test_audit_adds_missing_stop(self):
        now = int(time.time() * 1000)
        ex = FakeExchange({"BLESSUSDT": trend_klines()},
                          positions={"BLESSUSDT": {"amt": 100, "entry": 1.5, "mark": 1.52,
                                                   "updateTime": now - 3 * BAR_MS}})
        run(ex)
        self.assertEqual(len(ex.algo("BLESSUSDT", "STOP_MARKET")), 1)
        self.assertEqual(len(ex.algo("BLESSUSDT", "TAKE_PROFIT_MARKET")), 1)

    def test_breakeven_moves_stop_and_keeps_take_profit(self):
        now = int(time.time() * 1000)
        ex = FakeExchange({"BLESSUSDT": trend_klines()},
                          positions={"BLESSUSDT": {"amt": 100, "entry": 1.0, "mark": 1.11,
                                                   "updateTime": now - 3 * BAR_MS}},
                          algos=[{"algoId": 1, "symbol": "BLESSUSDT", "orderType": "STOP_MARKET",
                                  "closePosition": True, "triggerPrice": "0.9"},
                                 {"algoId": 2, "symbol": "BLESSUSDT",
                                  "orderType": "TAKE_PROFIT_MARKET",
                                  "closePosition": True, "triggerPrice": "1.2"}])
        run(ex)
        stops = ex.algo("BLESSUSDT", "STOP_MARKET")
        self.assertEqual(len(stops), 1)
        self.assertAlmostEqual(float(stops[0]["triggerPrice"]), 1.001, places=6)
        self.assertEqual(ex.algo("BLESSUSDT", "TAKE_PROFIT_MARKET")[0]["triggerPrice"], "1.2")

    def test_breakeven_cancel_fails_and_stop_gone_places_new_stop(self):
        now = int(time.time() * 1000)
        ex = FakeExchange({"BLESSUSDT": trend_klines()},
                          positions={"BLESSUSDT": {"amt": 100, "entry": 1.0, "mark": 1.11,
                                                   "updateTime": now - 3 * BAR_MS}},
                          algos=[{"algoId": 1, "symbol": "BLESSUSDT", "orderType": "STOP_MARKET",
                                  "closePosition": True, "triggerPrice": "0.9"}])
        real_cancel = ex.cancel_algo

        def cancel_then_fail(algo_id):     # the stop disappears but the API reports an error
            ex.algos = [a for a in ex.algos if a["algoId"] != algo_id]
            raise at.BinanceError(400, {"code": -2011})
        ex.cancel_algo = cancel_then_fail
        run(ex)
        stops = ex.algo("BLESSUSDT", "STOP_MARKET")
        self.assertEqual(len(stops), 1)
        self.assertAlmostEqual(float(stops[0]["triggerPrice"]), 1.001, places=6)
        ex.cancel_algo = real_cancel

    def test_channel_exit_closes_and_cancels_orders(self):
        now = int(time.time() * 1000)
        ex = FakeExchange({"BLESSUSDT": trend_klines(drop_last=0.10)},
                          positions={"BLESSUSDT": {"amt": 100, "entry": 1.5, "mark": 1.5,
                                                   "updateTime": now - 5 * BAR_MS}},
                          algos=[{"algoId": 1, "symbol": "BLESSUSDT", "orderType": "STOP_MARKET",
                                  "closePosition": True, "triggerPrice": "1.4"},
                                 {"algoId": 2, "symbol": "BLESSUSDT",
                                  "orderType": "TAKE_PROFIT_MARKET",
                                  "closePosition": True, "triggerPrice": "1.7"}])
        run(ex)
        self.assertEqual(ex.pos["BLESSUSDT"]["amt"], 0)
        self.assertIn(("cancel_all_algos", "BLESSUSDT"), ex.calls)
        self.assertEqual(ex.algos, [])
        # no re-entry on the same bar
        self.assertEqual([c for c in ex.calls if c[0] == "market" and not c[4]], [])

    def test_never_more_than_five_positions(self):
        syms = ["PIEVERSEUSDT", "IRYSUSDT", "ICNTUSDT", "BTWUSDT", "PIPPINUSDT", "BLESSUSDT"]
        now = int(time.time() * 1000)
        held = {s: {"amt": 10, "entry": 1.5, "mark": 1.5, "updateTime": now - 3 * BAR_MS}
                for s in syms[:5]}
        algos = []
        for i, s in enumerate(syms[:5]):
            algos += [{"algoId": 10 + 2 * i, "symbol": s, "orderType": "STOP_MARKET",
                       "closePosition": True, "triggerPrice": "1.4"},
                      {"algoId": 11 + 2 * i, "symbol": s, "orderType": "TAKE_PROFIT_MARKET",
                       "closePosition": True, "triggerPrice": "1.7"}]
        ex = FakeExchange({s: trend_klines() for s in syms}, positions=held, algos=algos)
        run(ex, universe=syms)
        self.assertEqual([c for c in ex.calls if c[0] == "market" and not c[4]], [])

    def test_leftover_orders_on_flat_symbol_are_cancelled(self):
        ex = FakeExchange({"BLESSUSDT": trend_klines()},
                          algos=[{"algoId": 5, "symbol": "VELVETUSDT", "orderType": "STOP_MARKET",
                                  "closePosition": True, "triggerPrice": "0.1"}])
        run(ex, universe=("BLESSUSDT", "VELVETUSDT"))
        self.assertIn(("cancel_all_algos", "VELVETUSDT"), ex.calls)

    def test_pause_file_blocks_new_entries(self):
        open(at.PAUSE_PATH, "w").close()
        ex = FakeExchange({"BLESSUSDT": trend_klines()})
        run(ex)
        self.assertEqual([c for c in ex.calls if c[0] == "market"], [])

    def test_invalid_entry_detection(self):
        e = {"order": {"quantity": 10},
             "stop_loss_algo_order": {"triggerPrice": 2.6},
             "take_profit_algo_order": {"triggerPrice": -0.18}}
        self.assertFalse(at.entry_valid(e))
        e["take_profit_algo_order"]["triggerPrice"] = 0.5
        self.assertTrue(at.entry_valid(e))

    def test_foreign_position_is_not_touched(self):
        now = int(time.time() * 1000)
        ex = FakeExchange({"BLESSUSDT": trend_klines()},
                          positions={"EVAAUSDT": {"amt": 5, "entry": 2.0, "mark": 2.0,
                                                  "updateTime": now}})
        run(ex, universe=("BLESSUSDT",))
        self.assertNotIn("EVAAUSDT", [c[1] for c in ex.calls])

    def test_fmt_has_no_scientific_notation(self):
        self.assertEqual(at.fmt(0.0000123), "0.0000123")
        self.assertEqual(at.fmt(31.411), "31.411")
        self.assertEqual(at.fmt(3356), "3356")


if __name__ == "__main__":
    unittest.main()
