"""
OMNIS AI — Donchian 20/10 live decision engine.

Chosen because it was the most profitable of the five tested families over
the full history (12 symbols, 4h). Rules are IDENTICAL to the backtest in
omnis/backtest/strategy_zoo.py:

  ENTRY (on a closed 4h bar, flat only) — "join the trend" variant
    SHORT: close < EMA200 AND close <= highest high of previous 10 bars
    LONG : close > EMA200 AND close >= lowest low of previous 10 bars
    (i.e. be in the trend whenever the exit is not triggered; switched from
    breakout-only entries on 2026-11-16 because on 25 coins it backtested
    better: +0.042R vs +0.033R per trade, t 1.28 vs 0.54 — still unproven)
    When more symbols qualify than free slots, UNIVERSE order (roughly
    market cap, most liquid first) decides.
  EXIT (on a closed 4h bar)
    SHORT: close > highest high of previous 10 bars
    LONG : close < lowest low of previous 10 bars
    or position held >= 120 bars (20 days)
  STOP LOSS: 3 x ATR(14) from entry, placed on the exchange immediately.
  TAKE PROFIT: TP_R x the stop distance (2R), placed on the exchange right
    after the stop (added 2026-11-18). Backtest on 25 coins, 4h: 2R was the
    only target that did not hurt in-sample (93% of no-TP) and it helped
    out-of-sample (185%); trades close ~20% sooner, freeing slots. Smaller
    targets (0.5-1R) cut in-sample results 2-3x. Not statistically proven.

Risk (user's hard rules):
  - RISK_PCT of equity per trade: 4% (user raised 0.75% -> 2% on 2026-11-16,
    then 2% -> 4% on 2026-11-18 after seeing the Monte Carlo: with the 2R
    target, 3-month median $474 vs $417 at 2%, but ~12% chance of hitting
    the equity floor vs 0.5%)
  - leverage 5x by default; --leverage overrides it (user chose 10x for
    Binance Alpha coins on 2026-11-19)
  - at most MAX_POSITIONS open: 10 (user raised 5 -> 10 on 2026-11-19 to put
    idle margin to work; crypto is highly correlated, so several stops can
    hit together)
  - a trade is skipped if the exchange minimum size would push risk > 5%
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

from omnis.backtest.strategy_zoo import indicators, donchian_join_entry, donchian_exit

UNIVERSE = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
            "AVAXUSDT", "LINKUSDT", "NEARUSDT", "RENDERUSDT", "ICPUSDT", "SUIUSDT",
            "ADAUSDT", "DOTUSDT", "LTCUSDT", "BCHUSDT", "TRXUSDT", "UNIUSDT",
            "AAVEUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "APTUSDT", "FILUSDT", "ATOMUSDT"]
RISK_PCT = 4.0             # user raised risk to 4% per trade (2026-11-18)
MAX_RISK_PCT = 5.0
SL_ATR = 3.0
TP_R = 2.0                 # take profit at 2 x the stop distance
MAX_HOLD_BARS = 120
MAX_POSITIONS = 10         # user raised 5 -> 10 (2026-11-19)
LEVERAGE = 5
EQUITY_FLOOR = 220.0       # ~ -37% from 348.68 start: stop opening new trades
BAR_MS = 4 * 3600 * 1000
EXIT_GRACE_MS = 20 * 60 * 1000   # engine exits happen within ~20 min of a 4h close

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


def load_recent_exits(path: str) -> dict:
    """Flat symbols with their last position update time (ms) — i.e. when
    the position was last closed, by the engine, a take profit or a stop."""
    if not path:
        return {}
    with open(path) as f:
        raw = json.load(f)
    return {p["symbol"]: int(p.get("updateTime", 0)) for p in raw
            if float(p.get("positionAmt", 0)) == 0 and int(p.get("updateTime", 0)) > 0}


def plan(klines: dict, positions: dict, equity: float, available: float,
         now_ms: int, recent_exits: dict = None, universe: list = None,
         max_positions: int = None, leverage: int = None,
         sl_atr: float = None, risk_pct: float = None,
         tp_r: float = None) -> dict:
    recent_exits = recent_exits or {}
    universe = universe or UNIVERSE
    max_positions = max_positions or MAX_POSITIONS
    leverage = leverage or LEVERAGE
    sl_atr = sl_atr or SL_ATR
    risk_pct = risk_pct or RISK_PCT
    tp_r = tp_r or TP_R
    filters = json.load(open(FILTERS_PATH))
    exits, entries, notes, held_positions = [], [], [], []
    open_count = len([s for s in positions if s in universe])
    margin_left = available

    for sym in universe:
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
                sl = pos["entry"] + sl_atr * atr if is_short else pos["entry"] - sl_atr * atr
                tp = (pos["entry"] - tp_r * sl_atr * atr if is_short
                      else pos["entry"] + tp_r * sl_atr * atr)
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
                    # use ONLY if the position has no take profit on the
                    # exchange AND no stop either; if a stop exists, put the
                    # TP at entry -/+ TP_R x |entry - stop trigger| instead
                    "fallback_take_profit_algo_order": {
                        "algoType": "CONDITIONAL", "symbol": sym,
                        "side": "BUY" if is_short else "SELL",
                        "type": "TAKE_PROFIT_MARKET", "closePosition": "true",
                        "workingType": "MARK_PRICE", "priceProtect": "true",
                        "triggerPrice": round_tick(tp, f["tick"], up=not is_short),
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

        side = donchian_join_entry(d, i)
        if not side:
            continue
        # Same timing as the backtest: after a position is closed during a 4h
        # bar (take profit / stop), the next entry check is the close of the
        # FOLLOWING bar. Matters because the operator may run every 30 min.
        if recent_exits.get(sym, 0) >= c[i]["time"] + EXIT_GRACE_MS:
            notes.append(f"{sym}: {side} signal ignored, position closed during or "
                         f"after the last closed 4h bar; waiting for the next close")
            continue
        if equity < EQUITY_FLOOR:
            notes.append(f"{sym}: {side} signal ignored, equity {equity} < floor {EQUITY_FLOOR}")
            continue
        if open_count >= max_positions:
            notes.append(f"{sym}: {side} signal ignored, already {open_count} positions")
            continue

        f = filters[sym]
        stop_dist = sl_atr * atr
        qty = floor_step(equity * risk_pct / 100 / stop_dist, f["step"])
        min_qty = max(float(f["min_qty"]),
                      floor_step(float(f["min_notional"]) / close, f["step"]) + float(f["step"]))
        if qty < min_qty:
            qty = min_qty
        risk = qty * stop_dist
        if risk > equity * MAX_RISK_PCT / 100:
            notes.append(f"{sym}: {side} skipped, min size risks ${risk:.2f} "
                         f"> {MAX_RISK_PCT}% of equity")
            continue
        margin = qty * close / leverage
        if margin > margin_left * 0.9:
            notes.append(f"{sym}: {side} skipped, needs ${margin:.2f} margin")
            continue
        margin_left -= margin
        open_count += 1
        sl = close + stop_dist if side == "SHORT" else close - stop_dist
        tp_dist = tp_r * stop_dist
        tp = close - tp_dist if side == "SHORT" else close + tp_dist
        entries.append({
            "symbol": sym,
            "direction": side,
            "set_leverage": leverage,
            "order": {"side": "SELL" if side == "SHORT" else "BUY",
                      "type": "MARKET", "quantity": qty},
            "stop_loss_algo_order": {
                "algoType": "CONDITIONAL", "symbol": sym,
                "side": "BUY" if side == "SHORT" else "SELL",
                "type": "STOP_MARKET", "closePosition": "true",
                "workingType": "MARK_PRICE", "priceProtect": "true",
                "triggerPrice": round_tick(sl, f["tick"], up=(side == "SHORT")),
            },
            # place right after the stop; recompute from the real fill:
            # entry - tp_distance (SHORT) / entry + tp_distance (LONG)
            "take_profit_algo_order": {
                "algoType": "CONDITIONAL", "symbol": sym,
                "side": "BUY" if side == "SHORT" else "SELL",
                "type": "TAKE_PROFIT_MARKET", "closePosition": "true",
                "workingType": "MARK_PRICE", "priceProtect": "true",
                "triggerPrice": round_tick(tp, f["tick"], up=(side == "LONG")),
            },
            "sl_distance": stop_dist,
            "tp_distance": tp_dist,
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
    ap.add_argument("--universe", default="",
                    help="comma-separated symbols to trade instead of UNIVERSE")
    ap.add_argument("--max-positions", type=int, default=0)
    ap.add_argument("--leverage", type=int, default=0)
    ap.add_argument("--sl-atr", type=float, default=0,
                    help="stop distance in ATR (default SL_ATR = 3)")
    ap.add_argument("--risk-pct", type=float, default=0,
                    help="risk per trade in %% of equity (default RISK_PCT = 4)")
    ap.add_argument("--tp-r", type=float, default=0,
                    help="take profit in multiples of the stop distance (default TP_R = 2)")
    a = ap.parse_args()
    kl = {}
    for item in a.klines:
        sym, path = item.split("=", 1)
        kl[sym] = load_klines(path)
    print(json.dumps(plan(kl, load_positions(a.positions_file), a.equity,
                          a.available, a.now_ms,
                          recent_exits=load_recent_exits(a.positions_file),
                          universe=[x for x in a.universe.split(",") if x] or None,
                          max_positions=a.max_positions or None,
                          leverage=a.leverage or None,
                          sl_atr=a.sl_atr or None,
                          risk_pct=a.risk_pct or None,
                          tp_r=a.tp_r or None), indent=1))


if __name__ == "__main__":
    main()
