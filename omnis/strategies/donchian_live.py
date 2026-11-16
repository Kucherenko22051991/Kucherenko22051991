"""
OMNIS AI — Donchian 20/10 live decision engine.

Chosen because it was the most profitable of the five tested families over
the full history (12 symbols, 4h). Rules are IDENTICAL to the backtest in
omnis/backtest/strategy_zoo.py:

  ENTRY (on a closed 4h bar, flat only)
    SHORT: close < lowest low of previous 20 bars  AND close < EMA200
    LONG : close > highest high of previous 20 bars AND close > EMA200
  EXIT (on a closed 4h bar)
    SHORT: close > highest high of previous 10 bars
    LONG : close < lowest low of previous 10 bars
    or position held >= 120 bars (20 days)
  STOP LOSS: 3 x ATR(14) from entry, placed on the exchange immediately.

Risk (user's hard rules):
  - RISK_PCT of equity per trade (0.75%, inside the required 0.5-1%)
  - leverage set to 5x, never more
  - at most MAX_POSITIONS open (crypto is highly correlated)
  - a trade is skipped if the exchange minimum size would push risk > 1%
  - no new entries if equity < EQUITY_FLOOR (circuit breaker)

This script never talks to the exchange. It reads data and prints a JSON
plan; the operator (scheduled agent) executes it with the Binance tools.

Usage:
  python -m omnis.strategies.donchian_live \
      --equity 348.68 --available 348.68 \
      --positions-file <positionInformationV2 dump> \
      --klines BTCUSDT=<klines dump> --klines ETHUSDT=<...> ...
"""
import argparse
import json
import math
import os
import time

from omnis.backtest.strategy_zoo import indicators, donchian_entry, donchian_exit

UNIVERSE = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
            "AVAXUSDT", "LINKUSDT", "NEARUSDT", "RENDERUSDT", "ICPUSDT", "SUIUSDT"]
RISK_PCT = 0.75
MAX_RISK_PCT = 1.0
SL_ATR = 3.0
MAX_HOLD_BARS = 120
MAX_POSITIONS = 5
LEVERAGE = 5
EQUITY_FLOOR = 300.0       # ~ -14% from 348.68 start: stop opening new trades
BAR_MS = 4 * 3600 * 1000

FILTERS_PATH = os.path.join(os.path.dirname(__file__), "..", "backtest", "data",
                            "symbol_filters.json")


def _dec(step: str) -> int:
    return max(0, -int(math.floor(math.log10(float(step)))))


def floor_step(x: float, step: str) -> float:
    s = float(step)
    return round(math.floor(x / s + 1e-9) * s, _dec(step))


def round_tick(x: float, tick: str, up: bool) -> float:
    t = float(tick)
    f = math.ceil if up else math.floor
    return round(f(x / t - 1e-9 if up else x / t + 1e-9) * t, _dec(tick))


