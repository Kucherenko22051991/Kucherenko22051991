"""Spot "new listings" buyer for futures profit (user 2026-11-26).

The user's rule: futures trading continues as before; any PROFIT the futures
account makes is moved to the spot wallet and spent on young, freshly listed
Binance spot coins. Only profit is used, never the deposit.

This module only plans; the operator executes with the Binance MCP tools.

Subcommands
-----------
candidates  newest USDT spot pairs after static filters (status TRADING, spot
            allowed, account permission group, no stablecoins, no tokenized
            stocks).  Print them so the operator can fetch 24h tickers.

plan        rank the candidates and size the buys:
              * 24h quote volume >= --min-quote-volume
              * listed at most --max-age-days ago (first 1d kline open time,
                passed as --first-open SYM=ms; candidates without it are listed
                in "need_first_open" so the operator can fetch them and rerun)
              * highest 24h quote volume first, at most --max-coins coins
              * budget split equally, every buy >= --min-order (8 USDT, so the -30% stop
                still clears the 5 USDT notional minimum) and >= the
                symbol's NOTIONAL filter

stop        stop-loss order fields for a filled buy: STOP_LOSS (market) SELL
            of the net quantity, stopPrice = avg fill * (1 - stop_pct),
            rounded down to the tick, quantity floored to the step.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time

ACCOUNT_PERMISSION = "TRD_GRP_072"
STABLES = {
    "USDC", "FDUSD", "TUSD", "USDP", "DAI", "EUR", "EURI", "AEUR", "PYUSD",
    "USDE", "RWUSD", "BFUSD", "USD1", "XUSD", "U", "USDS", "BUSD", "USDT",
    "FRAX", "KGST",            # pegged to fiat
    "XAUT", "PAXG",            # pegged to gold
}


def load_json(path):
    with open(path) as fh:
        return json.load(fh)


def symbol_filters(sym: dict) -> dict:
    out = {"tick": None, "step": None, "min_qty": 0.0, "min_notional": 5.0}
    for f in sym.get("filters", []):
        t = f.get("filterType")
        if t == "PRICE_FILTER":
            out["tick"] = float(f["tickSize"])
        elif t == "LOT_SIZE":
            out["step"] = float(f["stepSize"])
            out["min_qty"] = float(f["minQty"])
        elif t in ("NOTIONAL", "MIN_NOTIONAL"):
            out["min_notional"] = float(f.get("minNotional", 5.0))
    return out


def is_tokenized_stock(base: str) -> bool:
    # Binance tokenized stocks / ETFs are listed as <TICKER>B (NFLXB, GMEB, IBITB...).
    return len(base) >= 3 and base.endswith("B") and base.isascii() and base.isalpha() and base != "BNB"


def is_stable(base: str) -> bool:
    return base in STABLES or "USD" in base


def permission_ok(sym: dict) -> bool:
    sets = sym.get("permissionSets") or [sym.get("permissions") or []]
    return any(ACCOUNT_PERMISSION in s or "SPOT" in s for s in sets)


def candidates(exchange_info: dict, tail: int) -> list[dict]:
    usdt = [s for s in exchange_info["symbols"]
            if s.get("quoteAsset") == "USDT" and s.get("status") == "TRADING"
            and s.get("isSpotTradingAllowed", True)]
    newest = usdt[-tail:]  # exchangeInfo lists symbols in listing order
    out = []
    for s in newest:
        base = s["baseAsset"]
        if is_stable(base) or is_tokenized_stock(base) or not permission_ok(s):
            continue
        out.append(s)
    return out


def floor_step(x: float, step: float) -> float:
    if not step:
        return x
    n = math.floor(x / step + 1e-9)
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(n * step, decimals)


def parse_kv(items):
    out = {}
    for it in items or []:
        k, v = it.split("=", 1)
        out[k] = int(float(v))
    return out


def load_tickers(paths) -> dict:
    out = {}
    for p in paths or []:
        data = load_json(p)
        if isinstance(data, dict):
            data = [data]
        for t in data:
            out[t["symbol"]] = t
    return out


def plan(args) -> dict:
    info = load_json(args.exchange_info)
    cands = {s["symbol"]: s for s in candidates(info, args.tail)}
    tickers = load_tickers(args.tickers)
    first_open = parse_kv(args.first_open)
    now_ms = args.now_ms or int(time.time() * 1000)
    max_age_ms = args.max_age_days * 86_400_000

    liquid = []
    for sym, s in cands.items():
        t = tickers.get(sym)
        if not t:
            continue
        qv = float(t.get("quoteVolume", 0))
        if qv < args.min_quote_volume:
            continue
        liquid.append((qv, sym, t))
    liquid.sort(reverse=True)

    need, ranked, notes = [], [], []
    for qv, sym, t in liquid:
        fo = first_open.get(sym)
        if fo is None:
            need.append(sym)
            continue
        age_d = (now_ms - fo) / 86_400_000
        if now_ms - fo > max_age_ms:
            notes.append(f"{sym}: listed {age_d:.0f} days ago, too old")
            continue
        ranked.append({"symbol": sym, "quote_volume_24h": round(qv), "age_days": round(age_d, 1),
                       "last_price": float(t.get("lastPrice", 0)),
                       "change_24h_pct": float(t.get("priceChangePercent", 0))})

    # only rank once the age of every liquid candidate ahead of a pick is known
    need = need[: args.max_check]
    budget = math.floor(args.budget * 100) / 100
    n = min(args.max_coins, len(ranked), int(budget // args.min_order)) if budget > 0 else 0
    picks = []
    if n > 0 and not need:
        each = math.floor(budget / n * 100) / 100
        for r in ranked[:n]:
            f = symbol_filters(cands[r["symbol"]])
            if each < max(f["min_notional"], args.min_order):
                notes.append(f"{r['symbol']}: {each} below minimum order")
                continue
            picks.append({**r, "order": {"symbol": r["symbol"], "side": "BUY", "type": "MARKET",
                                         "quoteOrderQty": each, "newOrderRespType": "FULL"},
                          "filters": f})
    return {"budget": budget, "need_first_open": need, "ranked": ranked[: args.max_coins * 3],
            "picks": picks, "notes": notes,
            "stop_pct": args.stop_pct}


def stop(args) -> dict:
    info = load_json(args.exchange_info)
    sym = next(s for s in info["symbols"] if s["symbol"] == args.symbol)
    f = symbol_filters(sym)
    qty = floor_step(args.net_qty, f["step"])
    sp = args.avg_price * (1 - args.stop_pct / 100)
    sp = floor_step(sp, f["tick"])
    return {"symbol": args.symbol, "side": "SELL", "type": "STOP_LOSS",
            "quantity": qty, "stopPrice": sp,
            "note": f"stop {args.stop_pct}% below avg fill {args.avg_price}"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("candidates")
    c.add_argument("--exchange-info", required=True)
    c.add_argument("--tail", type=int, default=150)

    p = sub.add_parser("plan")
    p.add_argument("--exchange-info", required=True)
    p.add_argument("--tickers", nargs="*", default=[])
    p.add_argument("--first-open", action="append", default=[], help="SYM=first 1d kline open time in ms")
    p.add_argument("--budget", type=float, required=True, help="USDT available on spot from futures profit")
    p.add_argument("--tail", type=int, default=150)
    p.add_argument("--max-coins", type=int, default=3)
    p.add_argument("--min-order", type=float, default=8.0, help="smallest buy; 8 keeps the -30%% stop above the 5 USDT notional minimum")
    p.add_argument("--min-quote-volume", type=float, default=5_000_000)
    p.add_argument("--max-age-days", type=int, default=90)
    p.add_argument("--max-check", type=int, default=10)
    p.add_argument("--stop-pct", type=float, default=30.0)
    p.add_argument("--now-ms", type=int, default=0)

    s = sub.add_parser("stop")
    s.add_argument("--exchange-info", required=True)
    s.add_argument("--symbol", required=True)
    s.add_argument("--net-qty", type=float, required=True, help="executedQty minus base-asset commission")
    s.add_argument("--avg-price", type=float, required=True)
    s.add_argument("--stop-pct", type=float, default=30.0)

    args = ap.parse_args(argv)
    if args.cmd == "candidates":
        info = load_json(args.exchange_info)
        out = [{"symbol": s["symbol"], "base": s["baseAsset"]} for s in candidates(info, args.tail)]
    elif args.cmd == "plan":
        out = plan(args)
    else:
        out = stop(args)
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()


if __name__ == "__main__":
    main()
