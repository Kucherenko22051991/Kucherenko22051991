"""HFT research - render the result CSVs as markdown tables for REPORT.md.

Usage:
    python -m omnis.research.hft.report_tables --data-dir /tmp/claude-0/hft_data > /tmp/claude-0/hft_data/results/tables.md
"""
import argparse
import json
import os

import numpy as np
import pandas as pd


def md(df, floatfmt=2):
    def f(v):
        if isinstance(v, (float, np.floating)):
            return '' if pd.isna(v) else f'{v:.{floatfmt}f}'
        return str(v)
    head = '| ' + ' | '.join(df.columns) + ' |'
    sep = '|' + '|'.join('---' for _ in df.columns) + '|'
    rows = ['| ' + ' | '.join(f(v) for v in r) + ' |' for r in df.itertuples(index=False)]
    return '\n'.join([head, sep] + rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', default='/tmp/claude-0/hft_data')
    a = ap.parse_args()
    r = os.path.join(a.data_dir, 'results')
    out = []

    spr = pd.read_csv(os.path.join(r, 'spread_table.csv'))
    dep = pd.read_csv(os.path.join(r, 'depth_table.csv'))
    s = spr.groupby('symbol').agg(spread_bp=('spread_bp', 'mean'), top_bid_usd=('top_bid_usd', 'mean'),
                                  top_ask_usd=('top_ask_usd', 'mean')).reset_index()
    d = dep[['symbol', 'depth_bid_0p1', 'depth_ask_0p1', 'depth_band_complete', 'impact_bp_100', 'impact_bp_500',
             'impact_bp_1600']]
    t = s.merge(d, on='symbol', how='left')
    out.append('### Spread / depth\n' + md(t, 2))

    m = json.load(open(os.path.join(r, 'costs_model.json')))
    rows = [dict(symbol=k, **{f'slip%@{n}': v['slip_pct_by_notional'][n] for n in ('100', '250', '500', '1000', '1600')},
                 funding_h=v['funding']['interval_h'], funding_mean_abs_pct=v['funding']['mean_abs'] * 100,
                 funding_src=v['funding']['source']) for k, v in m.items()]
    out.append('### Slippage model (one side, % of price) and funding\n' + md(pd.DataFrame(rows), 4))

    vol = pd.read_csv(os.path.join(r, 'vol_table.csv'))
    out.append('### Volatility\n' + md(vol[['symbol', 'tf', 'median_atr14_pct', 'p10_atr14_pct', 'p90_atr14_pct',
                                            'std_ret_pct', 'med_abs_15m_pct', 'med_abs_60m_pct']], 3))

    ft = pd.read_csv(os.path.join(r, 'fee_table.csv'))
    cols = ['notional', 'net_target', 'taker/taker fees$', 'taker/taker move% majors', 'taker/taker move% alts',
            'maker->taker fees$', 'maker->taker move% majors', 'maker->taker move% alts', 'maker/maker fees$',
            'maker/maker move% majors']
    out.append('### Fee table\n' + md(ft[cols], 3))

    # realism: required move (maker->taker, majors) / median 5m ATR
    atr5 = vol[vol.tf == '5m'].set_index('symbol').median_atr14_pct
    sd5 = vol[vol.tf == '5m'].set_index('symbol').std_ret_pct
    rows = []
    for _, x in ft.iterrows():
        rr = dict(notional=x.notional, net_target=x.net_target)
        mv_t = x['taker/taker move% majors']; mv_m = x['maker->taker move% majors']
        rr['fee share of move (taker/taker)'] = round(x['taker/taker fees$'] / (mv_t / 100 * x.notional) * 100)
        rr['fee share (maker->taker)'] = round(x['maker->taker fees$'] / (mv_m / 100 * x.notional) * 100)
        for sym in ('BTCUSDT', 'SOLUSDT', 'EVAAUSDT'):
            mv = x['taker/taker move% majors'] if sym != 'EVAAUSDT' else x['taker/taker move% alts']
            rr[f'{sym[:-4]} move/ATR5m'] = round(mv / atr5[sym], 2)
            rr[f'{sym[:-4]} ~5m bars'] = round((mv / sd5[sym]) ** 2, 1)
        rows.append(rr)
    out.append('### Realism (taker/taker; ~bars = (move/sigma_5m)^2, random-walk time scale)\n' + md(pd.DataFrame(rows), 2))

    k = pd.read_csv(os.path.join(r, 'selected_kpis.csv'))
    k = k[(k.gate == 0)]
    cols = ['tf', 'family', 'mode', 'lat', 'seg', 'trades', 'trades_day', 'win_pct', 'avg_win', 'avg_loss', 'net',
            'gross', 'fees', 'fees_pct_gross', 'pf', 'exp_usd', 'exp_R', 'maxdd_usd', 'maxdd_pct', 'hold_min',
            'best_hours', 'breakeven_rt_cost_pct']
    out.append('### Selected (IS-best) configs, portfolio 3 slots - OOS\n' + md(k[k.seg == 'OOS'][cols], 2))
    out.append('### Selected configs - IS (for reference)\n' + md(k[(k.seg == 'IS') & (k.lat == 0)][cols], 2))

    sym = pd.read_csv(os.path.join(r, 'selected_by_symbol_oos.csv'))
    cols = ['tf', 'family', 'symbol', 'trades', 'trades_day', 'win_pct', 'avg_win', 'avg_loss', 'net', 'fees',
            'fees_pct_gross', 'pf', 'exp_usd', 'exp_R', 'maxdd_usd', 'maxdd_pct', 'hold_min', 'best_hours']
    out.append('### Per symbol OOS, maker-entry/taker-exit, lat 0\n' + md(sym[sym['mode'] == 'maker_entry'][cols], 2))
    out.append('### Per symbol OOS, taker/taker, lat 0\n' + md(sym[sym['mode'] == 'taker'][cols], 2))

    ho = pd.read_csv(os.path.join(r, 'holdout.csv'))
    out.append('### Holdout\n' + md(ho[['tf', 'family', 'mode', 'lat', 'trades', 'trades_day', 'win_pct', 'net', 'gross',
                                       'fees', 'pf', 'exp_usd', 'maxdd_pct']], 2))

    mc = pd.read_csv(os.path.join(r, 'montecarlo.csv'))
    out.append('### Monte Carlo\n' + md(mc[['list', 'trades_day', 'mean_net_trade', 'sampling', 'horizon', 'trades',
                                           'p_dd20', 'p_dd50', 'p_below_220', 'fin_p5', 'fin_med', 'fin_p95',
                                           'p_profit']], 1))

    dc = pd.read_csv(os.path.join(r, 'donchian_compare.csv'))
    out.append('### Donchian 4h replay\n' + md(dc, 2))

    fp = pd.read_csv(os.path.join(r, 'frequency_pairs.csv'))
    out.append('### Frequency pairs (maker-entry)\n' + md(fp[fp['mode'] == 'maker_entry'].drop(columns=['mode']), 1))
    print('\n\n'.join(out))


if __name__ == '__main__':
    main()
