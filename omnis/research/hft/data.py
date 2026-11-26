"""HFT research - data assembly.

Raw Binance USD-M kline pages (as saved by the MCP tool, one JSON array per
file) live in <data_dir>/raw/<SYMBOL>_<interval>/. This module merges the
pages, de-duplicates by open time, drops every bar whose close time is not
yet in the past (the unfinished last bar), checks for gaps and writes one CSV
per symbol/interval to <data_dir>/clean/.

For symbols that have both 1m and 5m pages, 5m bars missing from the 5m pages
(the most recent ~15 days were only downloaded as 1m) are rebuilt exactly from
1m bars (same trades, so OHLCV and taker-buy volume aggregate exactly).

Usage:
    python -m omnis.research.hft.data --data-dir /tmp/claude-0/hft_data --now-ms 1795709700000
"""
import argparse
import glob
import json
import os

import pandas as pd

COLS = ['t', 'o', 'h', 'l', 'c', 'v', 'ct', 'qv', 'n', 'tbv', 'tbqv']
STEP_MS = {'1m': 60_000, '5m': 300_000, '15m': 900_000, '1h': 3_600_000, '4h': 14_400_000}


def read_pages(folder):
    rows = []
    for f in sorted(glob.glob(os.path.join(folder, '*.txt')) + glob.glob(os.path.join(folder, '*.json'))):
        d = json.loads(open(f).read())
        if isinstance(d, dict):
            d = d.get('data', d)
        rows += d
    if not rows:
        return pd.DataFrame(columns=COLS)
    df = pd.DataFrame(rows).iloc[:, :11]
    df.columns = COLS
    df = df.astype(float)
    df['t'] = df.t.astype('int64')
    df['ct'] = df.ct.astype('int64')
    return df.drop_duplicates('t').sort_values('t').reset_index(drop=True)


def resample_1m(df1, rule_ms):
    g = (df1.t // rule_ms) * rule_ms
    out = df1.groupby(g).agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'), c=('c', 'last'),
                             v=('v', 'sum'), qv=('qv', 'sum'), n=('n', 'sum'), tbv=('tbv', 'sum'),
                             tbqv=('tbqv', 'sum'), cnt=('t', 'count')).reset_index(names='t')
    out = out[out.cnt == rule_ms // 60_000].drop(columns='cnt')       # only complete bars
    out['ct'] = out.t + rule_ms - 1
    return out[COLS]


def gap_report(df, step):
    d = df.t.diff().dropna()
    gaps = d[d != step]
    return int(len(gaps)), int((gaps / step - 1).sum()) if len(gaps) else 0


def build(data_dir, now_ms):
    raw = os.path.join(data_dir, 'raw')
    out = os.path.join(data_dir, 'clean')
    os.makedirs(out, exist_ok=True)
    report = []
    folders = sorted(os.listdir(raw))
    for name in folders:
        sym, iv = name.rsplit('_', 1)
        df = read_pages(os.path.join(raw, name))
        df = df[df.ct < now_ms]                                          # drop unfinished bar(s)
        if iv == '5m' and os.path.isdir(os.path.join(raw, f'{sym}_1m')):
            d1 = read_pages(os.path.join(raw, f'{sym}_1m'))
            d1 = d1[d1.ct < now_ms]
            r5 = resample_1m(d1, STEP_MS['5m'])
            r5 = r5[~r5.t.isin(df.t)]
            df = pd.concat([df, r5]).drop_duplicates('t').sort_values('t').reset_index(drop=True)
        ng, nmiss = gap_report(df, STEP_MS[iv])
        df.to_csv(os.path.join(out, f'{sym}_{iv}.csv'), index=False)
        report.append(dict(file=f'{sym}_{iv}', bars=len(df),
                           start=pd.to_datetime(df.t.iloc[0], unit='ms'),
                           end=pd.to_datetime(df.t.iloc[-1], unit='ms'),
                           gaps=ng, missing_bars=nmiss,
                           first_close=df.c.iloc[0], last_close=df.c.iloc[-1]))
    rep = pd.DataFrame(report)
    rep.to_csv(os.path.join(out, '_data_report.csv'), index=False)
    return rep


def load(data_dir, sym, iv):
    df = pd.read_csv(os.path.join(data_dir, 'clean', f'{sym}_{iv}.csv'))
    df['dt'] = pd.to_datetime(df.t, unit='ms')
    return df


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', default='/tmp/claude-0/hft_data')
    ap.add_argument('--now-ms', type=int, required=True,
                    help='download time; bars with close time >= this are dropped')
    a = ap.parse_args()
    pd.set_option('display.width', 200)
    print(build(a.data_dir, a.now_ms).to_string(index=False))
