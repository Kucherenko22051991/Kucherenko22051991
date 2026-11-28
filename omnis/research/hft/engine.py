"""HFT research - bar-based backtest engine with realistic costs.

Mechanics (all pessimistic where the bar data is ambiguous):
  * Signal at the close of bar i. Entry no earlier than bar i+1+lat
    (lat = 0, or 1 to model one extra bar of API/scheduling delay).
  * Taker entry: market at the open of bar i+1+lat, paying taker fee and the
    symbol's measured slippage (half spread + book impact for the size).
  * Maker entry (post-only limit at the signal close): filled ONLY if a later
    bar trades strictly through the limit within `maker_wait` bars; otherwise
    the signal is dropped. Fills therefore happen only when price moves
    against us first (adverse selection is in the test, not assumed away).
  * Stop = entry -/+ stop distance; target = entry +/- target distance.
    If a bar touches both, the stop counts first. If a bar OPENS beyond the
    stop, the fill is that open (gap). On a maker-entry fill bar the target
    is not allowed (we cannot know the order of the moves).
  * Exits: stop = stop-market (taker + slippage). Target = take-profit market
    (taker + slippage) or, in 'maker_both' mode, a resting limit (maker fee,
    filled only if price trades strictly through it). Time exit = market at
    the bar close (lat 0) or the next open (lat 1).
  * Funding: charged when a position is open across a funding time of the
    symbol (8h symbols 00/08/16 UTC, 4h symbols every 4h). Uses the real
    funding history where downloaded, else a conservative constant cost.
  * Size: quantity so that the stop loses `risk_usd`, capped at `cap_usd`
    notional per trade, floored to the exchange step; below min notional the
    trade is skipped. Portfolio: at most `max_conc` positions at once
    (per-trade cap x max_conc <= 5 x equity).
"""
import json
import math
import os

import numpy as np
import pandas as pd

MAKER = 0.0002
TAKER = 0.0005
FILTERS = os.path.join(os.path.dirname(__file__), '..', '..', 'backtest', 'data', 'symbol_filters.json')

MODES = {                     # entry fee, target-exit fee, entry is maker?, target is maker?
    'taker': (TAKER, TAKER, False, False),
    'maker_entry': (MAKER, TAKER, True, False),
    'maker_both': (MAKER, MAKER, True, True),
}


def load_filters():
    return json.load(open(FILTERS))


