"""
OMNIS AI — Multi-Factor Signal Scorer
Combines technical, social, derivatives, and market data
into a unified 0-100 signal score for each symbol.
"""
from omnis.intelligence.technical import analyze_symbol


def _clamp(val: float, lo: float = 0, hi: float = 100) -> float:
    return max(lo, min(hi, val))


def score_from_technical(ta: dict) -> tuple:
    """
    Returns (score, signals) from a technical analysis result.
    score is a delta (-50 to +50) relative to 50 baseline.
    """
    if "error" in ta:
        return 0, []

    delta = 0
    signals = list(ta.get("signals", []))

    rsi = ta.get("rsi", 50)
    if rsi < 30:
        delta += 15
    elif rsi < 40:
        delta += 8
    elif rsi > 70:
        delta -= 15
    elif rsi > 60:
        delta -= 5

    macd_hist = ta.get("macd_hist", 0)
    if macd_hist > 0:
        delta += 8
    else:
        delta -= 5

    trend = ta.get("trend", "neutral")
    if trend == "bullish":
        delta += 10
    elif trend == "bearish":
        delta -= 10

    # ATR % — moderate volatility preferred for trading
    atr_pct = ta.get("atr_pct", 0)
    if 1.5 < atr_pct < 6:
        delta += 5

    # Order blocks bonus already in ta.signal_score
    # Pull it and re-center
    ta_score = ta.get("signal_score", 50) - 50  # center at 0
    return ta_score, signals


def score_from_lunarcrush(lc_data: dict) -> tuple:
    """
    Score from LunarCrush metrics.
    lc_data expected: {altrank, galaxy_score, ...}
    Returns (delta, signals)
    """
    if not lc_data:
        return 0, []

    delta = 0
    signals = []

    altrank = lc_data.get("altrank", 999)
    galaxy = lc_data.get("galaxy_score", 0)

    # AltRank: lower = better momentum (1 = top)
    if altrank <= 10:
        delta += 20
        signals.append(f"AltRank #{altrank} (top)")
    elif altrank <= 25:
        delta += 15
        signals.append(f"AltRank #{altrank} (strong)")
    elif altrank <= 50:
        delta += 8
        signals.append(f"AltRank #{altrank} (good)")
    elif altrank > 200:
        delta -= 10

    # Galaxy Score: higher = better sentiment
    if galaxy >= 70:
        delta += 15
        signals.append(f"Galaxy {galaxy} (bullish)")
    elif galaxy >= 55:
        delta += 8
    elif galaxy < 40:
        delta -= 10

    return delta, signals


def score_from_derivatives(deriv_data: dict) -> tuple:
    """
    Score from derivatives data (funding rate, long/short ratio, OI).
    Returns (delta, signals)
    """
    if not deriv_data:
        return 0, []

    delta = 0
    signals = []

    # Funding rate: negative = shorts paying = bullish
    funding = deriv_data.get("funding_rate", 0)
    if funding < -0.001:
        delta += 15
        signals.append(f"Funding {funding:.4%} (shorts paying)")
    elif funding < 0:
        delta += 5
    elif funding > 0.003:
        delta -= 10
        signals.append(f"Funding {funding:.4%} (overcrowded longs)")
    elif funding > 0.001:
        delta -= 5

    # Long/short ratio: < 1.0 = more shorts = contrarian bullish
    ls_ratio = deriv_data.get("long_short_ratio", 1.0)
    if ls_ratio < 0.8:
        delta += 10
        signals.append(f"L/S ratio {ls_ratio:.2f} (short squeeze risk)")
    elif ls_ratio > 2.0:
        delta -= 8
        signals.append(f"L/S ratio {ls_ratio:.2f} (crowded longs)")

    # OI change: rising OI + rising price = strong trend
    oi_change_pct = deriv_data.get("oi_change_pct", 0)
    if oi_change_pct > 5:
        delta += 8
        signals.append(f"OI +{oi_change_pct:.1f}% (momentum)")
    elif oi_change_pct < -10:
        delta -= 5

    return delta, signals


