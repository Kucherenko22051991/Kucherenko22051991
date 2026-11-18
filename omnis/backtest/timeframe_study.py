"""
OMNIS AI — does the live Donchian strategy work on faster candles?

The user asked for faster turnover (30-minute cycles). This replays the
live rules (join the trend, 10-bar channel exit, 3 ATR stop, optional 2R
take profit) on 4h, 1h and 30m candles for the 12 most liquid coins, over
the same calendar window, with fees (0.04%/side) and extra slippage.

Result (2026-11-18), avgR per trade after fees:
  ~116 days: 4h+TP2R 0.175 | 4h 0.142 | 1h 0.051 | 1h+TP2R 0.012
  ~58 days:  4h+TP2R 0.146 | 4h -0.090 | 1h+TP2R 0.032 | 1h 0.008 |
             30m -0.051 .. -0.069 (lost ~90-100R)
Faster candles give 4-10x more trades but stops are 2-3x tighter, so
fees and noise eat the edge: 1h is ~zero after slippage, 30m loses.
4h with the 2R take profit (the live setup) stays the best.

Usage:
    python -m omnis.backtest.timeframe_study
"""
import json, sys, os, statistics

from omnis.backtest.strategy_zoo import indicators, donchian_join_entry, donchian_exit
SYMS = [s + "USDT" for s in "BTC ETH SOL BNB XRP DOGE AVAX LINK NEAR RENDER ICP SUI".split()]
SL_ATR = 3.0; MAXB = 120; WARM = 210
def run(c, d, start_t, end_t, tp_r, fee):
    out, pos = [], None
    for i in range(WARM, len(c) - 1):
        b = c[i]
        if b['time'] > end_t: break
        if pos:
            s, ex = pos['s'], None
            if s == 1 and b['low'] <= pos['sl']: ex = pos['sl']
            elif s == -1 and b['high'] >= pos['sl']: ex = pos['sl']
            elif tp_r and s == 1 and b['high'] >= pos['tp']: ex = pos['tp']
            elif tp_r and s == -1 and b['low'] <= pos['tp']: ex = pos['tp']
            elif donchian_exit(d, i, 'LONG' if s == 1 else 'SHORT') or i - pos['bar'] >= MAXB: ex = b['close']
            if ex is not None:
                out.append(((s * (ex - pos['e']) - (pos['e'] + ex) * fee) / pos['dist'], pos['dist'] / pos['e']))
                pos = None; continue
        if pos is None and b['time'] >= start_t:
            side, a = donchian_join_entry(d, i), d['atr'][i]
            if not side or not a: continue
            e = c[i + 1]['open']; s = 1 if side == 'LONG' else -1; dist = SL_ATR * a
            pos = {'s': s, 'e': e, 'dist': dist, 'bar': i + 1, 'sl': e - s * dist,
                   'tp': e + s * tp_r * dist if tp_r else None}
    return out
data = {tf: {s: (lambda c: (c, indicators(c)))(json.load(open(f'omnis/backtest/data/{s}_{tf}.json'))) for s in SYMS}
        for tf in ('4h', '1h', '30m')}
def window(tf_ref):
    c = data[tf_ref]['BTCUSDT'][0]
    return c[WARM]['time'], c[-1]['time']
print("Same strategy (join trend, 10-bar exit, 3ATR stop), 12 coins. avgR after 0.04%/side fees; '+slip' adds 0.03%/side slippage.\n")
for wname, (st, en) in [('~116 days (1h window)', window('1h')), ('~58 days (30m window)', window('30m'))]:
    days = (en - st) / 86400000
    print(f"== {wname}: {days:.0f} days")
    print(f"{'tf':>4}{'TP':>6}{'trades':>8}{'/day':>6}{'win%':>7}{'stop%':>7}{'avgR':>8}{'+slip':>8}{'sumR':>8}{'R/day':>7}")
    for tf in ('4h', '1h', '30m'):
        c0 = data[tf]['BTCUSDT'][0]
        if c0[WARM]['time'] > st + 86400000:  # not enough history for this window
            continue
        for tp in (None, 2.0):
            tr, tr2 = [], []
            for s in SYMS:
                c, d = data[tf][s]
                tr += run(c, d, st, en, tp, 0.0004)
                tr2 += run(c, d, st, en, tp, 0.0007)
            n = len(tr); rs = [t[0] for t in tr]
            stop = statistics.median(t[1] for t in tr) * 100
            print(f"{tf:>4}{str(tp):>6}{n:>8}{n/days:>6.1f}{sum(r>0 for r in rs)/n*100:>7.1f}{stop:>6.1f}%"
                  f"{sum(rs)/n:>8.3f}{sum(t[0] for t in tr2)/len(tr2):>8.3f}{sum(rs):>8.1f}{sum(rs)/days:>7.2f}")
    print()
