"""
OMNIS AI — take-profit and risk-level study for the live Donchian strategy.

Why: the user asked to turn capital over faster (take +$5-10 and move on)
and then to raise risk. This replays donchian_join (the live rules) on the
25-coin universe with fixed take-profit targets in R (R = stop distance =
3 ATR), split 60/40 in-sample / out-of-sample, then Monte-Carlo-resamples
the trade results at several risk levels.

Result (2026-11-18): only TP = 2R did not hurt in-sample (93% of no-TP per
bar held) and it helped out-of-sample (185%). Small targets (0.5-1R) cut
in-sample results 2-3x. With TP 2R, 3-month MC from $347: median $417 at
2% risk, $474 at 4% risk; chance of hitting the $220 floor 0.5% vs 12%.
Resampling treats trades as independent, so real drawdowns are larger
(open positions are correlated). Differences are within noise.

Usage:
    python -m omnis.backtest.tp_risk_study
"""
import json
import os
import random

from omnis.backtest.strategy_zoo import indicators, donchian_join_entry, donchian_exit
from omnis.strategies.donchian_live import UNIVERSE

FEE = 0.0004        # taker fee per side
SL_ATR = 3.0
MAX_BARS = 120
WARMUP = 210
IS_FRACTION = 0.6
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def run(c, d, start, end, tp_r):
    """Trades in [start, end) as (R multiple after fees, bars held)."""
    out, pos = [], None
    for i in range(start, end - 1):
        b = c[i]
        if pos:
            s, ex = pos["s"], None
            if s == 1 and b["low"] <= pos["sl"]:
                ex = pos["sl"]
            elif s == -1 and b["high"] >= pos["sl"]:
                ex = pos["sl"]
            elif tp_r and s == 1 and b["high"] >= pos["tp"]:
                ex = pos["tp"]
            elif tp_r and s == -1 and b["low"] <= pos["tp"]:
                ex = pos["tp"]
            elif donchian_exit(d, i, "LONG" if s == 1 else "SHORT") or i - pos["bar"] >= MAX_BARS:
                ex = b["close"]
            if ex is not None:
                r = (s * (ex - pos["e"]) - (pos["e"] + ex) * FEE) / pos["dist"]
                out.append((r, i - pos["bar"] + 1))
                pos = None
                continue
        if pos is None:
            side, a = donchian_join_entry(d, i), d["atr"][i]
            if not side or not a:
                continue
            e = c[i + 1]["open"]
            s = 1 if side == "LONG" else -1
            dist = SL_ATR * a
            pos = {"s": s, "e": e, "dist": dist, "bar": i + 1, "sl": e - s * dist,
                   "tp": e + s * tp_r * dist if tp_r else None}
    return out


def load():
    data = {}
    for sym in UNIVERSE:
        with open(os.path.join(DATA_DIR, f"{sym}_4h.json")) as f:
            c = json.load(f)
        data[sym] = (c, indicators(c))
    return data


def tp_table(data):
    print(f"{'TP':>8}{'seg':>5}{'trades':>8}{'win%':>7}{'avgR':>8}{'sumR':>8}{'bars':>7}{'R/100bars':>11}")
    for tp in [None, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0]:
        for seg in ("IS", "OOS"):
            tr = []
            for c, d in data.values():
                split = int(len(c) * IS_FRACTION)
                a, b = (WARMUP, split) if seg == "IS" else (split, len(c))
                tr += run(c, d, a, b, tp)
            n = len(tr)
            rs = [t[0] for t in tr]
            bars = sum(t[1] for t in tr)
            w = sum(r > 0 for r in rs) / n * 100
            print(f"{str(tp):>8}{seg:>5}{n:>8}{w:>7.1f}{sum(rs)/n:>8.3f}{sum(rs):>8.1f}"
                  f"{bars/n:>7.1f}{sum(rs)/bars*100:>11.3f}")


def monte_carlo(data, start=347.0, floor=220.0, sims=20000, seed=7):
    random.seed(seed)
    # trades per month ~ 5 slots * 180 bars / average bars held
    for tp, label, per_month in [(None, "no TP", 46), (2.0, "TP 2R", 55)]:
        rs = []
        for c, d in data.values():
            rs += [t[0] for t in run(c, d, WARMUP, len(c), tp)]
        print(f"\n== {label}: {len(rs)} trades, avgR={sum(rs)/len(rs):.3f}")
        for months in (3, 10):
            n = per_month * months
            print(f"  -- {months} months ({n} trades)")
            print(f"  {'risk':>6}{'median':>8}{'P(floor)':>10}{'P(-30%)':>9}{'P(>500)':>9}{'P(>1000)':>10}")
            for risk in (0.02, 0.03, 0.04, 0.05, 0.07):
                fin, stopped, dd30 = [], 0, 0
                for _ in range(sims):
                    eq, low, hit = start, start, False
                    for _ in range(n):
                        eq += eq * risk * random.choice(rs)
                        low = min(low, eq)
                        if eq < floor:
                            hit = True
                            break
                    stopped += hit
                    dd30 += low < start * 0.7
                    fin.append(eq)
                fin.sort()
                print(f"  {risk*100:>5.0f}%{fin[sims // 2]:>8.0f}{stopped/sims*100:>9.1f}%"
                      f"{dd30/sims*100:>8.1f}%{sum(f > 500 for f in fin)/sims*100:>8.1f}%"
                      f"{sum(f > 1000 for f in fin)/sims*100:>9.1f}%")


if __name__ == "__main__":
    d = load()
    tp_table(d)
    monte_carlo(d)
