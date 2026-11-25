"""Live setup scanner for the hourly high-potential entry routine.

Usage: python3 -m omnis.backtest.scalp.scan SYM=path5m,path1h [...] [--now-ms MS]
Klines files are Binance futures kline JSON arrays (as saved by the MCP tool).
Prints, per symbol, the multi-timeframe state and the three setups
(a) trend pullback rejection, (b) 1h liquidity sweep, (c) 1h break of structure
with their confirmations, stop, 3R target and room to the next 1h swing level.
Only closed candles are used. No orders are placed here.
"""
import json, sys, time
import numpy as np, pandas as pd
from omnis.backtest.scalp.bt import ema, rsi, atr


def read(path, now_ms):
    d = json.loads(open(path).read())
    df = pd.DataFrame(d).iloc[:, :11].astype(float)
    df.columns = ['t', 'o', 'h', 'l', 'c', 'v', 'ct', 'qv', 'n', 'tbv', 'tbqv']
    df = df[df.ct < now_ms].reset_index(drop=True)
    df['dt'] = pd.to_datetime(df.t, unit='ms')
    return df


def ind(df):
    for n in (9, 21, 50, 200): df[f'e{n}'] = ema(df.c, n)
    df['rsi'] = rsi(df.c); df['atr'] = atr(df)
    m = ema(df.c, 12) - ema(df.c, 26); df['macdh'] = m - ema(m, 9)
    df['vavg'] = df.v.rolling(20).mean()
    return df


def resample(df, rule):
    r = df.set_index('dt').resample(rule, label='left', closed='left').agg(
        {'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last', 'v': 'sum', 'tbv': 'sum'}).dropna().reset_index()
    return ind(r)


def swings(r, k=3):
    hs = [i for i in range(k, len(r) - k) if r.h[i] == r.h[i - k:i + k + 1].max()]
    ls = [i for i in range(k, len(r) - k) if r.l[i] == r.l[i - k:i + k + 1].min()]
    return hs, ls


def trend(r):
    x = r.iloc[-1]
    if x.c < x.e21 < x.e50: return 'DOWN'
    if x.c > x.e21 > x.e50: return 'UP'
    return 'MIXED'


