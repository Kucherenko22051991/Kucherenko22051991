"""
OMNIS AI — Strategy lab: parameter search with out-of-sample validation.

Data is split chronologically: the first IS_FRACTION of bars is used to
choose SL/TP, the remaining bars are an untouched out-of-sample test.
Only the out-of-sample number says anything about the future.

Usage:
    python -m omnis.backtest.strategy_lab
"""
from omnis.backtest.backtester import fetch_history, FEE_PCT, MAX_LEVERAGE, RISK_PCT
from omnis.strategies import trend

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "ICPUSDT", "RENDERUSDT", "XRPUSDT"]
IS_FRACTION = 0.6
WARMUP = 210
MAX_HOLD_BARS = 30


def simulate(candles, ind, start, end, sl_atr, tp_atr, equity=1000.0):
    """Simulate trades for bars [start, end). Returns stats dict."""
    eq0, peak, max_dd = equity, equity, 0.0
    trades, pos = [], None
    for i in range(start, end - 1):
        bar = candles[i]
        if pos:
            ex = None
            if pos["side"] == "LONG":
                if bar["low"] <= pos["sl"]:
                    ex = pos["sl"]
                elif bar["high"] >= pos["tp"]:
                    ex = pos["tp"]
            else:
                if bar["high"] >= pos["sl"]:
                    ex = pos["sl"]
                elif bar["low"] <= pos["tp"]:
                    ex = pos["tp"]
            if ex is None and i - pos["bar"] >= MAX_HOLD_BARS:
                ex = bar["close"]
            if ex is not None:
                s = 1 if pos["side"] == "LONG" else -1
                pnl = s * (ex - pos["entry"]) * pos["qty"] \
                    - (pos["entry"] + ex) * pos["qty"] * FEE_PCT / 100
                equity += pnl
                trades.append((pos["side"], pnl))
                pos = None
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak * 100)
        if pos is None:
            side = trend.signal_at(ind, i)
            if not side:
                continue
            a = ind["atr"][i]
            entry = candles[i + 1]["open"]
            qty = min((equity * RISK_PCT / 100) / (sl_atr * a), equity * MAX_LEVERAGE / entry)
            s = 1 if side == "LONG" else -1
            pos = {"side": side, "entry": entry, "qty": qty, "bar": i + 1,
                   "sl": entry - s * sl_atr * a, "tp": entry + s * tp_atr * a}
    wins = [p for _, p in trades if p > 0]
    gl = -sum(p for _, p in trades if p <= 0)
    return {
        "trades": len(trades),
        "shorts": sum(1 for sd, _ in trades if sd == "SHORT"),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        "ret": round((equity - eq0) / eq0 * 100, 2),
        "dd": round(max_dd, 2),
        "pf": round(sum(wins) / gl, 2) if gl else None,
    }


def portfolio(data, sl, tp, segment):
    rows = {}
    for sym, (candles, ind) in data.items():
        split = int(len(candles) * IS_FRACTION)
        a, b = (WARMUP, split) if segment == "IS" else (split, len(candles))
        rows[sym] = simulate(candles, ind, a, b, sl, tp)
    avg = sum(r["ret"] for r in rows.values()) / len(rows)
    return avg, rows


def main():
    data = {}
    for s in SYMBOLS:
        c = fetch_history(s)
        data[s] = (c, trend.precompute(c))

    grid = [(sl, tp) for sl in (1.5, 2.0, 2.5, 3.0) for tp in (2.0, 3.0, 4.0, 5.0, 6.0)]
    print("In-sample grid (avg return % across 6 symbols):")
    scored = []
    for sl, tp in grid:
        avg, _ = portfolio(data, sl, tp, "IS")
        scored.append((avg, sl, tp))
        print(f"  SL {sl} ATR / TP {tp} ATR -> {avg:+.2f}%")
    best_avg, sl, tp = max(scored)
    print(f"\nBest in-sample: SL {sl} / TP {tp} ({best_avg:+.2f}%)")

    avg, rows = portfolio(data, sl, tp, "OOS")
    print(f"\nOUT-OF-SAMPLE (last {int((1 - IS_FRACTION) * 100)}% of bars, untouched):")
    print(f"{'SYMBOL':<11}{'TRADES':>7}{'SHORTS':>7}{'WIN%':>7}{'RET%':>8}{'DD%':>7}{'PF':>6}")
    for sym, r in rows.items():
        print(f"{sym:<11}{r['trades']:>7}{r['shorts']:>7}{r['win_rate']:>7}"
              f"{r['ret']:>8}{r['dd']:>7}{str(r['pf']):>6}")
    print(f"OOS average return: {avg:+.2f}%")


if __name__ == "__main__":
    main()
