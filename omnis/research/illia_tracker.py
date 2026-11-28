"""Paper tracker for the trade ideas of the TikTok author @illia_btc.

User, 2026-11-28: the user believes the author's forecasts are very accurate and
asked to trade them. The month of videos checked by hand did not confirm that,
so the user agreed ("Так") to a 2-3 week paper journal first. Nothing here
places orders: the script only replays each idea against 4h candles and says
what would have happened.

The ideas live in omnis/research/illia_calls.csv, one row per idea, written by
hand from the charts in his videos (most uploads have no sound):
  id, video_utc, video_id, symbol, side, entry_lo, entry_hi, stop, target, note

Rules, fixed in advance so the result cannot be bent afterwards:
- Only candles that open after the video time count.
- Fill: a LONG fills when the low reaches entry_hi (a SHORT when the high
  reaches entry_lo). If the candle already opens inside or past the zone the
  fill is at the open; otherwise at the middle of the zone when price reaches
  it, else at the zone edge.
- Stop: his red line. If he showed none, a stop 3% beyond the far edge of the
  zone is used and marked "умовний".
- Target: his target if he gave one, otherwise 2R from the fill.
- A candle that touches both the stop and the target counts as a stop.
- An idea that has not filled 21 days after the video expires.
- Result is in R (initial risk units): a stop is -1R, a target is +R of the
  target; an open idea is marked to the last close.

Usage:
  python -m omnis.research.illia_tracker \
      --calls omnis/research/illia_calls.csv \
      --klines BTCUSDT=<4h klines json> --klines ETHUSDT=<...> ...
  Add --json for machine output; the default is a Ukrainian text report.
"""
import argparse
import csv
import json
from datetime import datetime, timezone

DEFAULT_STOP_PCT = 0.03
EXPIRY_DAYS = 21
DAY_MS = 86_400_000

STATUS_UA = {
    "WAITING": "чекає входу",
    "OPEN": "відкрита",
    "STOP": "стоп",
    "TARGET": "ціль",
    "EXPIRED": "прострочена",
    "NO_DATA": "немає даних",
}


def _num(value):
    value = (value or "").strip()
    return float(value) if value else None


def load_calls(path: str) -> list:
    with open(path, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    calls = []
    for r in rows:
        ts = datetime.fromisoformat(r["video_utc"]).replace(tzinfo=timezone.utc)
        calls.append({
            "id": int(r["id"]), "video_ms": int(ts.timestamp() * 1000),
            "video_utc": r["video_utc"], "symbol": r["symbol"].strip(),
            "side": r["side"].strip().upper(), "lo": float(r["entry_lo"]),
            "hi": float(r["entry_hi"]), "stop": _num(r["stop"]),
            "target": _num(r["target"]), "note": r.get("note", ""),
        })
    return calls


def load_klines(path: str) -> list:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]))
            for k in raw]


def evaluate(call: dict, bars: list, now_ms: int = None) -> dict:
    side, lo, hi = call["side"], call["lo"], call["hi"]
    long = side == "LONG"
    stop = call["stop"]
    stop_src = "його"
    if stop is None:
        stop = lo * (1 - DEFAULT_STOP_PCT) if long else hi * (1 + DEFAULT_STOP_PCT)
        stop_src = "умовний"
    out = {"id": call["id"], "symbol": call["symbol"], "side": side,
           "zone": [lo, hi], "stop": stop, "stop_src": stop_src,
           "status": "WAITING", "entry": None, "fill_ms": None,
           "target": call["target"], "target_src": "його" if call["target"] else "2R",
           "close_ms": None, "exit": None, "r": None, "max_r": None, "last": None}
    if not bars:
        out["status"] = "NO_DATA"
        return out
    out["last"] = bars[-1][4]
    bars = [b for b in bars if b[0] >= call["video_ms"]]
    if not bars:
        return out  # no candle has opened since the video yet
    out["last"] = bars[-1][4]
    mid = (lo + hi) / 2
    entry = risk = target = None
    best = 0.0
    for t, o, h, l, c in bars:
        if entry is None:
            if t - call["video_ms"] > EXPIRY_DAYS * DAY_MS:
                out["status"] = "EXPIRED"
                return out
            touched = l <= hi if long else h >= lo
            if not touched:
                continue
            if (long and o <= hi) or (not long and o >= lo):
                entry = o
            elif (long and l <= mid) or (not long and h >= mid):
                entry = mid
            else:
                entry = hi if long else lo
            risk = abs(entry - stop)
            if risk <= 0 or (long and entry <= stop) or (not long and entry >= stop):
                out.update(status="STOP", entry=entry, fill_ms=t, close_ms=t,
                           exit=stop, r=-1.0, max_r=0.0)
                return out
            target = call["target"] or (entry + 2 * risk if long else entry - 2 * risk)
            out.update(entry=entry, fill_ms=t, target=target, status="OPEN")
            # the fill candle: its extreme on the stop side may come after the fill
            if (long and l <= stop) or (not long and h >= stop):
                out.update(status="STOP", close_ms=t, exit=stop, r=-1.0, max_r=0.0)
                return out
            continue
        fav = (h - entry) / risk if long else (entry - l) / risk
        best = max(best, fav)
        hit_stop = l <= stop if long else h >= stop
        hit_tgt = h >= target if long else l <= target
        if hit_stop:
            out.update(status="STOP", close_ms=t, exit=stop, r=-1.0,
                       max_r=round(best, 2))
            return out
        if hit_tgt:
            r = abs(target - entry) / risk
            out.update(status="TARGET", close_ms=t, exit=target, r=round(r, 2),
                       max_r=round(max(best, r), 2))
            return out
    if entry is not None:
        last = bars[-1][4]
        out["r"] = round(((last - entry) if long else (entry - last)) / risk, 2)
        out["max_r"] = round(best, 2)
    return out


