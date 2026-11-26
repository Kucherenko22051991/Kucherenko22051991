"""HFT research - trading costs.

Inputs (all read-only market/account data saved under <data_dir>):
  book/depth_snap*.json        futures_usds.orderBook snapshots (bids/asks)
  book/bookticker_all_*.json   futures_usds.symbolOrderBookTicker, all symbols
  funding/*                    premiumIndex (all), fundingInfo (all), histories
  clean/<SYM>_<tf>.csv         klines (see data.py)

Outputs (<data_dir>/results/):
  costs_model.json   per-symbol spread, slippage curve, funding model, ATR%
  fee_table.csv      gross move needed for a net target, per notional/fee mode
  vol_table.csv      typical 1m/5m ranges per symbol

Fee facts measured on the account (2026-11-26, userCommissionRate +
futuresAccountConfiguration + getBnbBurnStatus): VIP 0, maker 0.0200%,
taker 0.0500%, BNB fee discount OFF and 0 BNB in the futures wallet.

Slippage model for a market (taker) order of notional N:
    slip%(N) = half spread% + impact%(N)
where impact%(N) = (VWAP of walking the visible book for N - best price)/mid,
averaged over the available snapshots and both sides. This is the cost of an
instant fill against a static book; it ignores latency (price moving while
the order travels) - the backtests handle latency separately (1-bar delay).
"""
import argparse
import glob
import json
import os

import numpy as np
import pandas as pd

MAKER = 0.0002
TAKER = 0.0005
NOTIONALS = [100, 250, 500, 1000, 1600]
TARGETS = [0.20, 0.50, 1.0, 2.0, 5.0]
SYMS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'PIPPINUSDT', 'EVAAUSDT', 'VVVUSDT']
EXTRA_BOOK_SYMS = ['BNBUSDT', 'DOGEUSDT', 'BTWUSDT']


def walk(levels, notional):
    """VWAP price to fill `notional` USDT walking `levels` [[px, qty], ...].
    Returns (vwap, filled_notional)."""
    left, cost, qty = notional, 0.0, 0.0
    for px, q in levels:
        px, q = float(px), float(q)
        take = min(left, px * q)
        cost += take
        qty += take / px
        left -= take
        if left <= 1e-9:
            break
    return (cost / qty if qty else np.nan), notional - left


def depth_stats(data_dir):
    rows = []
    for f in sorted(glob.glob(os.path.join(data_dir, 'book', 'depth_snap*.json'))):
        snap = json.load(open(f))
        for sym, b in snap.items():
            if sym.startswith('_'):
                continue
            bid, ask = float(b['bids'][0][0]), float(b['asks'][0][0])
            mid = (bid + ask) / 2
            lo, hi = mid * 0.999, mid * 1.001
            d_bid = sum(float(p) * float(q) for p, q in b['bids'] if float(p) >= lo)
            d_ask = sum(float(p) * float(q) for p, q in b['asks'] if float(p) <= hi)
            # did the snapshot reach the 0.1% band? (if not, depth is a lower bound)
            full = float(b['bids'][-1][0]) <= lo and float(b['asks'][-1][0]) >= hi
            r = dict(snap=os.path.basename(f), symbol=sym, mid=mid,
                     spread_bp=(ask - bid) / mid * 1e4,
                     depth_bid_0p1=d_bid, depth_ask_0p1=d_ask, depth_band_complete=full)
            for n in NOTIONALS:
                vb, fb = walk(b['asks'], n)          # market BUY walks asks
                vs, fs = walk(b['bids'], n)          # market SELL walks bids
                imp_b = (vb - ask) / mid if fb >= n - 1e-6 else np.nan
                imp_s = (bid - vs) / mid if fs >= n - 1e-6 else np.nan
                r[f'impact_bp_{n}'] = np.nanmean([imp_b, imp_s]) * 1e4
            rows.append(r)
    return pd.DataFrame(rows)


