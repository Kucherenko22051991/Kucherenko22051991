"""
OMNIS AI — autonomous Donchian trader that runs WITHOUT Claude.

User, 2026-11-28 ~10:10 UTC: "Навіть коли у клода закінчиться ліміт api сам
торгує на бінансі автономно відкриває закриває позиції без дозволів
додаткових".

Claude cannot act while its usage limit is exhausted, so this script does the
same job on its own, on the user's computer or a small server, with the
user's own Binance API key. It runs exactly the procedure of the scheduled
Claude routine, using the same code:

  * engine:    omnis.strategies.donchian_live.plan()  (Donchian 20/10 on 4h)
  * breakeven: omnis.strategies.breakeven.plan_moves()
  * settings:  Binance Alpha coins, at most 5 positions, leverage 5x,
               stop 2 ATR, risk 4% of equity per trade, take profit 2R,
               no new entries below $220 equity (engine EQUITY_FLOOR)

Every run (default every 30 min, at :02 and :32 UTC):
  1. balance, positions, open algo (conditional) orders, 4h klines
  2. left-over algo orders on flat symbols are cancelled
  3. BREAKEVEN: a position 1R in profit gets its stop moved to entry +/- 0.1%
  4. ORDER AUDIT: a position without a stop gets one at once; without a take
     profit gets one at entry +/- 2 x |entry - stop|
  5. EXITS from the engine (10-bar channel / time stop): market reduceOnly,
     then all algo orders of that symbol are cancelled
  6. ENTRIES from the engine: leverage 5, market order, the planned stop at
     once, then the take profit from the real fill and the stop re-based to
     the fill. If a stop cannot be placed the position is closed at market.
     An entry with a stop or take profit <= 0 is skipped and the next
     candidate takes the slot.

Hard rules it never breaks: never more than 5 positions, never above 5x,
never a position without an exchange-side stop, no averaging down, no
martingale, only the ALPHA symbols, it never touches spot or the wallet,
never transfers and never withdraws. The PROFIT -> SPOT rule stays with the
Claude routine (it needs a transfer permission this key must not have).

API key: create it on the trading (sub-)account with ONLY "Enable Futures"
(trading). Do NOT enable withdrawals or universal transfer. Restrict it to
the IP of the machine that runs this script. Put it in environment variables
BINANCE_API_KEY / BINANCE_API_SECRET or in a .env file in the repository
root (it is git-ignored). Never paste the key into a chat.

Usage (from the repository root):
  python -m omnis.strategies.autotrader --check        # read-only test of key and endpoints
  python -m omnis.strategies.autotrader                # dry run: prints what it WOULD do
  python -m omnis.strategies.autotrader --live         # trades once
  python -m omnis.strategies.autotrader --live --loop  # trades every 30 min, forever

Pause new entries (positions keep being managed): create the file
omnis/logs/PAUSE. Delete it to resume. Actions are logged to
omnis/logs/autotrader.jsonl.
"""
import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from omnis.strategies.donchian_live import FILTERS_PATH, plan, round_tick
from omnis.strategies.breakeven import plan_moves

# ---- strategy settings: identical to the scheduled Claude routine ----------
ALPHA = ["EVAAUSDT", "PIEVERSEUSDT", "IRYSUSDT", "ICNTUSDT", "BTWUSDT", "PIPPINUSDT",
         "NAORISUSDT", "VELVETUSDT", "AKEUSDT", "BLESSUSDT", "VVVUSDT"]
MAX_POSITIONS = 5
LEVERAGE = 5
SL_ATR = 2.0
RISK_PCT = 4.0
TP_R = 2.0
KLINE_LIMIT = 1000
RUN_MINUTES = (2, 32)          # UTC minutes of the hour for --loop

# ---- Binance USD-M endpoints ------------------------------------------------
BASE_URL = "https://fapi.binance.com"
EP = {
    "time": "/fapi/v1/time",
    "klines": "/fapi/v1/klines",
    "balance": "/fapi/v3/balance",
    "positions": "/fapi/v2/positionRisk",
    "leverage": "/fapi/v1/leverage",
    "order": "/fapi/v1/order",
    "algo_order": "/fapi/v1/algoOrder",          # POST new / DELETE one
    "algo_open": "/fapi/v1/openAlgoOrders",      # GET open algo orders
    "algo_open_all": "/fapi/v1/algoOpenOrders",  # DELETE all algo orders of a symbol
}

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LOG_DIR = os.path.join(ROOT, "omnis", "logs")
LOG_PATH = os.path.join(LOG_DIR, "autotrader.jsonl")
PAUSE_PATH = os.path.join(LOG_DIR, "PAUSE")
LOCK_PATH = os.path.join(LOG_DIR, "autotrader.lock")