def summarize(results: list) -> dict:
    closed = [r for r in results if r["status"] in ("STOP", "TARGET")]
    wins = [r for r in closed if r["status"] == "TARGET"]
    open_ = [r for r in results if r["status"] == "OPEN"]
    return {
        "ideas": len(results),
        "filled": len(closed) + len(open_),
        "targets": len(wins),
        "stops": len(closed) - len(wins),
        "open": len(open_),
        "waiting": sum(r["status"] == "WAITING" for r in results),
        "expired": sum(r["status"] == "EXPIRED" for r in results),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "closed_r": round(sum(r["r"] for r in closed), 2),
        "open_r": round(sum(r["r"] for r in open_ if r["r"] is not None), 2),
        "reached_1r": sum((r["max_r"] or 0) >= 1 for r in results),
    }


def _fmt(x):
    if x is None:
        return "—"
    if abs(x) >= 1000:
        return f"{x:,.0f}".replace(",", " ")
    if abs(x) >= 1:
        return f"{x:.2f}"
    return f"{x:.4f}"


def _day(ms):
    if ms is None:
        return "—"
    return datetime.fromtimestamp(ms / 1000 + 7200, tz=timezone.utc).strftime("%d.%m")


def report(calls: list, results: list, summary: dict) -> str:
    side_ua = {"LONG": "купівля", "SHORT": "продаж"}
    lines = ["| № | Дата відео | Монета | Напрям | Зона входу | Стоп | Ціль | Статус | Вхід | R |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    by_id = {c["id"]: c for c in calls}
    for r in results:
        c = by_id[r["id"]]
        zone = _fmt(r["zone"][0]) if r["zone"][0] == r["zone"][1] else \
            f"{_fmt(r['zone'][0])}–{_fmt(r['zone'][1])}"
        stop = _fmt(r["stop"]) + (" (умовний)" if r["stop_src"] == "умовний" else "")
        tgt = _fmt(r["target"]) + (" (2R)" if r["target_src"] == "2R" and r["target"] else "")
        status = STATUS_UA[r["status"]]
        if r["status"] in ("STOP", "TARGET"):
            status += f" {_day(r['close_ms'])}"
        rv = "—" if r["r"] is None else f"{r['r']:+.2f}"
        lines.append(f"| {r['id']} | {c['video_utc'][8:10]}.{c['video_utc'][5:7]} | "
                     f"{r['symbol'].replace('USDT', '')} | {side_ua[r['side']]} | {zone} | "
                     f"{stop} | {tgt} | {status} | {_fmt(r['entry'])} | {rv} |")
    s = summary
    wr = "—" if s["win_rate"] is None else f"{s['win_rate']}%"
    lines.append("")
    lines.append(f"Ідей: {s['ideas']}; спрацював вхід: {s['filled']}; ціль: {s['targets']}; "
                 f"стоп: {s['stops']}; відкриті: {s['open']}; чекають: {s['waiting']}; "
                 f"прострочені: {s['expired']}.")
    lines.append(f"Влучність по закритих: {wr}; сума закритих {s['closed_r']:+.2f}R; "
                 f"відкриті зараз {s['open_r']:+.2f}R; дійшли до +1R: {s['reached_1r']}.")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--calls", default="omnis/research/illia_calls.csv")
    ap.add_argument("--klines", action="append", default=[], metavar="SYM=path")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    calls = load_calls(args.calls)
    data = {}
    for item in args.klines:
        sym, path = item.split("=", 1)
        data[sym.strip()] = load_klines(path)
    results = [evaluate(c, data.get(c["symbol"], [])) for c in calls]
    summary = summarize(results)
    if args.json:
        print(json.dumps({"results": results, "summary": summary}, ensure_ascii=False, indent=1))
    else:
        print(report(calls, results, summary))


if __name__ == "__main__":
    main()
