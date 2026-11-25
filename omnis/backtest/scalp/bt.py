"""Scalping strategy lab on Binance USD-M 5m candles.

No look-ahead: signals are computed on a closed 5m bar, entry happens at the
NEXT bar's open. Stops/targets are checked intrabar with high/low; when one bar
touches both stop and target, the stop is assumed first (pessimistic).
Higher timeframes (15m, 1h) are resampled from 5m and only their CLOSED bars
are used (shifted by one higher-TF bar before being mapped back to 5m).

Costs: taker fee 0.05% per side + slippage 0.02% per side = 0.14% of notional
round trip, converted to R per trade (cost_R = 0.14% / stop distance %).
"""
import sys, itertools
import numpy as np, pandas as pd

FEE = 0.0005; SLIP = 0.0002; RT = 2 * (FEE + SLIP)
D = sys.argv[1] if len(sys.argv) > 1 else '.'
SYMS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def rsi(c, n=14):
    d = c.diff(); up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def atr(df, n=14):
    pc = df.c.shift()
    tr = pd.concat([df.h - df.l, (df.h - pc).abs(), (df.l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/n, adjust=False).mean()


def htf(df, rule):
    """Resample to a higher TF; return closed-bar values aligned to 5m index."""
    x = df.set_index('dt')
    r = x.resample(rule, label='left', closed='left').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last', 'v': 'sum'}).dropna()
    r['e9'] = ema(r.c, 9); r['e21'] = ema(r.c, 21); r['e50'] = ema(r.c, 50); r['e200'] = ema(r.c, 200)
    m = ema(r.c, 12) - ema(r.c, 26); r['macdh'] = m - ema(m, 9)
    r = r.shift(1)                                    # only fully closed HTF bars
    r.index = r.index + pd.Timedelta(rule)             # value known at the end of the bar
    return r.reindex(x.index, method='ffill').reset_index(drop=True)


def load(sym):
    df = pd.read_csv(f'{D}/{sym}_5m.csv')
    df['dt'] = pd.to_datetime(df.t, unit='ms')
    df['e9'] = ema(df.c, 9); df['e21'] = ema(df.c, 21); df['e50'] = ema(df.c, 50); df['e200'] = ema(df.c, 200)
    df['rsi'] = rsi(df.c); df['atr'] = atr(df)
    m = ema(df.c, 12) - ema(df.c, 26); df['macdh'] = m - ema(m, 9)
    day = df.dt.dt.floor('D'); tp = (df.h + df.l + df.c) / 3
    df['vwap'] = (tp * df.v).groupby(day).cumsum() / df.v.groupby(day).cumsum()
    df['vavg'] = df.v.rolling(20).mean()
    df['buyr'] = df.tbv / df.v                         # taker-buy share (order-flow proxy)
    h1 = htf(df, '1h'); m15 = htf(df, '15min')
    for k in ['c', 'e50', 'e200', 'macdh']: df['h1_' + k] = h1[k]
    for k in ['c', 'e21', 'e50']: df['m15_' + k] = m15[k]
    return df


# ---------------------------------------------------------------- signals
def sig_pullback(df, p):
    """Trend pullback: 1h trend + 15m trend, 5m dip to EMA21, RSI reset, reclaim."""
    up1h = (df.h1_c > df.h1_e50) & (df.h1_e50 > df.h1_e200)
    dn1h = (df.h1_c < df.h1_e50) & (df.h1_e50 < df.h1_e200)
    up15 = df.m15_e21 > df.m15_e50; dn15 = df.m15_e21 < df.m15_e50
    touch_l = (df.l <= df.e21).rolling(p['look']).max().astype(bool)
    touch_s = (df.h >= df.e21).rolling(p['look']).max().astype(bool)
    rsi_lo = (df.rsi.rolling(p['look']).min() < p['rsi_dip']); rsi_hi = (df.rsi.rolling(p['look']).max() > 100 - p['rsi_dip'])
    vol = df.v > df.vavg * p['vmult']
    L = up1h & up15 & touch_l & rsi_lo & (df.rsi > 50) & (df.c > df.e9) & (df.c > df.vwap) & (df.c > df.o) & vol
    S = dn1h & dn15 & touch_s & rsi_hi & (df.rsi < 50) & (df.c < df.e9) & (df.c < df.vwap) & (df.c < df.o) & vol
    swl = df.l.rolling(p['swing']).min(); swh = df.h.rolling(p['swing']).max()
    sl_l = np.minimum(swl - 0.1 * df.atr, df.c - p['minatr'] * df.atr)
    sl_s = np.maximum(swh + 0.1 * df.atr, df.c + p['minatr'] * df.atr)
    return L, S, sl_l, sl_s


def sig_sweep(df, p):
    """Liquidity sweep + reclaim of a prior swing, with volume spike."""
    prior_lo = df.l.shift(2).rolling(p['n']).min(); prior_hi = df.h.shift(2).rolling(p['n']).max()
    vol = df.v > df.vavg * p['vmult']
    L = (df.l < prior_lo) & (df.c > prior_lo) & vol & (df.rsi.shift(1).rolling(3).min() < p['rsi'])
    S = (df.h > prior_hi) & (df.c < prior_hi) & vol & (df.rsi.shift(1).rolling(3).max() > 100 - p['rsi'])
    if p.get('trend'):
        L &= df.h1_c > df.h1_e200; S &= df.h1_c < df.h1_e200
    sl_l = df.l - 0.15 * df.atr; sl_s = df.h + 0.15 * df.atr
    return L, S, sl_l, sl_s


def sig_breakout(df, p):
    """BOS momentum: close beyond N-bar range, volume expansion, trend aligned."""
    hi = df.h.shift(1).rolling(p['n']).max(); lo = df.l.shift(1).rolling(p['n']).min()
    vol = df.v > df.vavg * p['vmult']
    L = (df.c > hi) & vol & (df.h1_c > df.h1_e50) & (df.h1_e50 > df.h1_e200) & (df.macdh > 0) & (df.buyr > 0.55)
    S = (df.c < lo) & vol & (df.h1_c < df.h1_e50) & (df.h1_e50 < df.h1_e200) & (df.macdh < 0) & (df.buyr < 0.45)
    sl_l = df.c - p['slatr'] * df.atr; sl_s = df.c + p['slatr'] * df.atr
    return L, S, sl_l, sl_s


STRATS = {'pullback': sig_pullback, 'sweep': sig_sweep, 'breakout': sig_breakout}


# ---------------------------------------------------------------- simulator
def simulate(df, L, S, sl_l, sl_s, exit_model, max_bars=48, min_sl_pct=0.0):
    o, h, l, c = df.o.values, df.h.values, df.l.values, df.c.values
    Lv, Sv = L.fillna(False).values, S.fillna(False).values
    sll, sls = sl_l.values, sl_s.values
    trades = []; i = 260; n = len(df)
    while i < n - 2:
        d = 1 if Lv[i] else (-1 if Sv[i] else 0)
        if d == 0: i += 1; continue
        e = o[i + 1]; sl = sll[i] if d == 1 else sls[i]
        risk = (e - sl) * d
        if not np.isfinite(risk) or risk <= 0 or risk / e < min_sl_pct: i += 1; continue
        cost_R = RT * e / risk
        if exit_model == 'scaled': tps = [(1, 1/3), (2, 1/3), (3, 1/3)]
        else: tps = [(float(exit_model[1:]), 1.0)]
        left = 1.0; R = 0.0; stop = sl; k = 0; j = i + 1; be = False
        while j < n and j <= i + max_bars:
            hit_stop = (l[j] <= stop) if d == 1 else (h[j] >= stop)
            if hit_stop:
                R += left * ((stop - e) * d / risk); left = 0; break
            while k < len(tps):
                tgt = e + d * tps[k][0] * risk
                if (h[j] >= tgt) if d == 1 else (l[j] <= tgt):
                    R += tps[k][1] * tps[k][0]; left -= tps[k][1]; k += 1
                    if exit_model == 'scaled' and not be: stop = e; be = True
                else: break
            if left <= 1e-9: break
            j += 1
        if left > 1e-9:                                   # time stop at close
            j = min(j, n - 1); R += left * ((c[j] - e) * d / risk)
        trades.append(dict(i=i, t=df.dt.iloc[i + 1], dir=d, R_gross=R, cost_R=cost_R, R=R - cost_R,
                           sl_pct=risk / e * 100, bars=j - i))
        i = j + 1                                          # one position per symbol at a time
    return pd.DataFrame(trades)


def stats(t, risk_pct=0.5):
    if len(t) == 0: return dict(n=0)
    w = t[t.R > 0]; ls = t[t.R <= 0]
    pf = w.R.sum() / -ls.R.sum() if len(ls) and ls.R.sum() < 0 else np.inf
    eq = (t.sort_values('t').R * risk_pct).cumsum()
    dd = (eq - eq.cummax()).min()
    return dict(n=len(t), win=round(len(w) / len(t) * 100, 1), avgW=round(w.R.mean(), 2) if len(w) else 0,
                avgL=round(ls.R.mean(), 2) if len(ls) else 0, PF=round(pf, 2), expR=round(t.R.mean(), 3),
                costR=round(t.cost_R.mean(), 2), ret_pct=round(t.R.sum() * risk_pct, 2), maxDD_pct=round(dd, 2),
                sl_pct=round(t.sl_pct.median(), 2))


GRID = {
    'pullback': dict(look=[4, 6], rsi_dip=[40, 45], vmult=[0.8, 1.2], swing=[6, 12], minatr=[1.0, 1.5]),
    'sweep': dict(n=[24, 48], vmult=[1.5, 2.0], rsi=[35, 45], trend=[False, True]),
    'breakout': dict(n=[24, 48], vmult=[1.5, 2.5], slatr=[1.5, 2.5]),
}

if __name__ == '__main__':
    data = {s: load(s) for s in SYMS}
    cut = data['BTCUSDT'].dt.iloc[int(len(data['BTCUSDT']) * 0.6)]
    print('in-sample until', cut, '| out-of-sample after; costs RT', RT * 100, '% notional')
    rows = []
    for name, fn in STRATS.items():
        keys = list(GRID[name]);
        for vals in itertools.product(*[GRID[name][k] for k in keys]):
            p = dict(zip(keys, vals))
            for ex in ['scaled', 'f2', 'f1.5']:
                allt = []
                for s, df in data.items():
                    L, S, a, b = fn(df, p)
                    t = simulate(df, L, S, a, b, ex); t['sym'] = s; allt.append(t)
                t = pd.concat(allt)
                if len(t) == 0: continue
                ins, oos = t[t.t < cut], t[t.t >= cut]
                rows.append(dict(strat=name, exit=ex, **p, **{'IS_' + k: v for k, v in stats(ins).items()},
                                 **{'OOS_' + k: v for k, v in stats(oos).items()}))
    R = pd.DataFrame(rows)
    R.to_csv(f'{D}/grid.csv', index=False)
    pd.set_option('display.width', 250); pd.set_option('display.max_columns', 40)
    for name in STRATS:
        x = R[R.strat == name]
        print(f'\n=== {name}: {len(x)} variants | IS expR>0: {(x.IS_expR > 0).sum()} | OOS expR>0: {(x.OOS_expR > 0).sum()} | both>0: {((x.IS_expR > 0) & (x.OOS_expR > 0)).sum()}')
        cols = [c for c in x.columns if c not in ('strat',)]
        print(x.sort_values('IS_expR', ascending=False).head(5)[[c for c in cols if not c.endswith(('avgW', 'avgL', 'sl_pct'))]].to_string(index=False))
