"""
OMNIS AI — Trend-following strategy (symmetric LONG / SHORT)

Why it exists: the backtest of the original technical scorer showed
(1) no edge and (2) it could never emit SHORT (its minimum score was 41).
This strategy is symmetric by construction and trades only with the
higher-timeframe trend.

Rules (evaluated on bar close, entry on next bar open):
  LONG  : close > EMA200, EMA20 > EMA50, MACD histogram turns positive,
          RSI between rsi_lo and rsi_hi (not overbought)
  SHORT : mirror image

All indicators are causal: the value at bar i uses only candles[0..i].
"""
from omnis.intelligence.technical import ema, rsi, macd, atr


def _align(series: list, n: int) -> list:
    """Right-align an indicator series to length n (None-padded on the left)."""
    return [None] * (n - len(series)) + list(series)


def precompute(candles: list) -> dict:
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    n = len(closes)
    m = macd(closes)
    return {
        "close": closes,
        "ema20": _align(ema(closes, 20), n),
        "ema50": _align(ema(closes, 50), n),
        "ema200": _align(ema(closes, 200), n),
        "rsi": _align(rsi(closes, 14), n),
        "hist": _align(m["histogram"], n),
        "atr": _align(atr(highs, lows, closes, 14), n),
    }


def signal_at(ind: dict, i: int, rsi_lo: float = 40, rsi_hi: float = 65) -> str:
    """Return 'LONG', 'SHORT' or '' for bar i."""
    vals = [ind[k][i] for k in ("ema20", "ema50", "ema200", "rsi", "hist", "atr")]
    if any(v is None for v in vals) or ind["hist"][i - 1] is None:
        return ""
    e20, e50, e200, r, h, _ = vals
    h_prev = ind["hist"][i - 1]
    c = ind["close"][i]

    if c > e200 and e20 > e50 and h_prev <= 0 < h and rsi_lo <= r <= rsi_hi:
        return "LONG"
    if c < e200 and e20 < e50 and h_prev >= 0 > h and (100 - rsi_hi) <= r <= (100 - rsi_lo):
        return "SHORT"
    return ""