def spread_stats(data_dir, syms):
    rows = []
    for f in sorted(glob.glob(os.path.join(data_dir, 'book', 'bookticker_all_*.json'))):
        m = {x['symbol']: x for x in json.load(open(f))}
        for s in syms:
            x = m.get(s)
            if not x:
                continue
            b, a = float(x['bidPrice']), float(x['askPrice'])
            mid = (a + b) / 2
            rows.append(dict(snap=os.path.basename(f), symbol=s, spread_bp=(a - b) / mid * 1e4,
                             top_bid_usd=float(x['bidQty']) * b, top_ask_usd=float(x['askQty']) * a,
                             tick_bp=None))
    return pd.DataFrame(rows)


def funding_model(data_dir, syms):
    p = {x['symbol']: x for x in json.load(open(os.path.join(data_dir, 'funding', 'premiumIndex_all.json')))}
    info = {x['symbol']: x for x in json.load(open(os.path.join(data_dir, 'funding', 'fundingInfo_all.json')))}
    out = {}
    for s in syms:
        hist_f = os.path.join(data_dir, 'funding', f'{s}_hist.json')
        iv = int(info.get(s, {}).get('fundingIntervalHours', 8))
        m = dict(interval_h=iv, last_rate=float(p[s]['lastFundingRate']))
        if os.path.exists(hist_f):
            h = json.load(open(hist_f))
            m['history'] = [[int(x['fundingTime']), float(x['fundingRate'])] for x in h]
            r = np.array([x[1] for x in m['history']])
            m['mean_abs'] = float(np.mean(np.abs(r)))
            m['source'] = f'history ({len(r)} records)'
        else:
            # no history downloaded: charge 0.01% (90th percentile of |current rate| over all
            # USDT perps on 2026-11-26) per crossing, always against the position - conservative
            m['mean_abs'] = 0.0001
            m['source'] = 'assumed 0.01% per crossing, always paid'
        out[s] = m
    return out


def vol_stats(data_dir, syms):
    rows = []
    for s in syms:
        for tf in ('1m', '5m'):
            f = os.path.join(data_dir, 'clean', f'{s}_{tf}.csv')
            if not os.path.exists(f):
                continue
            df = pd.read_csv(f)
            pc = df.c.shift()
            tr = pd.concat([df.h - df.l, (df.h - pc).abs(), (df.l - pc).abs()], axis=1).max(axis=1) / df.c
            atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
            ret = np.log(df.c).diff()
            bars_15m = 15 if tf == '1m' else 3
            bars_60m = 60 if tf == '1m' else 12
            rows.append(dict(symbol=s, tf=tf, bars=len(df),
                             median_range_pct=float((tr * 100).median()),
                             median_atr14_pct=float((atr * 100).median()),
                             p10_atr14_pct=float((atr * 100).quantile(.1)),
                             p90_atr14_pct=float((atr * 100).quantile(.9)),
                             std_ret_pct=float(ret.std() * 100),
                             med_abs_15m_pct=float((np.log(df.c).diff(bars_15m).abs() * 100).median()),
                             med_abs_60m_pct=float((np.log(df.c).diff(bars_60m).abs() * 100).median())))
    return pd.DataFrame(rows)


def fee_table(slip_major_pct, slip_alt_pct):
    """Gross price move (%) needed so that net PnL = target, for one round trip.
    move = (target + fees + slippage) / notional. Slippage is paid only on
    taker legs (market entry / stop / market exit)."""
    modes = {'taker/taker': (TAKER, TAKER, 2), 'maker->taker': (MAKER, TAKER, 1), 'maker/maker': (MAKER, MAKER, 0)}
    rows = []
    for n in NOTIONALS:
        for t in TARGETS:
            r = dict(notional=n, net_target=t)
            for name, (fe, fx, taker_legs) in modes.items():
                fees = n * (fe + fx)
                r[f'{name} fees$'] = round(fees, 3)
                r[f'{name} move% (no slip)'] = round((t + fees) / n * 100, 4)
                r[f'{name} move% majors'] = round((t + fees + n * slip_major_pct / 100 * taker_legs) / n * 100, 4)
                r[f'{name} move% alts'] = round((t + fees + n * slip_alt_pct / 100 * taker_legs) / n * 100, 4)
            rows.append(r)
    return pd.DataFrame(rows)