def load_klines(path: str) -> list:
    with open(path) as f:
        raw = json.load(f)
    if raw and isinstance(raw[0], dict):
        return raw
    return [{"time": r[0], "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
            for r in raw]


def load_positions(path: str) -> dict:
    """Non-zero positions from a positionInformationV2 dump."""
    if not path:
        return {}
    with open(path) as f:
        raw = json.load(f)
    out = {}
    for p in raw:
        amt = float(p.get("positionAmt", 0))
        if amt != 0:
            out[p["symbol"]] = {
                "side": "LONG" if amt > 0 else "SHORT",
                "qty": abs(amt),
                "entry": float(p["entryPrice"]),
                "update_time": int(p.get("updateTime", 0)),
            }
    return out


def plan(klines: dict, positions: dict, equity: float, available: float,
         now_ms: int) -> dict:
    filters = json.load(open(FILTERS_PATH))
    exits, entries, notes, held_positions = [], [], [], []
    open_count = len([s for s in positions if s in UNIVERSE])
    margin_left = available

    for sym in UNIVERSE:
        if sym not in klines:
            notes.append(f"{sym}: no klines supplied, skipped")
            continue
        c = klines[sym]
        if c and c[-1]["time"] + BAR_MS > now_ms:
            c = c[:-1]                      # drop the still-forming bar
        if len(c) < 230:
            notes.append(f"{sym}: only {len(c)} closed bars, need 230")
            continue
        d = indicators(c)
        i = len(c) - 1
        close, atr = c[i]["close"], d["atr"][i]
        pos = positions.get(sym)

        if pos:
            held = (c[i]["time"] - pos["update_time"]) // BAR_MS if pos["update_time"] else 0
            if donchian_exit(d, i, pos["side"]):
                reason = "10-bar channel exit"
            elif held >= MAX_HOLD_BARS:
                reason = f"time stop ({held} bars)"
            else:
                f = filters[sym]
                is_short = pos["side"] == "SHORT"
                sl = pos["entry"] + SL_ATR * atr if is_short else pos["entry"] - SL_ATR * atr
                held_positions.append({
                    "symbol": sym, "side": pos["side"], "close": close,
                    "bars_held": held,
                    # use ONLY if the position has no stop on the exchange
                    "fallback_stop_loss_algo_order": {
                        "algoType": "CONDITIONAL", "symbol": sym,
                        "side": "BUY" if is_short else "SELL",
                        "type": "STOP_MARKET", "closePosition": "true",
                        "workingType": "MARK_PRICE", "priceProtect": "true",
                        "triggerPrice": round_tick(sl, f["tick"], up=is_short),
                    },
                })
                continue
            exits.append({
                "symbol": sym,
                "order": {"side": "BUY" if pos["side"] == "SHORT" else "SELL",
                          "type": "MARKET", "quantity": pos["qty"], "reduceOnly": "true"},
                "reason": reason,
                "then": "cancel all algo orders on symbol",
            })
            open_count -= 1
            continue

        side = donchian_entry(d, i)
        if not side:
            continue
        if equity < EQUITY_FLOOR:
            notes.append(f"{sym}: {side} signal ignored, equity {equity} < floor {EQUITY_FLOOR}")
            continue
        if open_count >= MAX_POSITIONS:
            notes.append(f"{sym}: {side} signal ignored, already {open_count} positions")
            continue

        f = filters[sym]
        stop_dist = SL_ATR * atr
        qty = floor_step(equity * RISK_PCT / 100 / stop_dist, f["step"])
        min_qty = max(float(f["min_qty"]),
                      floor_step(float(f["min_notional"]) / close, f["step"]) + float(f["step"]))
        if qty < min_qty:
            qty = min_qty
        risk = qty * stop_dist
        if risk > equity * MAX_RISK_PCT / 100:
            notes.append(f"{sym}: {side} skipped, min size risks ${risk:.2f} "
                         f"> {MAX_RISK_PCT}% of equity")
            continue
        margin = qty * close / LEVERAGE
        if margin > margin_left * 0.9:
            notes.append(f"{sym}: {side} skipped, needs ${margin:.2f} margin")
            continue
        margin_left -= margin
        open_count += 1
        sl = close + stop_dist if side == "SHORT" else close - stop_dist
        entries.append({
            "symbol": sym,
            "direction": side,
            "set_leverage": LEVERAGE,
            "order": {"side": "SELL" if side == "SHORT" else "BUY",
                      "type": "MARKET", "quantity": qty},
            "stop_loss_algo_order": {
                "algoType": "CONDITIONAL", "symbol": sym,
                "side": "BUY" if side == "SHORT" else "SELL",
                "type": "STOP_MARKET", "closePosition": "true",
                "workingType": "MARK_PRICE", "priceProtect": "true",
                "triggerPrice": round_tick(sl, f["tick"], up=(side == "SHORT")),
            },
            "sl_distance": stop_dist,
            "risk_usd": round(risk, 2),
            "signal_close": close,
        })

    return {
        "generated_at_ms": now_ms,
        "equity": equity,
        "open_positions": positions,
        "held": held_positions,
        "exits": exits,
        "entries": entries,
        "notes": notes,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--equity", type=float, required=True)
    ap.add_argument("--available", type=float, required=True)
    ap.add_argument("--positions-file", default="")
    ap.add_argument("--klines", action="append", default=[],
                    help="SYMBOL=path to klines dump (raw Binance arrays)")
    ap.add_argument("--now-ms", type=int, default=int(time.time() * 1000))
    a = ap.parse_args()
    kl = {}
    for item in a.klines:
        sym, path = item.split("=", 1)
        kl[sym] = load_klines(path)
    print(json.dumps(plan(kl, load_positions(a.positions_file), a.equity,
                          a.available, a.now_ms), indent=1))


if __name__ == "__main__":
    main()
