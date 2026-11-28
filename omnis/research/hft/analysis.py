"""HFT research - selection and KPI tables from the IS/OOS grid.

1. For every timeframe x family x fee mode, pick the parameter set with the
   best IN-SAMPLE net $ (lat 0, no fee gate, >= 20 IS trades). Report its
   OUT-OF-SAMPLE result (portfolio, 3 slots) with lat 0 and lat 1.
2. Per strategy x symbol OOS KPIs (each symbol traded alone) for the selected sets.
3. Frequency test: do looser filters (more trades) raise NET $ or only fees?
4. Break-even cost: the round-trip cost (% of notional) at which each
   selected strategy would have made zero in-sample / out-of-sample.
Trade lists of the selected sets are saved for montecarlo.py.

Usage:
    python -m omnis.research.hft.analysis --data-dir /tmp/claude-0/hft_data
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

from omnis.research.hft import engine as E
from omnis.research.hft import run_study as R

KPI_COLS = ['trades', 'trades_day', 'win_pct', 'avg_win', 'avg_loss', 'net', 'gross', 'fees', 'slip', 'funding',
            'fees_pct_gross', 'pf', 'exp_usd', 'exp_R', 'maxdd_usd', 'maxdd_pct', 'hold_min', 'best_hours']


def select(g):
    base = g[(g.lat == 0) & (g.gate == 0) & (g.IS_trades >= 20)]
    idx = base.groupby(['tf', 'family', 'mode']).IS_net.idxmax()
    return base.loc[idx].reset_index(drop=True)


def frequency_test(g):
    """Configs ranked by trades/day within tf x family x mode (lat 0, no gate)."""
    rows = []
    b = g[(g.lat == 0) & (g.gate == 0)].copy()
    for (tf, fam, mode), x in b.groupby(['tf', 'family', 'mode']):
        for seg in ('IS', 'OOS'):
            x = x.sort_values(f'{seg}_trades_day')
            q = pd.qcut(x[f'{seg}_trades_day'].rank(method='first'), 3, labels=['low', 'mid', 'high'])
            for lab, y in x.groupby(q, observed=True):
                rows.append(dict(tf=tf, family=fam, mode=mode, seg=seg, freq_tercile=lab, configs=len(y),
                                 trades_day=round(y[f'{seg}_trades_day'].mean(), 1),
                                 gross=round(y[f'{seg}_gross'].mean(), 1), fees=round(y[f'{seg}_fees'].mean(), 1),
                                 slip=round(y[f'{seg}_slip'].mean(), 1), net=round(y[f'{seg}_net'].mean(), 1)))
            c = np.corrcoef(x[f'{seg}_trades_day'], x[f'{seg}_net'])[0, 1] if len(x) > 2 else np.nan
            rows.append(dict(tf=tf, family=fam, mode=mode, seg=seg, freq_tercile='corr(trades/day, net)',
                             configs=len(x), net=round(c, 2)))
    return pd.DataFrame(rows)


def paired_looseness(g):
    """Same parameters except one filter switched off/looser: effect on trades and net (OOS+IS)."""
    b = g[(g.lat == 0) & (g.gate == 0)].copy()
    b['p'] = b.params.apply(json.loads)
    pairs = [('mom', 'trend', True, False), ('mom', 'vm', 1.5, 0.0), ('mr_bb', 'adx_max', 20, 100),
             ('vwap_rev', 'adx_max', 25, 100), ('vwap_rev', 'rev', True, False), ('vwap_rev', 'z', 4.0, 2.0),
             ('orb', 'trend', True, False)]
    rows = []
    for fam, key, strict, loose in pairs:
        x = b[b.family == fam]
        for (tf, mode), y in x.groupby(['tf', 'mode']):
            a = y[y.p.apply(lambda d: d.get(key) == strict)]
            c = y[y.p.apply(lambda d: d.get(key) == loose)]
            if a.empty or c.empty:
                continue
            for seg in ('IS', 'OOS'):
                rows.append(dict(tf=tf, family=fam, mode=mode, switch=f'{key}: {strict} -> {loose}', seg=seg,
                                 strict_trades_day=round(a[f'{seg}_trades_day'].mean(), 1),
                                 loose_trades_day=round(c[f'{seg}_trades_day'].mean(), 1),
                                 strict_net=round(a[f'{seg}_net'].mean(), 1), loose_net=round(c[f'{seg}_net'].mean(), 1),
                                 strict_fees=round(a[f'{seg}_fees'].mean(), 1), loose_fees=round(c[f'{seg}_fees'].mean(), 1),
                                 strict_gross=round(a[f'{seg}_gross'].mean(), 1), loose_gross=round(c[f'{seg}_gross'].mean(), 1)))
    return pd.DataFrame(rows)


def breakeven(tr):
    """Round-trip cost (% of notional) that would make the trade list break even."""
    turn = tr.notional.sum()
    return round(tr.gross.sum() / turn * 100, 4) if turn else np.nan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', default='/tmp/claude-0/hft_data')
    a = ap.parse_args()
    res = os.path.join(a.data_dir, 'results')
    g = pd.read_csv(os.path.join(res, 'grid.csv'))
    sel = select(g)
    agg_rows, sym_rows, trade_lists = [], [], {}
    for tf in ('5m', '1m'):
        R.init_ctx(a.data_dir, tf, R.HOLDOUT_START)
        sp = R.SPLITS[tf]
        d_is, d_oos = R.days(sp['start'], sp['is_end']), R.days(sp['is_end'], sp['oos_end'])
        for _, s in sel[sel.tf == tf].iterrows():
            p = json.loads(s.params)
            for lat in (0, 1):
                for gate in ((0.0, 1.5) if lat == 0 else (0.0,)):
                    tr = R.run_config(s.family, p, s['mode'], lat, gate)
                    is_, oos = R.segment(tr, tf)
                    key = f'{tf}|{s.family}|{s["mode"]}|lat{lat}|gate{gate}'
                    trade_lists[key] = dict(params=p, IS=is_, OOS=oos, d_is=d_is, d_oos=d_oos)
                    for seg, x, d in (('IS', is_, d_is), ('OOS', oos, d_oos)):
                        k = E.kpis(x, d)
                        agg_rows.append(dict(tf=tf, family=s.family, mode=s['mode'], lat=lat, gate=gate, seg=seg,
                                             params=s.params, breakeven_rt_cost_pct=breakeven(x) if len(x) else None,
                                             **{c: k.get(c) for c in KPI_COLS}))
            # per symbol, symbol traded alone, OOS, lat 0, no gate
            for sym in R.SYMS[tf]:
                tr = R.run_config(s.family, p, s['mode'], 0, 0.0, syms=[sym])
                _, oos = R.segment(tr, tf)
                k = E.kpis(oos, d_oos)
                sym_rows.append(dict(tf=tf, family=s.family, mode=s['mode'], symbol=sym,
                                     **{c: k.get(c) for c in KPI_COLS}))
    agg = pd.DataFrame(agg_rows); sym = pd.DataFrame(sym_rows)
    sel.to_csv(os.path.join(res, 'selected.csv'), index=False)
    agg.to_csv(os.path.join(res, 'selected_kpis.csv'), index=False)
    sym.to_csv(os.path.join(res, 'selected_by_symbol_oos.csv'), index=False)
    ft = frequency_test(g); ft.to_csv(os.path.join(res, 'frequency_test.csv'), index=False)
    pl = paired_looseness(g); pl.to_csv(os.path.join(res, 'frequency_pairs.csv'), index=False)
    pd.to_pickle(trade_lists, os.path.join(res, 'selected_trades.pkl'))
    pd.set_option('display.width', 260); pd.set_option('display.max_columns', 40); pd.set_option('display.max_colwidth', 80)
    print(agg[agg.gate == 0][['tf', 'family', 'mode', 'lat', 'seg', 'trades', 'trades_day', 'win_pct', 'net', 'gross', 'fees',
                              'slip', 'fees_pct_gross', 'pf', 'exp_usd', 'exp_R', 'maxdd_pct', 'hold_min',
                              'breakeven_rt_cost_pct']].to_string(index=False))
    print('\nselected params:\n', sel[['tf', 'family', 'mode', 'params']].to_string(index=False))


if __name__ == '__main__':
    main()
