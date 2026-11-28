"""
OMNIS AI — Backtesting Engine (Phase 7)

Walk-forward backtest of the signal scorer on historical Binance candles.

Limits (honest by design):
- Only the TECHNICAL component can be replayed historically. LunarCrush,
  derivatives and momentum snapshots are not available for past bars, so
  those components contribute 0 (neutral) here.
- No look-ahead: the signal on bar i uses candles[..i] only; entry is at
  the OPEN of bar i+1.
- If SL and TP are both touched inside one bar, SL is assumed (conservative).

Risk rules mirror live trading:
- risk per trade = RISK_PCT of current equity (default 1%)
- SL = 2 x ATR, TP = 3 x ATR (R:R 1.5)
- notional capped at MAX_LEVERAGE x equity (5x)
- taker fee charged on entry and exit

Usage:
    python -m omnis.backtest.backtester                 # whole universe
    python -m omnis.backtest.backtester BTCUSDT SOLUSDT
"""
import json
import os
import sys
from typing import Optional

from omnis.config.settings import CONFIG
from omnis.data.binance_client import binance
from omnis.intelligence.signal_scorer import compute_signal

WINDOW = 200            # candles fed to the scorer per bar
RISK_PCT = 1.0          # % of equity risked per trade
SL_ATR = 2.0
TP_ATR = 3.0
MAX_LEVERAGE = 5
FEE_PCT = 0.04          # Binance USD-M taker fee, per side
MAX_HOLD_BARS = 30      # time stop (30 x 4h = 5 days)


DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def fetch_history(symbol: str, interval: str = "4h", limit: int = 1500) -> list:
    """
    Load candles from the local cache (omnis/backtest/data/<SYMBOL>_<tf>.json)
    if present, otherwise from the Binance API (up to 1500 klines/request).
    """
    path = os.path.join(DATA_DIR, f"{symbol}_{interval}.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)[-limit:]
    return binance.get_klines(symbol, interval=interval, limit=limit)


def backtest_symbol(
    symbol: str,
    candles: list,
    long_threshold: float = 70.0,
    short_threshold: float = 35.0,
    start_equity: float = 1000.0,
) -> dict:
    equity = start_equity
    peak = equity
    max_dd = 0.0
    trades = []
    pos: Optional[dict] = None

    for i in range(WINDOW, len(candles) - 1):
        bar = candles[i]

        # ---- manage open position on this bar ----
        if pos:
            exit_price, reason = None, None
            if pos["side"] == "LONG":
                if bar["low"] <= pos["sl"]:
                    exit_price, reason = pos["sl"], "SL"
                elif bar["high"] >= pos["tp"]:
                    exit_price, reason = pos["tp"], "TP"
            else:
                if bar["high"] >= pos["sl"]:
                    exit_price, reason = pos["sl"], "SL"
                elif bar["low"] <= pos["tp"]:
                    exit_price, reason = pos["tp"], "TP"
            if exit_price is None and i - pos["bar"] >= MAX_HOLD_BARS:
                exit_price, reason = bar["close"], "TIME"

            if exit_price is not None:
                sign = 1 if pos["side"] == "LONG" else -1
                gross = sign * (exit_price - pos["entry"]) * pos["qty"]
                fees = (pos["entry"] + exit_price) * pos["qty"] * FEE_PCT / 100
                pnl = gross - fees
                equity += pnl
                trades.append({
                    "side": pos["side"], "entry": pos["entry"], "exit": exit_price,
                    "pnl": pnl, "reason": reason, "bars": i - pos["bar"],
                })
                pos = None
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak * 100)
                if equity <= 0:
                    break

        # ---- look for a new entry (signal on close of bar i) ----
        if pos is None:
            sig = compute_signal(symbol, candles[i - WINDOW + 1:i + 1])
            atr = sig["ta"].get("atr") if "error" not in sig["ta"] else None
            if not atr:
                continue
            if sig["score"] >= long_threshold:
                side = "LONG"
            elif sig["score"] <= short_threshold:
                side = "SHORT"
            else:
                continue

            entry = candles[i + 1]["open"]  # next bar open — no look-ahead
            stop_dist = SL_ATR * atr
            qty = (equity * RISK_PCT / 100) / stop_dist
            qty = min(qty, equity * MAX_LEVERAGE / entry)  # leverage cap
            sign = 1 if side == "LONG" else -1
            pos = {
                "side": side, "entry": entry, "qty": qty, "bar": i + 1,
                "sl": entry - sign * stop_dist,
                "tp": entry + sign * TP_ATR * atr,
            }

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = -sum(t["pnl"] for t in losses)
    return {
        "symbol": symbol,
        "bars": len(candles),
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        "return_pct": round((equity - start_equity) / start_equity * 100, 2),
        "max_dd_pct": round(max_dd, 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "longs": sum(1 for t in trades if t["side"] == "LONG"),
        "shorts": sum(1 for t in trades if t["side"] == "SHORT"),
        "trade_log": trades,
    }


def score_distribution(symbol: str, candles: list) -> dict:
    """How often each score band occurs — tells us if thresholds are reachable."""
    scores = [
        compute_signal(symbol, candles[i - WINDOW + 1:i + 1])["score"]
        for i in range(WINDOW, len(candles), 5)
    ]
    if not scores:
        return {}
    return {
        "min": min(scores), "max": max(scores),
        ">=70": sum(s >= 70 for s in scores),
        ">=60": sum(s >= 60 for s in scores),
        "<=35": sum(s <= 35 for s in scores),
        "<=40": sum(s <= 40 for s in scores),
        "samples": len(scores),
    }


def run(symbols: list, long_t: float = 70.0, short_t: float = 35.0) -> list:
    results = []
    print(f"Backtest 4h | long>={long_t} short<={short_t} | risk {RISK_PCT}% | "
          f"SL {SL_ATR}ATR TP {TP_ATR}ATR | fee {FEE_PCT}%/side")
    print(f"{'SYMBOL':<11}{'TRADES':>7}{'WIN%':>7}{'RET%':>9}{'MAXDD%':>8}{'PF':>7}")
    for sym in symbols:
        candles = fetch_history(sym)
        if len(candles) <= WINDOW + 1:
            print(f"{sym:<11} no data")
            continue
        r = backtest_symbol(sym, candles, long_t, short_t)
        results.append(r)
        pf = r["profit_factor"] if r["profit_factor"] is not None else "-"
        print(f"{sym:<11}{r['trades']:>7}{r['win_rate']:>7}{r['return_pct']:>9}"
              f"{r['max_dd_pct']:>8}{pf:>7}")
    if results:
        n = sum(r["trades"] for r in results)
        avg = sum(r["return_pct"] for r in results) / len(results)
        print(f"TOTAL trades={n} avg_return={avg:.2f}%")
    return results


if __name__ == "__main__":
    syms = sys.argv[1:] or CONFIG.trading.universe
    run(syms)
