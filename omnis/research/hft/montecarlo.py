"""HFT research - Monte Carlo risk of ruin on backtest trade lists.

Each trade's net result is turned into a return on the $330 equity it was
sized on (risk 1% = $3.30, notional cap $550, 3 slots). Paths resample those
returns with replacement (i.i.d.) and, as a check on loss clustering, in
blocks of 20 consecutive trades. Sizing compounds (risk stays 1% of current
equity). Horizon = measured trades/day x 30 (1 month) or x 90 (3 months).

Reported: P(max drawdown >= 20%), P(max drawdown >= 50%), P(equity ever
< $220, the user's floor), and final-equity percentiles.

Caveat: lists chosen because they were the best out-of-sample are an
optimistic sample (selection bias); real execution adds latency/queue effects.

Usage:
    python -m omnis.research.hft.montecarlo --data-dir /tmp/claude-0/hft_data
"""
import argparse
import os

import numpy as np
import pandas as pd

EQ0 = 330.0
FLOOR = 220.0


def paths(r, n_trades, runs, rng, block=1):
    n = len(r)
    if block <= 1:
        idx = rng.integers(0, n, size=(runs, n_trades))
    else:
        nb = int(np.ceil(n_trades / block))
        starts = rng.integers(0, n, size=(runs, nb))          # circular blocks: every trade equally likely
        idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(runs, -1)[:, :n_trades] % n
    eq = EQ0 * np.cumprod(1 + r[idx], axis=1)
    eq = np.concatenate([np.full((runs, 1), EQ0), eq], axis=1)
    peak = np.maximum.accumulate(eq, axis=1)
    dd = (eq / peak - 1).min(axis=1)
    return eq, dd


def summarize(r, trades_day, runs=10000, seed=7, block=1):
    rng = np.random.default_rng(seed)
    out = {}
    for label, days in (('1m', 30), ('3m', 90)):
        n = max(1, int(round(trades_day * days)))
        eq, dd = paths(r, n, runs, rng, block)
        fin = eq[:, -1]
        out[label] = dict(trades=n, p_dd20=round((dd <= -0.20).mean() * 100, 1),
                          p_dd50=round((dd <= -0.50).mean() * 100, 1),
                          p_below_220=round((eq.min(axis=1) < FLOOR).mean() * 100, 1),
                          fin_p5=round(np.percentile(fin, 5), 0), fin_med=round(np.median(fin), 0),
                          fin_p95=round(np.percentile(fin, 95), 0),
                          p_profit=round((fin > EQ0).mean() * 100, 1))
    return out


LISTS = [  # key in selected_trades.pkl, segment(s), label
    ('1m|mom|maker_both|lat0|gate0.0', ['OOS'], 'best OOS list (1m momentum, maker/maker) - optimistic'),
    ('1m|mom|maker_both|lat0|gate0.0', ['IS', 'OOS'], '1m momentum maker/maker, IS+OOS'),
    ('1m|orb|maker_both|lat0|gate0.0', ['IS', 'OOS'], '1m ORB maker/maker, IS+OOS (IS part is fitted)'),
    ('1m|orb|maker_both|lat0|gate0.0', ['OOS'], '1m ORB maker/maker, OOS only'),
    ('5m|orb|maker_both|lat0|gate0.0', ['OOS'], '5m ORB maker/maker, OOS (least-bad 5m)'),
    ('5m|mom|maker_entry|lat0|gate0.0', ['OOS'], '5m momentum maker-entry, OOS'),
    ('5m|mom|taker|lat1|gate0.0', ['OOS'], '5m momentum taker, 1-bar delay, OOS'),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', default='/tmp/claude-0/hft_data')
    ap.add_argument('--runs', type=int, default=10000)
    a = ap.parse_args()
    res = os.path.join(a.data_dir, 'results')
    tl = pd.read_pickle(os.path.join(res, 'selected_trades.pkl'))
    rows = []
    for key, segs, label in LISTS:
        x = tl[key]
        tr = pd.concat([x[s] for s in segs])
        d = sum(x['d_is'] if s == 'IS' else x['d_oos'] for s in segs)
        r = (tr.sort_values('exit_t').net.values / EQ0)
        tpd = len(tr) / d
        for block in (1, 20):
            m = summarize(r, tpd, a.runs, block=block)
            for h, v in m.items():
                rows.append(dict(list=label, segs='+'.join(segs), trades_hist=len(tr), trades_day=round(tpd, 1),
                                 mean_net_trade=round(tr.net.mean(), 3), sampling='iid' if block == 1 else 'block20',
                                 horizon=h, **v))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(res, 'montecarlo.csv'), index=False)
    pd.set_option('display.width', 250); pd.set_option('display.max_columns', 30); pd.set_option('display.max_colwidth', 60)
    print(df.to_string(index=False))


if __name__ == '__main__':
    main()