def score_from_momentum(momentum_data: dict) -> tuple:
    """
    Score from price momentum (24h, 7d change).
    Returns (delta, signals)
    """
    if not momentum_data:
        return 0, []

    delta = 0
    signals = []

    change_24h = momentum_data.get("change_24h", 0)
    change_7d = momentum_data.get("change_7d", 0)
    volume_ratio = momentum_data.get("volume_ratio", 1.0)  # vs avg

    # Moderate positive momentum is bullish
    if 5 < change_24h < 20:
        delta += 10
        signals.append(f"+{change_24h:.1f}% 24h momentum")
    elif change_24h > 20:
        # Overextended — reduce score
        delta -= 5
        signals.append(f"+{change_24h:.1f}% 24h (extended)")
    elif change_24h < -10:
        delta -= 10

    if change_7d > 15:
        delta += 8
    elif change_7d < -20:
        delta -= 10

    # Volume spike = confirmation
    if volume_ratio > 2.0:
        delta += 8
        signals.append(f"Volume {volume_ratio:.1f}x avg (surge)")

    return delta, signals


def compute_signal(
    symbol: str,
    candles: list,
    lunarcrush_data: dict = None,
    derivatives_data: dict = None,
    momentum_data: dict = None,
    weights: dict = None
) -> dict:
    """
    Master signal scoring function.
    Combines all data sources into final 0-100 score.

    Parameters
    ----------
    symbol : str
        Trading symbol, e.g. 'ICPUSDT'
    candles : list
        OHLCV candles from BinanceFuturesClient.get_klines()
    lunarcrush_data : dict, optional
        {altrank, galaxy_score}
    derivatives_data : dict, optional
        {funding_rate, long_short_ratio, oi_change_pct}
    momentum_data : dict, optional
        {change_24h, change_7d, volume_ratio}
    weights : dict, optional
        Custom weights for each source (default applied if None)

    Returns
    -------
    dict
        {score, direction, signals, components, ta, raw_score}
    """
    # Default weights
    default_weights = {
        "technical":  0.45,
        "lunarcrush": 0.20,
        "derivatives": 0.20,
        "momentum":   0.15,
    }
    w = weights or default_weights

    all_signals = []
    weighted_score = 50.0  # start at neutral

    # --- Technical Analysis (highest weight) ---
    ta = analyze_symbol(candles)
    ta_delta, ta_signals = score_from_technical(ta)
    all_signals.extend(ta_signals)
    ta_contribution = ta_delta * w["technical"]

    # --- LunarCrush Social ---
    lc_delta, lc_signals = score_from_lunarcrush(lunarcrush_data or {})
    all_signals.extend(lc_signals)
    lc_contribution = lc_delta * w["lunarcrush"]

    # --- Derivatives ---
    deriv_delta, deriv_signals = score_from_derivatives(derivatives_data or {})
    all_signals.extend(deriv_signals)
    deriv_contribution = deriv_delta * w["derivatives"]

    # --- Momentum ---
    mom_delta, mom_signals = score_from_momentum(momentum_data or {})
    all_signals.extend(mom_signals)
    mom_contribution = mom_delta * w["momentum"]

    # --- Combine ---
    weighted_score = 50 + ta_contribution + lc_contribution + deriv_contribution + mom_contribution
    final_score = round(_clamp(weighted_score), 1)

    # Direction
    if final_score >= 70:
        direction = "LONG"
    elif final_score <= 35:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    return {
        "symbol": symbol,
        "score": final_score,
        "direction": direction,
        "signals": all_signals[:10],  # top 10
        "components": {
            "technical": round(50 + ta_contribution, 1),
            "lunarcrush": round(50 + lc_contribution, 1),
            "derivatives": round(50 + deriv_contribution, 1),
            "momentum": round(50 + mom_contribution, 1),
        },
        "ta": ta,
        "raw_score": round(weighted_score, 2),
    }


def rank_universe(scored_signals: list) -> list:
    """
    Sort symbols by score, return ranked list.
    scored_signals: list of compute_signal() results
    """
    return sorted(scored_signals, key=lambda x: x["score"], reverse=True)


def filter_tradeable(ranked: list, min_score: float = 70.0) -> list:
    """Return only signals above the minimum score threshold."""
    return [s for s in ranked if s["score"] >= min_score]
