"""HFT research - the live Donchian 4h rules replayed on the SAME symbols and
window as the HFT tests, with the SAME cost model, for a like-for-like comparison.

Rules (omnis/strategies/donchian_live.py, imported read-only, not modified):
  join-the-trend entry on a closed 4h bar (EMA200 side, 10-bar channel not
  broken), exit on the 10-bar channel at the close, time stop 120 bars,
  stop = SL_ATR x ATR(14) (2 ATR = current Alpha setting; 3 ATR = code default),
  take profit 2R, risk 4% of equity per trade, at most 5 positions,
  total notional <= 5 x equity, no re-entry until the bar after an exit.
Fills: entry at the next bar open (taker + measured slippage), stop and take
profit as stop-market / take-profit-market (taker + slippage, gap = open),
channel/time exits at the next open. Funding as in engine.py.

Usage:
    python -m omnis.research.hft.donchian_compare --data-dir /tmp/claude-0/hft_data
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

from omnis.backtest.strategy_zoo import indicators, donchian_join_entry, donchian_exit
from omnis.research.hft import engine as E
from omnis.research.hft.costs import slip_pct
from omnis.research.hft.data import load

SYMS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'PIPPINUSDT', 'EVAAUSDT', 'VVVUSDT']
BAR = 14_400_000
TS = lambda s: int(pd.Timestamp(s, tz='UTC').timestamp() * 1000)


def run(data_dir, start, sl_atr=2.0, tp_r=2.0, risk_pct=4.0, max_pos=5, eq0=330.0, lev=5):
    model = json.load(open(os.path.join(data_dir, 'results', 'costs_model.json')))
    filt = E.load_filters()
    fund = {s: E.funding_cost_fn(model[s]['funding']) for s in SYMS}
    data = {}
    for s in SYMS:
        df = load(data_dir, s, '4h')
        c = [dict(time=int(r.t), open=r.o, high=r.h, low=r.l, close=r.c) for r in df.itertuples()]
        data[s] = (c, indicators(c), {x['time']: k for k, x in enumerate(c)})
    times = sorted(set(t for s in SYMS for t in data[s][2] if t >= start))
    eq = eq0; pos = {}; trades = []; last_exit = {}
    curve = []
    for t in times:
        # 1) intrabar stop / take profit on bar t for open positions
        for s in list(pos):
            c, d, ix = data[s]; k = ix.get(t)
            if k is None or k < pos[s]['k_in']:
                continue
            p = pos[s]; b = c[k]; dr = p['d']; ex = None
            if k > p['k_in'] and ((dr == 1 and b['open'] <= p['sl']) or (dr == -1 and b['open'] >= p['sl'])):
                ex = (b['open'], 'gap_stop')
            elif (dr == 1 and b['low'] <= p['sl']) or (dr == -1 and b['high'] >= p['sl']):
                ex = (p['sl'], 'stop')
            elif (dr == 1 and b['high'] >= p['tp']) or (dr == -1 and b['low'] <= p['tp']):
                ex = (p['tp'], 'tp')
            elif donchian_exit(d, k, 'LONG' if dr == 1 else 'SHORT') or k - p['k_in'] >= 120:
                if k + 1 < len(c):
                    ex = (c[k + 1]['open'], 'channel/time')        # engine exits right after the close
            if ex:
                raw_x, why = ex
                slip = slip_pct(model, s, p['notional']) / 100
                fx = raw_x * (1 - dr * slip)
                fees = p['qty'] * (p['fe'] + fx) * E.TAKER
                t_out = t + BAR if why != 'channel/time' else t + BAR
                f_usd, nf = fund[s](p['t_in'], t_out, dr, p['notional'])
                net = p['qty'] * dr * (fx - p['fe']) - fees - f_usd
                eq += net
                trades.append(dict(sym=s, entry_t=p['t_in'], exit_t=t_out, dir=dr, notional=p['notional'],
                                   risk_usd=p['risk'], gross=p['qty'] * dr * (raw_x - p['raw_e']), fees=fees,
                                   slip=p['qty'] * (abs(fx - raw_x) + abs(p['fe'] - p['raw_e'])), funding=f_usd,
                                   net=net, exit_type=why))
                last_exit[s] = t; del pos[s]
        # 2) entries decided on the close of bar t, filled at the next open
        for s in SYMS:
            if s in pos or len(pos) >= max_pos:
                continue
            c, d, ix = data[s]; k = ix.get(t)
            if k is None or k + 1 >= len(c) or last_exit.get(s) == t:
                continue
            side = donchian_join_entry(d, k)
            a = d['atr'][k]
            if not side or not a:
                continue
            dr = 1 if side == 'LONG' else -1
            raw_e = c[k + 1]['open']
            dist = sl_atr * a
            step = float(filt[s]['step'])
            qty = np.floor(eq * risk_pct / 100 / dist / step) * step
            used = sum(p['notional'] for p in pos.values())
            if qty * raw_e > lev * eq - used:
                qty = np.floor((lev * eq - used) / raw_e / step) * step
            if qty <= 0 or qty * raw_e < float(filt[s]['min_notional']):
                continue
            notional = qty * raw_e
            slip = slip_pct(model, s, notional) / 100
            fe = raw_e * (1 + dr * slip)
            pos[s] = dict(d=dr, k_in=k + 1, t_in=c[k + 1]['time'], raw_e=raw_e, fe=fe, qty=qty, notional=notional,
                          risk=qty * dist, sl=raw_e - dr * dist, tp=raw_e + dr * tp_r * dist)
        curve.append((t, eq))
    return pd.DataFrame(trades), pd.DataFrame(curve, columns=['t', 'eq']), pos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', default='/tmp/claude-0/hft_data')
    a = ap.parse_args()
    rows = []
    end = None
    for label, start in (('60d 09-27..11-26', TS('2026-09-27')), ('HFT 5m window 10-06..11-26', TS('2026-10-06')),
                         ('HFT holdout 11-22..11-26', TS('2026-11-22'))):
        for sl in (2.0, 3.0):
            tr, curve, open_pos = run(a.data_dir, start, sl_atr=sl)
            days = (curve.t.iloc[-1] + BAR - start) / 86_400_000
            k = E.kpis(tr, days) if len(tr) else dict(trades=0)
            rows.append(dict(window=label, sl_atr=sl, days=round(days, 1), open_at_end=len(open_pos),
                             net_month=round(k.get('net', 0) / days * 30, 2) if len(tr) else 0.0,
                             **{c: k.get(c) for c in ['trades', 'trades_day', 'win_pct', 'net', 'gross', 'fees', 'slip',
                                                      'funding', 'pf', 'exp_usd', 'exp_R', 'maxdd_usd', 'maxdd_pct', 'hold_min']}))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(a.data_dir, 'results', 'donchian_compare.csv'), index=False)
    pd.set_option('display.width', 250); pd.set_option('display.max_columns', 30)
    print(df.to_string(index=False))


if __name__ == '__main__':
    main()