def analyse(sym, p5, p1, now_ms):
    m5 = ind(read(p5, now_ms)); h1 = ind(read(p1, now_ms))
    m15 = resample(m5, '15min'); h4 = resample(h1, '4h')
    day = m5.dt.dt.floor('D'); tp = (m5.h + m5.l + m5.c) / 3
    m5['vwap'] = (tp * m5.v).groupby(day).cumsum() / m5.v.groupby(day).cumsum()
    x5, x1 = m5.iloc[-1], h1.iloc[-1]; price = x5.c
    tr = {'4h': trend(h4), '1h': trend(h1), '15m': trend(m15), '5m': trend(m5)}
    big = tr['4h']
    hh = h1.tail(168).reset_index(drop=True)
    hs, ls = swings(hh)
    sw_hi = [hh.h[i] for i in hs]; sw_lo = [hh.l[i] for i in ls]
    out = dict(sym=sym, price=price, trend=tr, rsi1h=round(x1.rsi, 1), rsi15=round(m15.rsi.iloc[-1], 1),
               vwap=round(x5.vwap, 6), atr5_pct=round(x5.atr / price * 100, 3), atr1h_pct=round(x1.atr / price * 100, 3),
               e21_1h=x1.e21, e50_1h=x1.e50, e200_1h=x1.e200,
               taker_buy_12x5m=round(m5.tbv.tail(12).sum() / m5.v.tail(12).sum(), 3),
               vol5_last3=(m5.v.tail(3) / m5.vavg.tail(3)).round(2).tolist(),
               sw_hi=[round(v, 6) for v in sw_hi[-3:]], sw_lo=[round(v, 6) for v in sw_lo[-3:]],
               week_hi=hh.h.max(), week_lo=hh.l.min(), setups=[])

    def room(d, entry, stop):
        R = abs(entry - stop)
        lv = [v for v in (sw_lo if d < 0 else sw_hi) if (v < entry if d < 0 else v > entry)]
        nxt = (max(lv) if d < 0 else min(lv)) if lv else (hh.l.min() if d < 0 else hh.h.max())
        return round(abs(nxt - entry) / R, 2), nxt

    for d in (-1, 1):
        if (d < 0 and big == 'UP') or (d > 0 and big == 'DOWN'):
            continue  # setups (a) and (c) only with the 4h trend; sweeps checked separately
        # (a) pullback into 1h EMA21-50 zone / VWAP, rejected by a 5m CHoCH with volume and taker flow
        zone_top = max(x1.e21, x1.e50); zone_bot = min(x1.e21, x1.e50)
        last12 = m5.tail(12)
        # rejection of the zone: wick into it AND close back on the trade side of it
        touched = ((last12.h.max() >= zone_bot) and (price < zone_bot)) if d < 0 else \
                  ((last12.l.min() <= zone_top) and (price > zone_top))
        s5h, s5l = swings(m5.tail(60).reset_index(drop=True), k=2)
        t5 = m5.tail(60).reset_index(drop=True)
        if d < 0 and s5l:
            lvl = t5.l[s5l[-1]]; choch = (t5.c.iloc[-1] < lvl) and (t5.c.iloc[-2] >= lvl)
        elif d > 0 and s5h:
            lvl = t5.h[s5h[-1]]; choch = (t5.c.iloc[-1] > lvl) and (t5.c.iloc[-2] <= lvl)
        else:
            lvl, choch = np.nan, False
        vol_ok = bool(x5.v >= 1.5 * x5.vavg)
        tb3 = m5.tbv.tail(3).sum() / m5.v.tail(3).sum()
        flow_ok = (tb3 < 0.45) if d < 0 else (tb3 > 0.55)
        conf = {'4h trend': big != 'MIXED', 'touched 1h EMA21-50 zone': bool(touched), '5m CHoCH': bool(choch),
                'volume>=1.5x': vol_ok, 'taker flow': bool(flow_ok),
                'below/above VWAP': bool(price < x5.vwap) if d < 0 else bool(price > x5.vwap)}
        ext = last12.h.max() if d < 0 else last12.l.min()
        stop = ext + d * -1 * 0.1 * x5.atr if False else (ext + 0.1 * x5.atr if d < 0 else ext - 0.1 * x5.atr)
        rr, nxt = room(d, price, stop)
        out['setups'].append(dict(kind='a_pullback', side='SHORT' if d < 0 else 'LONG', confirmations=conf,
                                  n_conf=sum(conf.values()), stop=round(stop, 6), stop_pct=round(abs(stop - price) / price * 100, 3),
                                  room_R_to_next_level=rr, next_level=round(nxt, 6)))
        # (c) 1h BOS in 4h trend direction
        if d < 0 and sw_lo:
            bos = x1.c < sw_lo[-1]; inv = sw_hi[-1] if sw_hi else hh.h.tail(12).max()
        elif d > 0 and sw_hi:
            bos = x1.c > sw_hi[-1]; inv = sw_lo[-1] if sw_lo else hh.l.tail(12).min()
        else:
            bos, inv = False, np.nan
        conf_c = {'4h trend': big != 'MIXED', '1h BOS close': bool(bos), '1h volume>avg': bool(x1.v > x1.vavg),
                  '1h MACD side': bool(x1.macdh < 0) if d < 0 else bool(x1.macdh > 0)}
        stop_c = inv + 0.1 * x1.atr * (1 if d < 0 else -1)
        rr_c, nxt_c = room(d, price, stop_c)
        out['setups'].append(dict(kind='c_bos', side='SHORT' if d < 0 else 'LONG', confirmations=conf_c,
                                  n_conf=sum(conf_c.values()), stop=round(stop_c, 6), stop_pct=round(abs(stop_c - price) / price * 100, 3),
                                  room_R_to_next_level=rr_c, next_level=round(nxt_c, 6)))
    # (b) 1h liquidity sweep of a prior swing, closed back inside, volume spike
    prior = h1.iloc[-50:-2]
    ph, pl = prior.h.max(), prior.l.min()
    for d, cond, ext in ((-1, (x1.h > ph) and (x1.c < ph), x1.h), (1, (x1.l < pl) and (x1.c > pl), x1.l)):
        conf_b = {'sweep+close back': bool(cond), '1h volume>=1.5x': bool(x1.v >= 1.5 * x1.vavg),
                  'RSI extreme': bool(x1.rsi > 65) if d < 0 else bool(x1.rsi < 35)}
        stop_b = ext + 0.1 * x5.atr * (1 if d < 0 else -1)
        rr_b, nxt_b = room(d, price, stop_b)
        out['setups'].append(dict(kind='b_sweep', side='SHORT' if d < 0 else 'LONG', confirmations=conf_b,
                                  n_conf=sum(conf_b.values()), stop=round(stop_b, 6), stop_pct=round(abs(stop_b - price) / price * 100, 3),
                                  room_R_to_next_level=rr_b, next_level=round(nxt_b, 6)))
    for st in out['setups']:   # a stop on the wrong side of price, or outside 0.5%-5%, makes the setup invalid
        wrong = (st['side'] == 'SHORT' and st['stop'] <= price) or (st['side'] == 'LONG' and st['stop'] >= price)
        st['valid'] = (not wrong) and 0.5 <= st['stop_pct'] <= 5.0 and st['room_R_to_next_level'] >= 3
    return out


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if '=' in a and not a.startswith('--')]
    now_ms = int(time.time() * 1000)
    if '--now-ms' in sys.argv: now_ms = int(sys.argv[sys.argv.index('--now-ms') + 1])
    for a in args:
        sym, paths = a.split('=', 1); p5, p1 = paths.split(',')
        r = analyse(sym, p5, p1, now_ms)
        print(f"\n=== {sym} {r['price']} trend {r['trend']} rsi1h {r['rsi1h']} rsi15 {r['rsi15']} VWAP {r['vwap']} "
              f"ATR5m {r['atr5_pct']}% ATR1h {r['atr1h_pct']}% takerBuy {r['taker_buy_12x5m']} vol5 {r['vol5_last3']}")
        print(f"    1h EMA21 {r['e21_1h']:.6g} EMA50 {r['e50_1h']:.6g} EMA200 {r['e200_1h']:.6g} | swing hi {r['sw_hi']} lo {r['sw_lo']} | week {r['week_lo']:.6g}-{r['week_hi']:.6g}")
        for s in r['setups']:
            ok = [k for k, v in s['confirmations'].items() if v]; no = [k for k, v in s['confirmations'].items() if not v]
            print(f"    {s['kind']:10s} {s['side']:5s} conf {s['n_conf']}/{len(s['confirmations'])} stop {s['stop']} ({s['stop_pct']}%) room {s['room_R_to_next_level']}R {'VALID' if s['valid'] else 'invalid'} | yes:{ok} | no:{no}")
