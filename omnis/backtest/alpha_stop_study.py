"""
OMNIS AI - stop width x entry type study on Binance Alpha coins.

Why: the user asked for shorter stops and entries "without a move against
us". Replays donchian_join on 11 liquid Alpha perps (999 x 4h bars, ~5.5
months) with stops of 1/1.5/2/3 ATR, TP 2R or none, and market entry vs a
limit entry 0.5/1 ATR back toward the trend (valid 3 bars). Split 60/40
in-sample / out-of-sample. Costs: market 0.10%/side (fee + slippage),
limit 0.02%.

Result (2026-11-19), avg R per trade IS / OOS:
  SL 1.0 ATR TP 2R: -0.014 / +0.017, stopped out 57% of trades
  SL 1.5 ATR TP 2R: -0.035 / +0.012, stopped out 46%
  SL 2.0 ATR TP 2R: +0.025 / +0.104, stopped out 30%  <- chosen
  SL 3.0 ATR TP 2R: +0.029 / +0.065, stopped out 16%
Pullback limits barely changed results (2 ATR, 0.5 ATR pullback:
+0.028 / +0.106). Even the best variant saw 57-63% of trades move >=0.5R
against before exiting: no entry rule avoids adverse moves. The edge is
small and within noise on this sample.

Usage:
    python -m omnis.backtest.alpha_stop_study --klines SYM=path [...]
"""
import argparse
import statistics
from omnis.strategies.donchian_live import load_klines
from omnis.backtest.strategy_zoo import indicators, donchian_join_entry, donchian_exit

MKT_COST = 0.0005 + 0.0005   # taker fee + slippage per side
LMT_COST = 0.0002            # maker fee
WARMUP, MAX_BARS, LIMIT_BARS = 210, 120, 3

def run(c, d, start, end, sl_atr, tp_r, k):
    """k = pullback in ATR for a limit entry (0 = market at next open)."""
    out, pos, pend = [], None, None
    for i in range(start, end - 1):
        b = c[i]
        if pend and pos is None:                       # try to fill pending limit on this bar
            s = pend["s"]
            if (s == 1 and b["low"] <= pend["px"]) or (s == -1 and b["high"] >= pend["px"]):
                e = min(pend["px"], b["open"]) if s == 1 else max(pend["px"], b["open"])
                dist = sl_atr * pend["atr"]
                pos = {"s": s, "e": e, "dist": dist, "sl": e - s * dist, "tp": e + s * tp_r * dist if tp_r else None,
                       "bar": i, "cost": LMT_COST, "mae": 0.0}
                pend = None
            elif i - pend["bar"] >= LIMIT_BARS:
                pend = None
        if pos:
            s, ex = pos["s"], None
            adverse = (pos["e"] - b["low"]) if s == 1 else (b["high"] - pos["e"])
            pos["mae"] = max(pos["mae"], adverse / pos["dist"])
            if s == 1 and b["low"] <= pos["sl"]: ex = pos["sl"]
            elif s == -1 and b["high"] >= pos["sl"]: ex = pos["sl"]
            elif pos["tp"] and s == 1 and b["high"] >= pos["tp"]: ex = pos["tp"]
            elif pos["tp"] and s == -1 and b["low"] <= pos["tp"]: ex = pos["tp"]
            elif donchian_exit(d, i, "LONG" if s == 1 else "SHORT") or i - pos["bar"] >= MAX_BARS: ex = b["close"]
            if ex is not None:
                r = (s * (ex - pos["e"]) - pos["e"] * pos["cost"] - ex * MKT_COST) / pos["dist"]
                out.append((r, min(pos["mae"], 1.0)))
                pos = None
            continue
        if pend is None:
            side, a = donchian_join_entry(d, i), d["atr"][i]
            if not side or not a: continue
            s = 1 if side == "LONG" else -1
            if k == 0:
                e = c[i + 1]["open"]; dist = sl_atr * a
                pos = {"s": s, "e": e, "dist": dist, "sl": e - s * dist, "tp": e + s * tp_r * dist if tp_r else None,
                       "bar": i + 1, "cost": MKT_COST, "mae": 0.0}
            else:
                pend = {"s": s, "px": c[i]["close"] - s * k * a, "atr": a, "bar": i + 1}
    return out

ap = argparse.ArgumentParser()
ap.add_argument("--klines", action="append", default=[], help="SYMBOL=path (raw Binance 4h arrays)")
data = {}
for item in ap.parse_args().klines:
    s, path = item.split("=", 1)
    c = load_klines(path)[:-1]
    data[s] = (c, indicators(c))

print(f"{len(data)} Alpha coins, {len(next(iter(data.values()))[0])} x 4h bars; costs: market {MKT_COST*100:.2f}%/side, limit {LMT_COST*100:.2f}%")
print(f"{'SL':>4} {'TP':>3} {'pull':>5} | {'IS n':>5} {'IS avgR':>8} {'IS win':>7} | {'OOS n':>5} {'OOS avgR':>8} {'OOS win':>7} {'OOS sumR':>8} | {'stopped':>7} {'MAE>0.5R':>8}")
for sl in (1.0, 1.5, 2.0, 3.0):
    for tp in (2, 0):
        for k in (0, 0.5, 1.0):
            IS, OOS = [], []
            for s, (c, d) in data.items():
                split = int(len(c) * 0.6)
                IS += run(c, d, WARMUP, split, sl, tp, k)
                OOS += run(c, d, split, len(c), sl, tp, k)
            def st(t):
                if not t: return 0, 0, 0, 0
                rs = [r for r, _ in t]
                return len(rs), statistics.mean(rs), sum(r > 0 for r in rs) / len(rs) * 100, sum(rs)
            n1, a1, w1, _ = st(IS); n2, a2, w2, s2 = st(OOS)
            allt = IS + OOS
            stopped = sum(r <= -0.95 for r, _ in allt) / max(1, len(allt)) * 100
            mae = sum(m >= 0.5 for _, m in allt) / max(1, len(allt)) * 100
            print(f"{sl:>4} {tp or '-':>3} {k:>5} | {n1:>5} {a1:>+8.3f} {w1:>6.0f}% | {n2:>5} {a2:>+8.3f} {w2:>6.0f}% {s2:>+8.1f} | {stopped:>6.0f}% {mae:>7.0f}%")
