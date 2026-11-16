"""
OMNIS AI — Strategy zoo: several classic strategy families, judged
out-of-sample on the whole universe.

Anti-self-deception rules:
- Every strategy has FIXED textbook parameters, written down before any
  test. Nothing is tuned to this data.
- Results are reported on the first 60% (in-sample) AND last 40%
  (out-of-sample) of each symbol's history. A strategy counts only if it
  is positive in BOTH halves and on most symbols.
- With 5 families tested, one can look good by luck; a real edge should
  show up broadly, not on one coin.

Usage:
    python -m omnis.backtest.strategy_zoo
"""
from omnis.backtest.backtester import fetch_history, FEE_PCT, MAX_LEVERAGE, RISK_PCT
from omnis.intelligence.technical import ema, rsi, atr, sma

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
           "AVAXUSDT", "LINKUSDT", "NEARUSDT", "RENDERUSDT", "ICPUSDT", "SUIUSDT"]
IS_FRACTION = 0.6
WARMUP = 210


def _align(s, n):
    return [None] * (n - len(s)) + list(s)


def indicators(c):
    cl = [x["close"] for x in c]
    hi = [x["high"] for x in c]
    lo = [x["low"] for x in c]
    n = len(cl)
    sma20 = _align(sma(cl, 20), n)
    std20 = [None] * n
    for i in range(19, n):
        w = cl[i - 19:i + 1]
        m = sum(w) / 20
        std20[i] = (sum((v - m) ** 2 for v in w) / 20) ** 0.5
    return {
        "c": cl, "h": hi, "l": lo,
        "ema5": _align(ema(cl, 5), n),
        "ema20": _align(ema(cl, 20), n),
        "ema50": _align(ema(cl, 50), n),
        "ema200": _align(ema(cl, 200), n),
        "rsi2": _align(rsi(cl, 2), n),
        "atr": _align(atr(hi, lo, cl, 14), n),
        "sma20": sma20, "std20": std20,
    }


# ---------------------------------------------------------------
# Strategy families. entry(ind, i) -> 'LONG'|'SHORT'|''
# exit(ind, i, side) -> True to close at bar close.
# Each also defines stop (ATR multiple) and optional take-profit.
# ---------------------------------------------------------------

def donchian_entry(d, i, n=20):
    if i < n + 1 or d["ema200"][i] is None:
        return ""
    hh = max(d["h"][i - n:i])
    ll = min(d["l"][i - n:i])
    if d["c"][i] > hh and d["c"][i] > d["ema200"][i]:
        return "LONG"
    if d["c"][i] < ll and d["c"][i] < d["ema200"][i]:
        return "SHORT"
    return ""


def donchian_exit(d, i, side, n=10):
    if side == "LONG":
        return d["c"][i] < min(d["l"][i - n:i])
    return d["c"][i] > max(d["h"][i - n:i])


def donchian_join_entry(d, i):
    """
    Join an existing trend instead of waiting for a fresh 20-bar breakout:
    be in the EMA200 direction whenever the 10-bar Donchian exit is not
    triggered. Same stop and exit as donchian_20_10. On 25 coins it gave
    +0.042R/trade over 1491 trades vs +0.033R over 717 for breakout-only
    (t = 1.28 vs 0.54) — better, but still not statistically proven.
    """
    e = d["ema200"][i]
    if e is None or i < 12:
        return ""
    if d["c"][i] < e and not donchian_exit(d, i, "SHORT"):
        return "SHORT"
    if d["c"][i] > e and not donchian_exit(d, i, "LONG"):
        return "LONG"
    return ""


def tsmom_entry(d, i, look=180):
    # time-series momentum: sign of ~30-day return, only on a flip
    if i < look + 1:
        return ""
    r_now = d["c"][i] / d["c"][i - look] - 1
    r_prev = d["c"][i - 1] / d["c"][i - 1 - look] - 1
    if r_now > 0 >= r_prev:
        return "LONG"
    if r_now < 0 <= r_prev:
        return "SHORT"
    return ""


def tsmom_exit(d, i, side, look=180):
    r = d["c"][i] / d["c"][i - look] - 1
    return (side == "LONG" and r < 0) or (side == "SHORT" and r > 0)


def rsi2_entry(d, i):
    # Connors RSI(2) pullback in the direction of the EMA200 trend
    r, e = d["rsi2"][i], d["ema200"][i]
    if r is None or e is None:
        return ""
    if d["c"][i] > e and r < 10:
        return "LONG"
    if d["c"][i] < e and r > 90:
        return "SHORT"
    return ""


def rsi2_exit(d, i, side):
    e5 = d["ema5"][i]
    return (side == "LONG" and d["c"][i] > e5) or (side == "SHORT" and d["c"][i] < e5)


