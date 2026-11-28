"""HFT research - one-time evaluation on the strict holdout (2026-11-22 00:00 UTC -> end of data).

Runs ONLY the parameter sets already chosen in-sample (results/selected.csv),
in all three fee modes, with 0 and 1 bar of extra latency. Nothing here feeds
back into parameter choice.

Usage:
    python -m omnis.research.hft.holdout --data-dir /tmp/claude-0/hft_data
"""
import argparse
import json
import os

import pandas as pd

from omnis.research.hft import engine as E
from omnis.research.hft import run_study as R
from omnis.research.hft.analysis import KPI_COLS, breakeven


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', default='/tmp/claude-0/hft_data')
    a = ap.parse_args()
    res = os.path.join(a.data_dir, 'results')
    sel = pd.read_csv(os.path.join(res, 'selected.csv'))
    rows, lists = [], {}
    for tf in ('5m', '1m'):
        R.init_ctx(a.data_dir, tf, 10 ** 15)                 # all data, including the holdout
        end = max(df.t.iloc[-1] for df in R._CTX['dfs'].values()) + R.BAR_MS[tf]
        days = R.days(R.HOLDOUT_START, end)
        for _, s in sel[sel.tf == tf].iterrows():
            for lat in (0, 1):
                tr = R.run_config(s.family, json.loads(s.params), s['mode'], lat)
                ho = tr[tr.entry_t >= R.HOLDOUT_START]
                lists[f'{tf}|{s.family}|{s["mode"]}|lat{lat}'] = ho
                k = E.kpis(ho, days)
                rows.append(dict(tf=tf, family=s.family, mode=s['mode'], lat=lat, days=round(days, 2),
                                 params=s.params, breakeven_rt_cost_pct=breakeven(ho) if len(ho) else None,
                                 **{c: k.get(c) for c in KPI_COLS}))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(res, 'holdout.csv'), index=False)
    pd.to_pickle(lists, os.path.join(res, 'holdout_trades.pkl'))
    pd.set_option('display.width', 250); pd.set_option('display.max_columns', 30)
    print(df[['tf', 'family', 'mode', 'lat', 'days', 'trades', 'trades_day', 'win_pct', 'net', 'gross', 'fees', 'slip',
              'pf', 'exp_usd', 'maxdd_pct', 'breakeven_rt_cost_pct']].to_string(index=False))


if __name__ == '__main__':
    main()
