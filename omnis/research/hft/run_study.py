"""HFT research - parameter grid with in-sample / out-of-sample split.

The most recent days (from HOLDOUT_START) are cut off BEFORE any simulation
here, so parameter choice can never see them. holdout.py evaluates them once.

Splits (UTC):
  5m: warm-up until 2026-10-06 (1h EMA200 needs ~8 days), IS 10-06 -> 11-05 12:00,
      OOS 11-05 12:00 -> 11-22 00:00, holdout 11-22 00:00 -> 11-26 16:05
  1m: IS 11-11 01:00 -> 11-18 06:00, OOS 11-18 06:00 -> 11-22 00:00, holdout as above
      (1h trend filter for 1m is computed from the longer 5m series)

Account model: equity $330, risk 1% ($3.30) per trade, notional cap $550 per
trade, at most 3 positions at once (3 x $550 = $1650 = 5 x equity).

Usage:
    python -m omnis.research.hft.run_study --data-dir /tmp/claude-0/hft_data
"""
import argparse
import itertools
import json
import os
import time
from multiprocessing import Pool

import pandas as pd

from omnis.research.hft import strategies as S
from omnis.research.hft import engine as E
from omnis.research.hft.costs import slip_pct
from omnis.research.hft.data import load

SYMS = {'5m': ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'PIPPINUSDT', 'EVAAUSDT', 'VVVUSDT'],
        '1m': ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']}
TS = lambda s: int(pd.Timestamp(s, tz='UTC').timestamp() * 1000)
SPLITS = {'5m': dict(start=TS('2026-10-06'), is_end=TS('2026-11-05 12:00'), oos_end=TS('2026-11-22')),
          '1m': dict(start=TS('2026-11-11 01:00'), is_end=TS('2026-11-18 06:00'), oos_end=TS('2026-11-22'))}
HOLDOUT_START = TS('2026-11-22')
BAR_MS = {'1m': 60_000, '5m': 300_000}
MAKER_WAIT = {'1m': 3, '5m': 1}
ACCOUNT = dict(equity=330.0, risk_pct=1.0, cap_usd=550.0, max_conc=3)
_CTX = {}


def days(a, b):
    return (b - a) / 86_400_000


def init_ctx(data_dir, tf, end_ms):
    model = json.load(open(os.path.join(data_dir, 'results', 'costs_model.json')))
    filters = E.load_filters()
    dfs = {}
    for s in SYMS[tf]:
        df = load(data_dir, s, tf)
        src = load(data_dir, s, '5m') if tf == '1m' else None
        df = S.prepare(df, htf_src=src)
        dfs[s] = df[df.t < end_ms].reset_index(drop=True)
    fund = {s: E.funding_cost_fn(model[s]['funding']) for s in SYMS[tf]}
    _CTX.update(model=model, filters=filters, dfs=dfs, fund=fund, tf=tf)


def run_config(family, p, mode, lat, gate=0.0, account=ACCOUNT, syms=None, dfs=None):
    tf = _CTX['tf']; dfs = dfs or _CTX['dfs']
    model = _CTX['model']
    sfn = lambda sym, n: slip_pct(model, sym, n)
    risk = account['equity'] * account['risk_pct'] / 100
    out = []
    for s in (syms or SYMS[tf]):
        df = dfs[s]
        sig = S.signals(df, family, p, tf)
        tr = E.simulate_symbol(df, sig, s, mode=mode, lat=lat, risk_usd=risk, cap_usd=account['cap_usd'],
                               slip_fn=sfn, filt=_CTX['filters'][s], fund_fn=_CTX['fund'][s],
                               maker_wait=MAKER_WAIT[tf], gate=gate, bar_ms=BAR_MS[tf],
                               start_ms=SPLITS[tf]['start'])
        out.append(tr)
    tr = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    return E.portfolio(tr, account['max_conc'])


def segment(tr, tf):
    sp = SPLITS[tf]
    is_ = tr[(tr.entry_t >= sp['start']) & (tr.entry_t < sp['is_end'])]
    oos = tr[(tr.entry_t >= sp['is_end']) & (tr.entry_t < sp['oos_end'])]
    return is_, oos


def _job(args):
    data_dir, tf, family = args
    init_ctx(data_dir, tf, HOLDOUT_START)
    sp = SPLITS[tf]
    d_is, d_oos = days(sp['start'], sp['is_end']), days(sp['is_end'], sp['oos_end'])
    rows = []
    for p, mode, lat, gate in itertools.product(S.GRIDS[family], E.MODES, (0, 1), (0.0, 1.5)):
        if gate and lat:          # fee gate tested at lat 0 only (keeps the grid small)
            continue
        tr = run_config(family, p, mode, lat, gate)
        is_, oos = segment(tr, tf) if len(tr) else (tr, tr)
        r = dict(tf=tf, family=family, params=json.dumps(p), mode=mode, lat=lat, gate=gate)
        r.update({f'IS_{k}': v for k, v in E.kpis(is_, d_is).items()})
        r.update({f'OOS_{k}': v for k, v in E.kpis(oos, d_oos).items()})
        rows.append(r)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', default='/tmp/claude-0/hft_data')
    ap.add_argument('--procs', type=int, default=4)
    a = ap.parse_args()
    t0 = time.time()
    jobs = [(a.data_dir, tf, fam) for tf in ('5m', '1m') for fam in S.GRIDS]
    with Pool(a.procs) as pool:
        res = pool.map(_job, jobs)
    rows = [r for rr in res for r in rr]
    g = pd.DataFrame(rows)
    out = os.path.join(a.data_dir, 'results', 'grid.csv')
    g.to_csv(out, index=False)
    print(f'{len(g)} rows -> {out} in {time.time() - t0:.0f}s')


if __name__ == '__main__':
    main()