def build(data_dir):
    res = os.path.join(data_dir, 'results')
    os.makedirs(res, exist_ok=True)
    dep = depth_stats(data_dir)
    spr = spread_stats(data_dir, SYMS + EXTRA_BOOK_SYMS + ['ICNTUSDT', 'IRYSUSDT', 'PIEVERSEUSDT', 'LINKUSDT', 'AVAXUSDT', 'SUIUSDT'])
    fund = funding_model(data_dir, SYMS)
    vol = vol_stats(data_dir, SYMS)
    model = {}
    for s in SYMS:
        d = dep[dep.symbol == s]
        sp = pd.concat([spr[spr.symbol == s].spread_bp, d.spread_bp])
        half = float(sp.mean()) / 2 / 1e4 * 100                     # % of price
        curve = {n: half + float(d[f'impact_bp_{n}'].mean()) / 100 for n in NOTIONALS}
        v5 = vol[(vol.symbol == s) & (vol.tf == '5m')].iloc[0]
        model[s] = dict(spread_bp_mean=float(sp.mean()), spread_bp_max=float(sp.max()), n_spread_obs=int(len(sp)),
                        slip_pct_by_notional=curve, funding=fund[s],
                        atr5m_pct_median=float(v5.median_atr14_pct))
    json.dump(model, open(os.path.join(res, 'costs_model.json'), 'w'), indent=1)
    maj = np.mean([model[s]['slip_pct_by_notional'][1000] for s in ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT']])
    alt = np.mean([model[s]['slip_pct_by_notional'][1000] for s in ['PIPPINUSDT', 'EVAAUSDT', 'VVVUSDT']])
    ft = fee_table(maj, alt)
    ft.to_csv(os.path.join(res, 'fee_table.csv'), index=False)
    vol.to_csv(os.path.join(res, 'vol_table.csv'), index=False)
    dep.to_csv(os.path.join(res, 'depth_table.csv'), index=False)
    spr.to_csv(os.path.join(res, 'spread_table.csv'), index=False)
    return model, ft, vol, dep, spr, maj, alt


def slip_pct(model, sym, notional):
    """Interpolated one-side taker slippage (% of price) for a notional."""
    c = model[sym]['slip_pct_by_notional']
    xs = sorted(int(k) for k in c)
    ys = [c[str(x)] if str(x) in c else c[x] for x in xs]
    return float(np.interp(notional, xs, ys))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', default='/tmp/claude-0/hft_data')
    a = ap.parse_args()
    pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40)
    model, ft, vol, dep, spr, maj, alt = build(a.data_dir)
    print('== spreads (bookTicker snapshots)\n', spr.groupby('symbol')[['spread_bp', 'top_bid_usd', 'top_ask_usd']].agg(['mean', 'max', 'count']).round(2).to_string())
    print('\n== depth within 0.1% of mid and impact (orderBook snapshots)\n', dep.drop(columns=['mid']).round(3).to_string(index=False))
    print('\n== slippage model, one side, % of price')
    for s, m in model.items():
        print(f"{s:12s}", {k: round(v, 4) for k, v in m['slip_pct_by_notional'].items()}, 'funding', m['funding']['interval_h'], 'h', m['funding']['source'], 'mean|r| %.4f%%' % (m['funding']['mean_abs'] * 100))
    print(f'\nslip used in fee table @ $1000: majors {maj:.4f}%  alts {alt:.4f}%')
    print('\n== volatility\n', vol.round(4).to_string(index=False))
    print('\n== fee table\n', ft.to_string(index=False))