def funding_cost_fn(fmodel):
    """Return f(entry_ms, exit_ms, direction, notional) -> funding paid (USD, >0 = cost)."""
    iv = fmodel['interval_h'] * 3_600_000
    hist = fmodel.get('history')
    if hist:
        ts = np.array([h[0] // 1000 * 1000 for h in hist], dtype=np.int64)
        rs = np.array([h[1] for h in hist])
    const = fmodel.get('mean_abs', 0.0001)

    def f(t0, t1, d, notional):
        first = (t0 // iv + 1) * iv                  # first funding time strictly after entry
        if first > t1:
            return 0.0, 0
        times = np.arange(first, t1 + 1, iv)
        if hist:
            idx = np.searchsorted(ts, times, side='right') - 1
            r = np.where(idx >= 0, rs[np.clip(idx, 0, None)], const)
            return float(np.sum(r) * d * notional), len(times)
        return float(const * notional * len(times)), len(times)
    return f


def simulate_symbol(df, sig, sym, mode='taker', lat=0, risk_usd=3.3, cap_usd=550.0,
                    slip_fn=None, filt=None, fund_fn=None, maker_wait=1, gate=0.0, bar_ms=300_000,
                    start_ms=None):
    o, h, l, c, t = df.o.values, df.h.values, df.l.values, df.c.values, df.t.values
    d_arr, sd_arr, td_arr, hold_arr = sig['d'], sig['sd'], sig['td'], sig['hold']
    fee_e, fee_tp, mk_e, mk_tp = MODES[mode]
    step = float(filt['step']); min_not = float(filt['min_notional'])
    n = len(df)
    trades = []
    cand = np.flatnonzero(d_arr != 0)
    if start_ms is not None:
        cand = cand[t[cand] >= start_ms]
    k = 0
    next_free = 0                                   # first bar index where a new signal may be taken
    while k < len(cand):
        i = cand[k]; k += 1
        if i < next_free:
            continue
        d = int(d_arr[i]); sd = sd_arr[i]; td = td_arr[i]; hold = int(hold_arr[i])
        px = c[i]
        notional = min(risk_usd / (sd / px), cap_usd)
        qty = math.floor(notional / px / step + 1e-9) * step
        if qty * px < min_not or qty <= 0:
            continue
        notional = qty * px
        slip = slip_fn(sym, notional) / 100.0        # fraction of price, one side
        # fee check: expected target move must beat the round-trip cost by `gate` x
        rt = fee_e + fee_tp + (0 if mk_e else slip) + (0 if mk_tp else slip)
        if gate > 0 and td / px < gate * rt:
            continue
        e0 = i + 1 + lat
        if e0 >= n:
            break
        if mk_e:
            lim = px
            fill = -1
            for j in range(e0, min(e0 + maker_wait, n)):
                if (d == 1 and l[j] < lim) or (d == -1 and h[j] > lim):
                    fill = j
                    break
            if fill < 0:
                next_free = min(e0 + maker_wait, n)
                continue
            raw_e = lim; fe = lim
        else:
            fill = e0
            raw_e = o[e0]; fe = raw_e * (1 + d * slip)
        stop = raw_e - d * sd
        tgt = raw_e + d * td
        j = fill
        ex_type = None
        while j < n:
            if j > fill:                              # gap through the stop / target at the open
                if (d == 1 and o[j] <= stop) or (d == -1 and o[j] >= stop):
                    raw_x = o[j]; ex_type = 'gap_stop'; break
                if (d == 1 and o[j] >= tgt) or (d == -1 and o[j] <= tgt):
                    raw_x = tgt if mk_tp else o[j]; ex_type = 'target'; break
            stop_hit = (l[j] <= stop) if d == 1 else (h[j] >= stop)
            if stop_hit:
                raw_x = stop; ex_type = 'stop'; break
            if not (mk_e and j == fill):
                if mk_tp:
                    tgt_hit = (h[j] > tgt) if d == 1 else (l[j] < tgt)
                else:
                    tgt_hit = (h[j] >= tgt) if d == 1 else (l[j] <= tgt)
                if tgt_hit:
                    raw_x = tgt; ex_type = 'target'; break
            if j - fill + 1 >= hold:
                if lat and j + 1 < n:
                    raw_x = o[j + 1]; j = j + 1
                else:
                    raw_x = c[j]
                ex_type = 'time'; break
            j += 1
        if ex_type is None:                           # ran out of data: close at last close
            j = n - 1; raw_x = c[j]; ex_type = 'eod'
        x_maker = (ex_type == 'target' and mk_tp)
        fx = raw_x if x_maker else raw_x * (1 - d * slip)
        fee_x = MAKER if x_maker else TAKER
        gross = qty * d * (raw_x - raw_e)
        fees = qty * fe * fee_e + qty * fx * fee_x
        slip_usd = qty * (abs(fe - raw_e) + abs(fx - raw_x))
        t_in = int(t[fill]); t_out = int(t[j]) + bar_ms
        fund, n_fund = fund_fn(t_in, int(t[j]), d, notional) if fund_fn else (0.0, 0)
        net = qty * d * (fx - fe) - fees - fund
        trades.append((sym, t_in, t_out, d, raw_e, raw_x, qty, notional, qty * sd, gross, fees, slip_usd,
                       fund, n_fund, net, ex_type, int(t[i])))
        next_free = j                                 # one position per symbol; next signal >= exit bar
    cols = ['sym', 'entry_t', 'exit_t', 'dir', 'entry', 'exit', 'qty', 'notional', 'risk_usd', 'gross',
            'fees', 'slip', 'funding', 'n_funding', 'net', 'exit_type', 'signal_t']
    return pd.DataFrame(trades, columns=cols)


def portfolio(trades, max_conc=3):
    """Keep trades in entry-time order while fewer than max_conc are open."""
    if trades.empty:
        return trades
    tr = trades.sort_values(['entry_t', 'sym']).reset_index(drop=True)
    keep = np.zeros(len(tr), bool)
    open_exits = []
    for idx, (t_in, t_out) in enumerate(zip(tr.entry_t.values, tr.exit_t.values)):
        open_exits = [x for x in open_exits if x > t_in]
        if len(open_exits) < max_conc:
            keep[idx] = True
            open_exits.append(t_out)
    return tr[keep].reset_index(drop=True)


def kpis(tr, days, equity0=330.0):
    """KPI dict for a trade list covering `days` calendar days."""
    if tr is None or len(tr) == 0:
        return dict(trades=0, trades_day=0.0)
    tr = tr.sort_values('exit_t')
    w = tr[tr.net > 0]; ls = tr[tr.net <= 0]
    gross = tr.gross.sum()
    eq = equity0 + tr.net.cumsum()
    peak = np.maximum.accumulate(np.r_[equity0, eq.values])[1:]
    dd = (eq.values - peak)
    dd_pct = dd / peak
    hrs = pd.to_datetime(tr.entry_t, unit='ms').dt.hour
    by_h = tr.groupby(hrs.values).net.sum().sort_values(ascending=False)
    return dict(
        trades=len(tr), trades_day=round(len(tr) / days, 2),
        win_pct=round(len(w) / len(tr) * 100, 1),
        avg_win=round(w.net.mean(), 3) if len(w) else 0.0,
        avg_loss=round(ls.net.mean(), 3) if len(ls) else 0.0,
        net=round(tr.net.sum(), 2), gross=round(gross, 2),
        fees=round(tr.fees.sum(), 2), slip=round(tr.slip.sum(), 2), funding=round(tr.funding.sum(), 3),
        fees_pct_gross=round(tr.fees.sum() / gross * 100, 0) if gross > 0 else None,
        pf=round(w.net.sum() / -ls.net.sum(), 2) if len(ls) and ls.net.sum() < 0 else None,
        exp_usd=round(tr.net.mean(), 3),
        exp_R=round((tr.net / tr.risk_usd).mean(), 3),
        maxdd_usd=round(dd.min(), 2), maxdd_pct=round(dd_pct.min() * 100, 1),
        hold_min=round(((tr.exit_t - tr.entry_t) / 60000).mean(), 1),
        best_hours=','.join(str(x) for x in by_h.index[:3]),
        net_day=round(tr.net.sum() / days, 3),
        avg_notional=round(tr.notional.mean(), 0),
    )
