"""HFT research - indicators and short-term signal families (1m / 5m).

Every signal is computed on CLOSED bars only. A signal on bar i means "decided
at the close of bar i"; the engine enters at the open of bar i+1 (or later).
Higher-timeframe (1h) values are taken from fully closed 1h bars only.

Each family returns a dict of numpy arrays aligned with the bars:
    d     direction (+1 long, -1 short, 0 none)
    sd    stop distance in price units (from the entry)
    td    target distance in price units (from the entry)
    hold  max holding time in bars
"""
import numpy as np
import pandas as pd


# ------------------------------------------------------------------ indicators
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(c, n=14):
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def atr(df, n=14):
    pc = df.c.shift()
    tr = pd.concat([df.h - df.l, (df.h - pc).abs(), (df.l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def adx(df, n=14):
    up = df.h.diff()
    dn = -df.l.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(df, n)
    pdi = 100 * pd.Series(pdm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    ndi = 100 * pd.Series(ndm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def htf_closed(df, rule='1h'):
    """Resample to `rule`; return EMA50/EMA200/close of CLOSED bars aligned to df."""
    x = df.set_index('dt')
    r = x.resample(rule, label='left', closed='left').agg({'o': 'first', 'h': 'max', 'l': 'min', 'c': 'last'}).dropna()
    out = pd.DataFrame({'c': r.c, 'e50': ema(r.c, 50), 'e200': ema(r.c, 200)})
    out.index = out.index + pd.Timedelta(rule)          # known only when the bar has closed
    return out.reindex(x.index, method='ffill').reset_index(drop=True)


def prepare(df, htf_src=None):
    """Add all indicators used by the families. df has t,o,h,l,c,v,tbv,dt.
    htf_src: optional longer series (e.g. 5m) of the same symbol used for the
    1h trend filter, so that 1m data does not need 8+ days of warm-up."""
    df = df.copy()
    df['atr'] = atr(df)
    df['rsi'] = rsi(df.c)
    df['adx'] = adx(df)
    df['sma20'] = df.c.rolling(20).mean()
    df['std20'] = df.c.rolling(20).std(ddof=0)
    df['vavg'] = df.v.rolling(20).mean().shift(1)        # average of the 20 bars before
    day = df.dt.dt.floor('D')
    tp = (df.h + df.l + df.c) / 3
    df['vwap'] = (tp * df.v).groupby(day).cumsum() / df.v.groupby(day).cumsum()
    df['bars_today'] = df.groupby(day).cumcount()
    if htf_src is None:
        h1 = htf_closed(df, '1h')
    else:
        src = htf_src[htf_src.t + 300_000 <= df.t.iloc[-1] + 60_000]   # never use bars that end later
        h1s = htf_closed(src, '1h')
        h1s.index = src.dt.values
        h1 = h1s[~h1s.index.duplicated()].reindex(df.dt.values, method='ffill').reset_index(drop=True)
    df['h1_up'] = ((h1.c > h1.e50) & (h1.e50 > h1.e200)).values
    df['h1_dn'] = ((h1.c < h1.e50) & (h1.e50 < h1.e200)).values
    df['minute_of_day'] = df.dt.dt.hour * 60 + df.dt.dt.minute
    return df


def _out(df, L, S, sd, td, hold):
    d = np.where(L, 1, np.where(S, -1, 0)).astype(np.int8)
    ok = np.isfinite(sd) & (sd > 0) & np.isfinite(td) & (td > 0)
    d = np.where(ok, d, 0).astype(np.int8)
    return dict(d=d, sd=np.asarray(sd, float), td=np.asarray(td, float), hold=np.asarray(hold, float))


# ------------------------------------------------------------------ families
def mom(df, p):
    """Momentum breakout: close beyond the N-bar range, volume expansion,
    optional 1h trend filter. Stop sl_atr x ATR, target tp_r x stop."""
    hi = df.h.shift(1).rolling(p['n']).max()
    lo = df.l.shift(1).rolling(p['n']).min()
    vol = df.v > df.vavg * p['vm']
    L = (df.c > hi) & vol
    S = (df.c < lo) & vol
    if p.get('trend', True):
        L &= df.h1_up
        S &= df.h1_dn
    sd = p['sl_atr'] * df.atr
    return _out(df, L.values, S.values, sd.values, (p['tp_r'] * sd).values, np.full(len(df), p['hold']))


def mr_bb(df, p):
    """Bollinger + RSI extreme fade in a range regime (ADX below a cap).
    Target = the 20-bar mean at the signal; stop sl_atr x ATR."""
    lower = df.sma20 - p['k'] * df.std20
    upper = df.sma20 + p['k'] * df.std20
    rng = df.adx < p['adx_max']
    L = (df.c < lower) & (df.rsi < p['rsi']) & rng
    S = (df.c > upper) & (df.rsi > 100 - p['rsi']) & rng
    sd = p['sl_atr'] * df.atr
    td = (df.sma20 - df.c).abs()
    return _out(df, L.values, S.values, sd.values, td.values, np.full(len(df), p['hold']))


def vwap_rev(df, p):
    """Session-VWAP (UTC day) reversion: price stretched z ATRs from VWAP,
    optional reversal candle and ADX cap. Target = VWAP at the signal."""
    dev = (df.c - df.vwap) / df.atr
    early = df.bars_today < p.get('skip_bars', 12)     # VWAP is noisy right after 00:00 UTC
    L = (dev < -p['z']) & ~early
    S = (dev > p['z']) & ~early
    if p.get('rev', False):
        L &= df.c > df.o
        S &= df.c < df.o
    if p.get('adx_max', 100) < 100:
        L &= df.adx < p['adx_max']
        S &= df.adx < p['adx_max']
    sd = p['sl_atr'] * df.atr
    td = (df.vwap - df.c).abs()
    return _out(df, L.values, S.values, sd.values, td.values, np.full(len(df), p['hold']))


SESSIONS_UTC = {'asia': 0, 'europe': 7 * 60, 'us': 14 * 60 + 30}   # NY cash open = 14:30 UTC in Nov


def orb(df, p, bar_min):
    """Opening-range breakout for the Asia/Europe/US session opens.
    Range = first `or_min` minutes; the first close beyond it within `win_min`
    minutes triggers one trade per session. Stop at the opposite side
    ('opp') or the middle ('mid') of the range; target tp_r x stop; exit at
    the end of the window. Optional 1h trend filter."""
    n = len(df)
    d = np.zeros(n, np.int8); sd = np.full(n, np.nan); td = np.full(n, np.nan); hold = np.full(n, np.nan)
    mod = df.minute_of_day.values
    h, l, c = df.h.values, df.l.values, df.c.values
    up, dn = df.h1_up.values, df.h1_dn.values
    or_bars = p['or_min'] // bar_min
    win_bars = p['win_min'] // bar_min
    starts = np.where(np.isin(mod, list(SESSIONS_UTC.values())))[0]
    for s0 in starts:
        e0 = s0 + or_bars                       # first bar after the range
        if e0 >= n:
            continue
        rh, rl = h[s0:e0].max(), l[s0:e0].min()
        if rh <= rl:
            continue
        for i in range(e0, min(e0 + win_bars, n)):
            if c[i] > rh and (not p.get('trend') or up[i]):
                dd = 1
            elif c[i] < rl and (not p.get('trend') or dn[i]):
                dd = -1
            else:
                continue
            ref = rl if dd == 1 else rh
            if p['stop'] == 'mid':
                ref = (rh + rl) / 2
            dist = abs(c[i] - ref)
            d[i] = dd; sd[i] = dist; td[i] = p['tp_r'] * dist
            hold[i] = max(1, e0 + win_bars - i)  # until the end of the window
            break
    return _out(df, d == 1, d == -1, sd, td, hold)


FAMILIES = {'mom': mom, 'mr_bb': mr_bb, 'vwap_rev': vwap_rev, 'orb': orb}

# Small grids. "Looser" settings (lower vm, trend off, lower z, adx off, ...)
# are included on purpose to test whether more trades means more net profit.
GRIDS = {
    'mom': [dict(n=n, vm=vm, sl_atr=sl, tp_r=tp, trend=tr)
            for n in (20, 48) for vm in (0.0, 1.5) for sl in (1.0, 2.0) for tp in (1.5, 3.0) for tr in (True, False)],
    'mr_bb': [dict(k=k, rsi=r, adx_max=a, sl_atr=sl)
              for k in (2.0, 2.5) for r in (25, 35) for a in (20, 100) for sl in (1.5, 3.0)],
    'vwap_rev': [dict(z=z, sl_atr=sl, rev=rv, adx_max=a)
                 for z in (2.0, 3.0, 4.0) for sl in (1.5, 3.0) for rv in (True, False) for a in (25, 100)],
    'orb': [dict(or_min=om, stop=st, tp_r=tp, trend=tr, win_min=180)
            for om in (15, 30) for st in ('opp', 'mid') for tp in (1.0, 2.0) for tr in (True, False)],
}
HOLD_BARS = {'1m': {'mom': 30, 'mr_bb': 20, 'vwap_rev': 30}, '5m': {'mom': 24, 'mr_bb': 12, 'vwap_rev': 24}}


def signals(df, family, p, tf):
    bar_min = 1 if tf == '1m' else 5
    q = dict(p)
    if family in HOLD_BARS[tf]:
        q.setdefault('hold', HOLD_BARS[tf][family])
    if family == 'orb':
        return orb(df, q, bar_min)
    return FAMILIES[family](df, q)