def now_ms() -> int:
    return int(time.time() * 1000)


def fmt(x) -> str:
    """Number -> plain decimal string (no scientific notation)."""
    s = f"{float(x):.10f}".rstrip("0").rstrip(".")
    return s or "0"


def load_dotenv(path: str):
    """Minimal .env reader: KEY=VALUE lines; existing env vars win."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:     # -sig: Windows Notepad may add a BOM
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


class BinanceError(Exception):
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload
        self.code = payload.get("code") if isinstance(payload, dict) else None
        super().__init__(f"HTTP {status}: {payload}")


class BinanceREST:
    """Small signed REST client (standard library only)."""

    def __init__(self, key: str, secret: str, base: str = BASE_URL, recv_window: int = 10000):
        self.key = key
        self.secret = secret.encode()
        self.base = base
        self.recv_window = recv_window
        self.offset = 0

    def _send(self, method: str, url: str, headers: dict):
        req = urllib.request.Request(url, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            body = e.read()
            try:
                payload = json.loads(body)
            except Exception:
                payload = {"raw": body.decode(errors="replace")}
            raise BinanceError(e.code, payload)

    def public(self, path: str, params: dict = None):
        q = urllib.parse.urlencode(params or {})
        return self._send("GET", self.base + path + ("?" + q if q else ""), {})

    def signed(self, method: str, path: str, params: dict = None):
        p = {k: v for k, v in (params or {}).items() if v is not None}
        p["timestamp"] = now_ms() + self.offset
        p["recvWindow"] = self.recv_window
        q = urllib.parse.urlencode(p)
        sig = hmac.new(self.secret, q.encode(), hashlib.sha256).hexdigest()
        url = f"{self.base}{path}?{q}&signature={sig}"
        return self._send(method, url, {"X-MBX-APIKEY": self.key})

    def sync_time(self):
        self.offset = int(self.public(EP["time"])["serverTime"]) - now_ms()


class Exchange:
    """The handful of operations the trader needs."""

    def __init__(self, rest: BinanceREST):
        self.r = rest

    def sync_time(self):
        self.r.sync_time()

    def usdt_balance(self):
        for a in self.r.signed("GET", EP["balance"]):
            if a.get("asset") == "USDT":
                return (float(a["balance"]), float(a["crossUnPnl"]),
                        float(a["availableBalance"]))
        raise RuntimeError("no USDT balance in futures account")

    def positions(self, symbol: str = None) -> list:
        return self.r.signed("GET", EP["positions"], {"symbol": symbol} if symbol else {})

    def open_algos(self) -> list:
        res = self.r.signed("GET", EP["algo_open"])
        if isinstance(res, dict):            # some API versions wrap the list
            res = res.get("orders") or res.get("data") or []
        return res

    def klines(self, symbol: str) -> list:
        raw = self.r.public(EP["klines"], {"symbol": symbol, "interval": "4h",
                                           "limit": KLINE_LIMIT})
        return [{"time": k[0], "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5])}
                for k in raw]

    def set_leverage(self, symbol: str, leverage: int):
        return self.r.signed("POST", EP["leverage"], {"symbol": symbol, "leverage": leverage})

    def market(self, symbol: str, side: str, qty: float, reduce_only: bool = False):
        p = {"symbol": symbol, "side": side, "type": "MARKET", "quantity": fmt(qty),
             "newOrderRespType": "RESULT"}
        if reduce_only:
            p["reduceOnly"] = "true"
        return self.r.signed("POST", EP["order"], p)

    def new_algo(self, order: dict):
        p = dict(order)
        p["triggerPrice"] = fmt(p["triggerPrice"])
        return self.r.signed("POST", EP["algo_order"], p)

    def cancel_algo(self, algo_id):
        return self.r.signed("DELETE", EP["algo_order"], {"algoId": algo_id})

    def cancel_all_algos(self, symbol: str):
        return self.r.signed("DELETE", EP["algo_open_all"], {"symbol": symbol})


def nonzero(positions: list) -> list:
    return [p for p in positions if float(p.get("positionAmt", 0)) != 0]


def engine_positions(raw: list) -> dict:
    """Same conversion as donchian_live.load_positions, from a list."""
    out = {}
    for p in nonzero(raw):
        amt = float(p["positionAmt"])
        out[p["symbol"]] = {"side": "LONG" if amt > 0 else "SHORT", "qty": abs(amt),
                            "entry": float(p["entryPrice"]),
                            "update_time": int(p.get("updateTime", 0))}
    return out


def engine_recent_exits(raw: list) -> dict:
    """Same as donchian_live.load_recent_exits, from a list."""
    return {p["symbol"]: int(p.get("updateTime", 0)) for p in raw
            if float(p.get("positionAmt", 0)) == 0 and int(p.get("updateTime", 0)) > 0}


def algos_by_symbol(algos: list) -> dict:
    """{symbol: {"STOP_MARKET": order, "TAKE_PROFIT_MARKET": order, "all": [...]}}"""
    out = {}
    for o in algos:
        d = out.setdefault(o["symbol"], {"all": []})
        d["all"].append(o)
        typ = o.get("orderType") or o.get("type")
        if str(o.get("closePosition")).lower() == "true" and typ in ("STOP_MARKET",
                                                                       "TAKE_PROFIT_MARKET"):
            d[typ] = o
    return out


def entry_valid(e: dict) -> bool:
    sl = float(e["stop_loss_algo_order"]["triggerPrice"])
    tp = float(e["take_profit_algo_order"]["triggerPrice"])
    return sl > 0 and tp > 0 and e["order"]["quantity"] > 0


class Trader:
    def __init__(self, ex: Exchange, filters: dict, live: bool, log=print,
                 universe: list = None):
        self.ex = ex
        self.filters = filters
        self.live = live
        self.log = log
        self.universe = list(universe or ALPHA)
        self.actions = []

    # -- helpers ---------------------------------------------------------
    def act(self, what: str, fn, *args):
        """Run a write operation (only in live mode) and record it."""
        rec = {"action": what}
        if not self.live:
            rec["dry_run"] = True
            self.actions.append(rec)
            self.log(f"[план] {what}")
            return None
        try:
            res = fn(*args)
            rec["ok"] = True
            self.actions.append(rec)
            self.log(f"[виконано] {what}")
            return res
        except Exception as e:
            rec["ok"] = False
            rec["error"] = str(e)
            self.actions.append(rec)
            self.log(f"[ПОМИЛКА] {what}: {e}")
            raise

    def try_act(self, what: str, fn, *args):
        try:
            return self.act(what, fn, *args), True
        except Exception:
            return None, False

    def stop_order(self, sym: str, is_short: bool, price: float) -> dict:
        return {"algoType": "CONDITIONAL", "symbol": sym, "side": "BUY" if is_short else "SELL",
                "type": "STOP_MARKET", "closePosition": "true", "workingType": "MARK_PRICE",
                "priceProtect": "true", "triggerPrice": price}

    def tp_order(self, sym: str, is_short: bool, price: float) -> dict:
        o = self.stop_order(sym, is_short, price)
        o["type"] = "TAKE_PROFIT_MARKET"
        return o

    def plan(self, raw_pos, klines, equity, available, universe):
        return plan(klines, engine_positions(raw_pos), equity, available, now_ms(),
                    recent_exits=engine_recent_exits(raw_pos), universe=universe,
                    max_positions=MAX_POSITIONS, leverage=LEVERAGE, sl_atr=SL_ATR,
                    risk_pct=RISK_PCT, tp_r=TP_R)

    # -- one full cycle --------------------------------------------------
    def run_once(self) -> dict:
        self.actions = []
        self.ex.sync_time()
        wallet, unpnl, available = self.ex.usdt_balance()
        raw = self.ex.positions()
        algos = algos_by_symbol(self.ex.open_algos())
        klines = {}
        for sym in self.universe:
            try:
                klines[sym] = self.ex.klines(sym)
            except Exception as e:
                self.log(f"{sym}: свічки не отримано ({e}), монету пропущено")
        equity = wallet + unpnl
        held = {p["symbol"]: p for p in nonzero(raw)}
        self.log(f"капітал ${equity:.2f}, вільно ${available:.2f}, позицій {len(held)}")
        for sym in held:
            if sym not in self.universe:
                self.log(f"{sym}: позиція не з нашого списку — не чіпаю")

        # 2. left-over algo orders on flat ALPHA symbols
        for sym, d in algos.items():
            if sym in self.universe and sym not in held and d["all"]:
                self.try_act(f"{sym}: скасувати залишкові умовні ордери (позиції немає)",
                             self.ex.cancel_all_algos, sym)

        # 3. breakeven
        stops = {s: d["STOP_MARKET"]["triggerPrice"] for s, d in algos.items()
                 if "STOP_MARKET" in d and s in held and s in self.universe}
        moves = plan_moves(list(held.values()), stops, self.filters)
        for m in moves:
            sym = m["symbol"]
            is_short = m["side"] == "SHORT"
            old = algos[sym]["STOP_MARKET"]
            _, ok = self.try_act(f"{sym}: стоп у беззбиток — скасувати старий стоп {old['triggerPrice']}",
                                 self.ex.cancel_algo, old["algoId"])
            if not ok:
                # the cancel failed: if the old stop is still there, leave it for
                # the next run; if it is gone, the new stop must go in right now
                fresh = algos_by_symbol(self.ex.open_algos()).get(sym, {})
                if "STOP_MARKET" in fresh:
                    continue
            _, ok = self.try_act(f"{sym}: стоп у беззбиток — новий стоп {m['new_stop']}",
                                 self.ex.new_algo, m["stop_loss_algo_order"])
            if not ok:
                _, ok = self.try_act(f"{sym}: повернути старий стоп {old['triggerPrice']}",
                                     self.ex.new_algo,
                                     self.stop_order(sym, is_short, float(old["triggerPrice"])))
                if not ok:
                    amt = abs(float(held[sym]["positionAmt"]))
                    self.try_act(f"{sym}: стоп не ставиться — закрити за ринком", self.ex.market,
                                 sym, "BUY" if is_short else "SELL", amt, True)
        if moves and self.live:
            algos = algos_by_symbol(self.ex.open_algos())   # audit must see the real state
            raw = self.ex.positions()
            held = {p["symbol"]: p for p in nonzero(raw)}

        # 4 + 5. engine plan: exits and order audit
        p = self.plan(raw, klines, equity, available, self.universe)
        for n in p["notes"]:
            self.log(f"примітка: {n}")
        exiting = {x["symbol"] for x in p["exits"]}
        held_plan = {h["symbol"]: h for h in p["held"]}
        for sym, pos in held.items():
            if sym not in self.universe or sym in exiting:
                continue
            d = algos.get(sym, {})
            amt = float(pos["positionAmt"])
            is_short = amt < 0
            entry = float(pos["entryPrice"])
            tick = self.filters[sym]["tick"]
            stop_px = float(d["STOP_MARKET"]["triggerPrice"]) if "STOP_MARKET" in d else None
            if stop_px is None:
                fb = held_plan.get(sym, {}).get("fallback_stop_loss_algo_order")
                if fb is None:
                    self.log(f"{sym}: БЕЗ СТОПУ і рушій не дав резервного стопу — закриваю за ринком")
                    self.try_act(f"{sym}: закрити за ринком (немає стопу)", self.ex.market, sym,
                                 "BUY" if is_short else "SELL", abs(amt), True)
                    continue
                _, ok = self.try_act(f"{sym}: поставити відсутній стоп {fb['triggerPrice']}",
                                     self.ex.new_algo, fb)
                if not ok and self.live:
                    self.try_act(f"{sym}: стоп не ставиться — закрити за ринком", self.ex.market,
                                 sym, "BUY" if is_short else "SELL", abs(amt), True)
                    continue
                stop_px = float(fb["triggerPrice"])
            if "TAKE_PROFIT_MARKET" not in d:
                dist = abs(entry - stop_px)
                beyond = (stop_px <= entry) if is_short else (stop_px >= entry)
                if beyond or dist == 0:
                    fb = held_plan.get(sym, {}).get("fallback_take_profit_algo_order")
                    if fb:
                        self.try_act(f"{sym}: поставити відсутній тейк {fb['triggerPrice']}",
                                     self.ex.new_algo, fb)
                else:
                    tp = (round_tick(entry - TP_R * dist, tick, up=False) if is_short
                          else round_tick(entry + TP_R * dist, tick, up=True))
                    if tp > 0:
                        self.try_act(f"{sym}: поставити відсутній тейк {tp}", self.ex.new_algo,
                                     self.tp_order(sym, is_short, tp))

        for x in p["exits"]:
            sym = x["symbol"]
            o = x["order"]
            _, ok = self.try_act(f"{sym}: вихід ({x['reason']}) — {o['side']} {o['quantity']} за ринком",
                                 self.ex.market, sym, o["side"], o["quantity"], True)
            if ok:
                self.try_act(f"{sym}: скасувати стоп і тейк після виходу",
                             self.ex.cancel_all_algos, sym)

        # 6. entries
        if os.path.exists(PAUSE_PATH):
            self.log("нові входи на паузі (є файл omnis/logs/PAUSE)")
            return self.summary(equity, available)
        if p["exits"] and self.live:
            wallet, unpnl, available = self.ex.usdt_balance()
            equity = wallet + unpnl
            raw = self.ex.positions()
        universe = list(self.universe)
        entries = []
        while universe:      # never pass an empty list: the engine would fall back to its own coins
            p = self.plan(raw, klines, equity, available, universe)
            bad = [e for e in p["entries"] if not entry_valid(e)]
            if not bad:
                entries = p["entries"]
                break
            for e in bad:
                self.log(f"{e['symbol']}: вхід {e['direction']} пропущено — стоп або тейк ≤ 0")
                universe.remove(e["symbol"])
        open_now = len([s for s in engine_positions(raw) if s in self.universe])
        for e in entries:
            if e["symbol"] not in self.universe:
                continue
            if open_now >= MAX_POSITIONS:
                break
            if self.enter(e):
                open_now += 1
        return self.summary(equity, available)

    def enter(self, e: dict) -> bool:
        sym = e["symbol"]
        is_short = e["direction"] == "SHORT"
        tick = self.filters[sym]["tick"]
        o = e["order"]
        self.log(f"{sym}: сигнал {'продаж' if is_short else 'купівля'} {o['quantity']}, "
                 f"ризик ${e['risk_usd']}")
        _, ok = self.try_act(f"{sym}: плече {LEVERAGE}x", self.ex.set_leverage, sym, LEVERAGE)
        if not ok:
            return False
        res, ok = self.try_act(f"{sym}: {o['side']} {o['quantity']} за ринком", self.ex.market,
                               sym, o["side"], o["quantity"], False)
        if not self.live:
            self.act(f"{sym}: стоп {e['stop_loss_algo_order']['triggerPrice']}", None)
            self.act(f"{sym}: тейк від ціни входу (≈{e['take_profit_algo_order']['triggerPrice']})", None)
            return True
        if not ok or float((res or {}).get("executedQty", 0)) <= 0:
            return False
        planned = e["stop_loss_algo_order"]
        sl_res, ok = self.try_act(f"{sym}: стоп {planned['triggerPrice']}", self.ex.new_algo, planned)
        if not ok:
            self.try_act(f"{sym}: стоп не ставиться — закрити за ринком", self.ex.market, sym,
                         "BUY" if is_short else "SELL", float(res["executedQty"]), True)
            return False
        pos = nonzero(self.ex.positions(sym))
        if not pos:
            return False
        entry = float(pos[0]["entryPrice"])
        tp = (round_tick(entry - e["tp_distance"], tick, up=False) if is_short
              else round_tick(entry + e["tp_distance"], tick, up=True))
        if tp > 0:
            self.try_act(f"{sym}: тейк {tp} (2R від ціни входу {entry})", self.ex.new_algo,
                         self.tp_order(sym, is_short, tp))
        new_sl = (round_tick(entry + e["sl_distance"], tick, up=True) if is_short
                  else round_tick(entry - e["sl_distance"], tick, up=False))
        if new_sl != float(planned["triggerPrice"]) and new_sl > 0:
            algo_id = (sl_res or {}).get("algoId")
            _, ok = self.try_act(f"{sym}: перенести стоп до ціни входу — скасувати {planned['triggerPrice']}",
                                 self.ex.cancel_algo, algo_id)
            if ok:
                _, ok = self.try_act(f"{sym}: стоп {new_sl}", self.ex.new_algo,
                                     self.stop_order(sym, is_short, new_sl))
                if not ok:
                    _, ok = self.try_act(f"{sym}: повернути стоп {planned['triggerPrice']}",
                                         self.ex.new_algo, planned)
                    if not ok:
                        self.try_act(f"{sym}: стоп не ставиться — закрити за ринком",
                                     self.ex.market, sym, "BUY" if is_short else "SELL",
                                     float(res["executedQty"]), True)
                        return False
        return True

    def summary(self, equity, available) -> dict:
        return {"time_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "live": self.live, "equity": round(equity, 2),
                "available": round(available, 2), "actions": self.actions}


def append_log(rec: dict):
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def acquire_lock() -> bool:
    os.makedirs(LOG_DIR, exist_ok=True)
    try:
        if os.path.exists(LOCK_PATH) and time.time() - os.path.getmtime(LOCK_PATH) > 900:
            os.remove(LOCK_PATH)     # stale lock from a crashed run
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False


def release_lock():
    try:
        os.remove(LOCK_PATH)
    except FileNotFoundError:
        pass


def seconds_to_next_run(minutes=RUN_MINUTES) -> float:
    t = time.time()
    now = datetime.fromtimestamp(t, timezone.utc)
    base = t - now.minute * 60 - now.second - now.microsecond / 1e6
    cands = [base + h * 3600 + m * 60 for h in (0, 1) for m in minutes]
    return min(c for c in cands if c > t + 5) - t


def check(ex: Exchange):
    """Read-only test: key, clock, balance, positions, algo orders, klines."""
    ex.sync_time()
    w, u, a = ex.usdt_balance()
    print(f"[так] ключ працює. Баланс USDT {w:.2f}, плюс/мінус {u:+.2f}, вільно {a:.2f}")
    pos = nonzero(ex.positions())
    print(f"[так] позиції: {len(pos)}")
    for p in pos:
        print(f"   {p['symbol']} {p['positionAmt']} вхід {p['entryPrice']} зараз {p['markPrice']}")
    algos = ex.open_algos()
    print(f"[так] умовні ордери (стопи й тейки): {len(algos)}")
    for o in algos:
        print(f"   {o['symbol']} {o.get('orderType') or o.get('type')} {o['triggerPrice']}")
    k = ex.klines(ALPHA[0])
    print(f"[так] свічки {ALPHA[0]}: {len(k)}")
    print("Перевірку завершено. Нічого не змінено.")


def setup_key():
    """Ask for the key in the local console (hidden input) and write .env."""
    import getpass
    print("Налаштування ключа Binance. Під час введення символи не показуються —")
    print("просто вставте (Ctrl+V або права кнопка миші) і натисніть Enter.")
    key = getpass.getpass("Ключ (API Key): ").strip()
    secret = getpass.getpass("Секретний ключ (Secret Key): ").strip()
    if not key or not secret:
        sys.exit("Ключ порожній — нічого не збережено.")
    path = os.path.join(ROOT, ".env")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"BINANCE_API_KEY={key}\nBINANCE_API_SECRET={secret}\n")
    os.environ["BINANCE_API_KEY"], os.environ["BINANCE_API_SECRET"] = key, secret
    print(f"Ключ збережено у файлі {path} (лише на цьому комп'ютері).")


def main():
    ap = argparse.ArgumentParser(description="OMNIS autonomous Donchian trader")
    ap.add_argument("--live", action="store_true", help="really place orders (default: dry run)")
    ap.add_argument("--loop", action="store_true", help="run every 30 min forever")
    ap.add_argument("--check", action="store_true", help="read-only connectivity test")
    ap.add_argument("--setup", action="store_true", help="enter the API key and save it to .env")
    a = ap.parse_args()

    if a.setup:
        setup_key()
    load_dotenv(os.path.join(ROOT, ".env"))
    key, secret = os.environ.get("BINANCE_API_KEY"), os.environ.get("BINANCE_API_SECRET")
    if not key or not secret:
        sys.exit("Немає ключа: задайте BINANCE_API_KEY і BINANCE_API_SECRET (або файл .env).")
    ex = Exchange(BinanceREST(key, secret))
    if a.check or a.setup:
        try:
            check(ex)
        except BinanceError as e:
            sys.exit(f"Біржа відхилила запит: {e}\nПеревірте ключ, дозвіл «Enable Futures» "
                     f"і обмеження за IP-адресою.")
        except Exception as e:
            sys.exit(f"Немає зв'язку з біржею: {e}")
        return
    with open(FILTERS_PATH) as f:
        filters = json.load(f)

    while True:
        if acquire_lock():
            try:
                rec = Trader(ex, filters, live=a.live).run_once()
                append_log(rec)
            except Exception as e:
                append_log({"time_utc": datetime.now(timezone.utc).isoformat(),
                            "error": str(e), "trace": traceback.format_exc()})
                print(f"ПОМИЛКА циклу: {e}")
            finally:
                release_lock()
        else:
            print("Інший запуск ще працює — пропускаю цей цикл")
        if not a.loop:
            break
        wait = seconds_to_next_run()
        print(f"Наступний цикл через {int(wait // 60)} хв")
        time.sleep(wait)


if __name__ == "__main__":
    main()
