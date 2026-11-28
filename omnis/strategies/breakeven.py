"""Breakeven stop mover for the live Donchian positions.

User rule (2026-11-19, "Коли вийдуть в без збиток стопи переносимо"): once a
position is 1R in profit (price has moved one initial stop distance in our
favour), move its stop to breakeven, i.e. entry +/- 0.1% so the round-trip
taker fees (~0.05% each side) are covered. The 2R take profit is left alone.

A stop that is already at or beyond entry (breakeven or profit-locked) is never
touched, so the rule can only reduce risk, never add it.

This script never talks to the exchange. It reads the positions dump and the
current stop trigger of each position and prints the moves as JSON; the
operator (scheduled agent) cancels the old stop and places the new one.

Usage:
  python -m omnis.strategies.breakeven \
      --positions-file <positionInformationV2 dump> \
      --stop SOLUSDT=80.02 --stop XRPUSDT=1.0441 ...
"""
import argparse
import json

from omnis.strategies.donchian_live import FILTERS_PATH, round_tick

TRIGGER_R = 1.0     # move to breakeven once price is 1R in profit
FEE_BUFFER = 0.001  # 0.1% beyond entry covers the round-trip fees


def plan_moves(positions: list, stops: dict, filters: dict) -> list:
    moves = []
    for p in positions:
        amt = float(p.get("positionAmt", 0))
        sym = p["symbol"]
        if amt == 0 or sym not in stops:
            continue
        entry = float(p["entryPrice"])
        mark = float(p["markPrice"])
        stop = float(stops[sym])
        tick = filters[sym]["tick"]
        if amt < 0:  # SHORT: risk exists only while the stop is above entry
            dist = stop - entry
            if dist <= 0 or mark > entry - TRIGGER_R * dist:
                continue
            new_stop = round_tick(entry * (1 - FEE_BUFFER), tick, up=False)
            side = "SHORT"
        else:        # LONG: risk exists only while the stop is below entry
            dist = entry - stop
            if dist <= 0 or mark < entry + TRIGGER_R * dist:
                continue
            new_stop = round_tick(entry * (1 + FEE_BUFFER), tick, up=True)
            side = "LONG"
        moves.append({
            "symbol": sym, "side": side, "entry": entry, "mark": mark,
            "old_stop": stop, "new_stop": new_stop,
            "stop_loss_algo_order": {
                "algoType": "CONDITIONAL", "symbol": sym,
                "side": "BUY" if side == "SHORT" else "SELL",
                "type": "STOP_MARKET", "closePosition": "true",
                "workingType": "MARK_PRICE", "priceProtect": "true",
                "triggerPrice": f"{new_stop:f}".rstrip("0").rstrip("."),
            },
        })
    return moves


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--positions-file", required=True)
    ap.add_argument("--stop", action="append", default=[],
                    help="SYMBOL=current stop trigger price")
    a = ap.parse_args()
    with open(a.positions_file) as f:
        positions = json.load(f)
    with open(FILTERS_PATH) as f:
        filters = json.load(f)
    stops = dict(s.split("=", 1) for s in a.stop)
    print(json.dumps({"moves": plan_moves(positions, stops, filters)}, indent=2))


if __name__ == "__main__":
    main()