def ema_cross_entry(d, i):
    a, b, pa, pb = d["ema20"][i], d["ema50"][i], d["ema20"][i - 1], d["ema50"][i - 1]
    if None in (a, b, pa, pb):
        return ""
    if pa <= pb and a > b:
        return "LONG"
    if pa >= pb and a < b:
        return "SHORT"
    return ""


def ema_cross_exit(d, i, side):
    a, b = d["ema20"][i], d["ema50"][i]
    return (side == "LONG" and a < b) or (side == "SHORT" and a > b)


def bb_revert_entry(d, i):
    # Bollinger mean reversion against 2-sigma extremes, no trend filter
    m, s = d["sma20"][i], d["std20"][i]
    if m is None or s is None:
        return ""
    if d["c"][i] < m - 2 * s:
        return "LONG"
    if d["c"][i] > m + 2 * s:
        return "SHORT"
    return ""


def bb_revert_exit(d, i, side):
    m = d["sma20"][i]
    return (side == "LONG" and d["c"][i] >= m) or (side == "SHORT" and d["c"][i] <= m)


STRATEGIES = {
    #  name           entry             exit            SL ATR  max bars
    "donchian_20_10": (donchian_entry, donchian_exit, 3.0, 120),
    "donchian_join":  (donchian_join_entry, donchian_exit, 3.0, 120),
    "tsmom_30d":      (tsmom_entry, tsmom_exit, 4.0, 360),
    "ema_20_50":      (ema_cross_entry, ema_cross_exit, 3.0, 180),
    "rsi2_pullback":  (rsi2_entry, rsi2_exit, 3.0, 20),
    "bb_revert":      (bb_revert_entry, bb_revert_exit, 3.0, 30),
}


def simulate(c, d, start, end, entry_fn, exit_fn, sl_atr, max_bars):
    eq = eq0 = 1000.0
    trades, pos = [], None
    for i in range(start, end - 1):
        bar = c[i]
        if pos:
            ex = None
            if pos["side"] == "LONG" and bar["low"] <= pos["sl"]:
                ex = pos["sl"]
            elif pos["side"] == "SHORT" and bar["high"] >= pos["sl"]:
                ex = pos["sl"]
            elif exit_fn(d, i, pos["side"]) or i - pos["bar"] >= max_bars:
                ex = bar["close"]
            if ex is not None:
                s = 1 if pos["side"] == "LONG" else -1
                pnl = s * (ex - pos["entry"]) * pos["qty"] \
                    - (pos["entry"] + ex) * pos["qty"] * FEE_PCT / 100
                eq += pnl
                trades.append(pnl)
                pos = None
                continue
        if pos is None:
            side = entry_fn(d, i)
            a = d["atr"][i]
            if not side or not a:
                continue
            entry = c[i + 1]["open"]
            qty = min((eq * RISK_PCT / 100) / (sl_atr * a), eq * MAX_LEVERAGE / entry)
            s = 1 if side == "LONG" else -1
            pos = {"side": side, "entry": entry, "qty": qty, "bar": i + 1,
                   "sl": entry - s * sl_atr * a}
    return (eq - eq0) / eq0 * 100, len(trades)


def main():
    data = {}
    for s in SYMBOLS:
        c = fetch_history(s)
        data[s] = (c, indicators(c))

    print(f"{len(SYMBOLS)} symbols, 4h, risk {RISK_PCT}%/trade, fee {FEE_PCT}%/side\n")
    print(f"{'STRATEGY':<16}{'IS avg%':>9}{'OOS avg%':>10}{'OOS +sym':>10}{'OOS trades':>12}")
    summary = {}
    for name, (en, ex, sl, mb) in STRATEGIES.items():
        is_r, oos_r, oos_n, per = [], [], 0, {}
        for s, (c, d) in data.items():
            split = int(len(c) * IS_FRACTION)
            r1, _ = simulate(c, d, WARMUP, split, en, ex, sl, mb)
            r2, n2 = simulate(c, d, split, len(c), en, ex, sl, mb)
            is_r.append(r1)
            oos_r.append(r2)
            oos_n += n2
            per[s] = r2
        pos_syms = sum(r > 0 for r in oos_r)
        summary[name] = per
        print(f"{name:<16}{sum(is_r)/len(is_r):>+9.2f}{sum(oos_r)/len(oos_r):>+10.2f}"
              f"{pos_syms:>7}/{len(oos_r):<2}{oos_n:>12}")

    print("\nOOS return % per symbol:")
    print(f"{'':<11}" + "".join(f"{n[:9]:>10}" for n in STRATEGIES))
    for s in SYMBOLS:
        print(f"{s:<11}" + "".join(f"{summary[n][s]:>+10.1f}" for n in STRATEGIES))


if __name__ == "__main__":
    main()
