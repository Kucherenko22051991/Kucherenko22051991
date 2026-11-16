"""
OMNIS AI — Technical Analysis Engine
Implements all indicators needed for signal generation:
- Trend indicators: EMA, SMA, VWAP, Ichimoku
- Momentum: RSI, MACD, Stochastic
- Volatility: ATR, Bollinger Bands
- Volume: OBV, Volume Profile
- Smart Money Concepts: Order Blocks, FVG, BOS/CHoCH, Liquidity
"""
from typing import Optional


def ema(prices: list, period: int) -> list:
    """Exponential Moving Average"""
    if len(prices) < period:
        return []
    k = 2 / (period + 1)
    result = [sum(prices[:period]) / period]
    for p in prices[period:]:
        result.append(p * k + result[-1] * (1 - k))
    return result


def sma(prices: list, period: int) -> list:
    """Simple Moving Average"""
    if len(prices) < period:
        return []
    return [sum(prices[i:i+period])/period for i in range(len(prices)-period+1)]


def rsi(prices: list, period: int = 14) -> list:
    """Relative Strength Index"""
    if len(prices) <= period:
        return []

    gains, losses = [], []
    for i in range(1, len(prices)):
        diff = prices[i] - prices[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    result = []
    for i in range(period, len(gains)):
        if avg_loss == 0:
            result.append(100.0)
        else:
            rs = avg_gain / avg_loss
            result.append(100 - 100 / (1 + rs))
        avg_gain = (avg_gain * (period-1) + gains[i]) / period
        avg_loss = (avg_loss * (period-1) + losses[i]) / period

    return result


def macd(prices: list, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """MACD indicator — returns {macd, signal, histogram}"""
    if len(prices) < slow + signal:
        return {"macd": [], "signal": [], "histogram": []}

    ema_fast = ema(prices, fast)
    ema_slow = ema(prices, slow)

    offset = slow - fast
    macd_line = [f - s for f, s in zip(ema_fast[offset:], ema_slow)]
    signal_line = ema(macd_line, signal)

    offset2 = len(macd_line) - len(signal_line)
    histogram = [m - s for m, s in zip(macd_line[offset2:], signal_line)]

    return {
        "macd": macd_line,
        "signal": signal_line,
        "histogram": histogram
    }


def atr(highs: list, lows: list, closes: list, period: int = 14) -> list:
    """Average True Range"""
    if len(highs) < period + 1:
        return []

    tr_list = []
    for i in range(1, len(highs)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        tr_list.append(tr)

    result = [sum(tr_list[:period]) / period]
    for tr in tr_list[period:]:
        result.append((result[-1] * (period-1) + tr) / period)
    return result


def bollinger_bands(prices: list, period: int = 20, std_dev: float = 2.0) -> dict:
    """Bollinger Bands — returns {upper, middle, lower, width}"""
    if len(prices) < period:
        return {"upper": [], "middle": [], "lower": [], "width": []}

    middle = sma(prices, period)
    upper, lower, width = [], [], []

    for i in range(len(middle)):
        window = prices[i:i+period]
        mean = middle[i]
        std = (sum((x - mean)**2 for x in window) / period) ** 0.5
        u = mean + std_dev * std
        l = mean - std_dev * std
        upper.append(u)
        lower.append(l)
        width.append((u - l) / mean if mean != 0 else 0)

    return {"upper": upper, "middle": middle, "lower": lower, "width": width}


def stochastic(highs: list, lows: list, closes: list, k_period: int = 14, d_period: int = 3) -> dict:
    """Stochastic Oscillator"""
    if len(closes) < k_period:
        return {"k": [], "d": []}

    k_values = []
    for i in range(k_period-1, len(closes)):
        h = max(highs[i-k_period+1:i+1])
        l = min(lows[i-k_period+1:i+1])
        if h == l:
            k_values.append(50.0)
        else:
            k_values.append(100 * (closes[i] - l) / (h - l))

    d_values = sma(k_values, d_period) if len(k_values) >= d_period else []
    return {"k": k_values, "d": d_values}


def obv(closes: list, volumes: list) -> list:
    """On-Balance Volume"""
    if len(closes) < 2:
        return []

    result = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            result.append(result[-1] + volumes[i])
        elif closes[i] < closes[i-1]:
            result.append(result[-1] - volumes[i])
        else:
            result.append(result[-1])
    return result


def vwap(highs: list, lows: list, closes: list, volumes: list) -> list:
    """Volume Weighted Average Price"""
    result = []
    cum_pv = 0
    cum_vol = 0
    for h, l, c, v in zip(highs, lows, closes, volumes):
        typical = (h + l + c) / 3
        cum_pv += typical * v
        cum_vol += v
        result.append(cum_pv / cum_vol if cum_vol > 0 else c)
    return result


# ============================================================
# SMART MONEY CONCEPTS
# ============================================================

def find_order_blocks(candles: list, lookback: int = 20) -> list:
    """
    Identify Order Blocks — last down-candle before significant up-move (bullish OB)
    and last up-candle before significant down-move (bearish OB).
    Returns list of {"type": "bull"|"bear", "high", "low", "index"}
    """
    obs = []
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]

    for i in range(lookback, len(candles) - 3):
        # Bullish OB: bearish candle followed by strong bullish move
        if closes[i] < candles[i]["open"]:  # bearish candle
            future_move = max(highs[i+1:i+4]) - closes[i]
            candle_range = candles[i]["open"] - closes[i]
            if candle_range > 0 and future_move > candle_range * 1.5:
                obs.append({
                    "type": "bull",
                    "high": candles[i]["open"],
                    "low": closes[i],
                    "index": i,
                    "strength": future_move / candle_range
                })

        # Bearish OB: bullish candle followed by strong bearish move
        elif closes[i] > candles[i]["open"]:  # bullish candle
            future_drop = closes[i] - min(lows[i+1:i+4])
            candle_range = closes[i] - candles[i]["open"]
            if candle_range > 0 and future_drop > candle_range * 1.5:
                obs.append({
                    "type": "bear",
                    "high": closes[i],
                    "low": candles[i]["open"],
                    "index": i,
                    "strength": future_drop / candle_range
                })

    return obs


def find_fair_value_gaps(candles: list) -> list:
    """
    Fair Value Gaps (FVG) — 3-candle pattern where gap exists between
    candle[i-2].high and candle[i].low (bullish) or
    candle[i-2].low and candle[i].high (bearish)
    """
    fvgs = []
    for i in range(2, len(candles)):
        # Bullish FVG
        if candles[i]["low"] > candles[i-2]["high"]:
            fvgs.append({
                "type": "bull",
                "top": candles[i]["low"],
                "bottom": candles[i-2]["high"],
                "index": i,
                "size": candles[i]["low"] - candles[i-2]["high"]
            })
        # Bearish FVG
        elif candles[i]["high"] < candles[i-2]["low"]:
            fvgs.append({
                "type": "bear",
                "top": candles[i-2]["low"],
                "bottom": candles[i]["high"],
                "index": i,
                "size": candles[i-2]["low"] - candles[i]["high"]
            })
    return fvgs


def find_swing_points(prices: list, lookback: int = 5) -> dict:
    """Find swing highs and lows"""
    swing_highs = []
    swing_lows = []

    for i in range(lookback, len(prices) - lookback):
        window = prices[i-lookback:i+lookback+1]
        if prices[i] == max(window):
            swing_highs.append({"index": i, "price": prices[i]})
        elif prices[i] == min(window):
            swing_lows.append({"index": i, "price": prices[i]})

    return {"highs": swing_highs, "lows": swing_lows}


def detect_bos_choch(candles: list) -> list:
    """
    Break of Structure (BOS) and Change of Character (CHoCH)
    Returns list of structure breaks with direction
    """
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]

    swings = find_swing_points(closes)
    events = []

    swing_highs = [s["price"] for s in swings["highs"]]
    swing_lows = [s["price"] for s in swings["lows"]]

    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        # BOS up: price breaks above last swing high (bullish structure)
        last_sh = max(swing_highs[-3:]) if len(swing_highs) >= 3 else swing_highs[-1]
        if closes[-1] > last_sh:
            events.append({"type": "BOS", "direction": "bull", "level": last_sh})

        # BOS down: price breaks below last swing low (bearish structure)
        last_sl = min(swing_lows[-3:]) if len(swing_lows) >= 3 else swing_lows[-1]
        if closes[-1] < last_sl:
            events.append({"type": "BOS", "direction": "bear", "level": last_sl})

    return events


def find_liquidity_zones(highs: list, lows: list, closes: list, threshold: float = 0.001) -> dict:
    """
    Find liquidity pools — equal highs/lows where stops likely clustered
    Returns dict with buy_side (above equal highs) and sell_side (below equal lows)
    """
    buy_side = []   # above = buy stops / sell liquidity
    sell_side = []  # below = sell stops / buy liquidity

    # Find equal highs (within threshold)
    for i in range(len(highs)-1):
        for j in range(i+1, min(i+20, len(highs))):
            if abs(highs[i] - highs[j]) / highs[i] < threshold:
                buy_side.append({
                    "price": max(highs[i], highs[j]),
                    "strength": 2,
                    "indices": [i, j]
                })

    # Find equal lows
    for i in range(len(lows)-1):
        for j in range(i+1, min(i+20, len(lows))):
            if abs(lows[i] - lows[j]) / lows[i] < threshold:
                sell_side.append({
                    "price": min(lows[i], lows[j]),
                    "strength": 2,
                    "indices": [i, j]
                })

    return {"buy_side": buy_side, "sell_side": sell_side}


# ============================================================
# MASTER ANALYSIS FUNCTION
# ============================================================

def analyze_symbol(candles: list) -> dict:
    """
    Full technical analysis on candle data.
    Returns comprehensive analysis dict.
    """
    if len(candles) < 50:
        return {"error": "insufficient_data"}

    closes = [c["close"] for c in candles]
    opens  = [c["open"]  for c in candles]
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    vols   = [c["volume"] for c in candles]

    # Trend indicators
    ema20  = ema(closes, 20)
    ema50  = ema(closes, 50)
    ema200 = ema(closes, 200) if len(closes) >= 200 else []

    # Momentum
    rsi14  = rsi(closes, 14)
    macd_d = macd(closes)
    stoch  = stochastic(highs, lows, closes)

    # Volatility
    atr14  = atr(highs, lows, closes, 14)
    bb     = bollinger_bands(closes, 20)

    # Volume
    obv_v  = obv(closes, vols)
    vwap_v = vwap(highs, lows, closes, vols)

    # Smart Money
    order_blocks = find_order_blocks(candles)
    fvgs         = find_fair_value_gaps(candles)
    structure    = detect_bos_choch(candles)
    liquidity    = find_liquidity_zones(highs, lows, closes)

    # Current values (last)
    current_price = closes[-1]
    curr_rsi = rsi14[-1] if rsi14 else 50
    curr_macd = macd_d["histogram"][-1] if macd_d["histogram"] else 0
    curr_atr = atr14[-1] if atr14 else 0

    # Trend direction
    trend = "neutral"
    if ema20 and ema50:
        if ema20[-1] > ema50[-1] and closes[-1] > ema20[-1]:
            trend = "bullish"
        elif ema20[-1] < ema50[-1] and closes[-1] < ema20[-1]:
            trend = "bearish"

    # Signal score (0-100)
    score = 50
    signals = []

    # RSI signals
    if curr_rsi < 30:
        score += 15
        signals.append("RSI oversold")
    elif curr_rsi > 70:
        score -= 15
        signals.append("RSI overbought")
    elif 40 <= curr_rsi <= 60:
        score += 5

    # MACD signals
    if curr_macd > 0 and macd_d["histogram"]:
        prev_hist = macd_d["histogram"][-2] if len(macd_d["histogram"]) > 1 else 0
        if prev_hist < 0:
            score += 20
            signals.append("MACD bullish cross")
        else:
            score += 5
    elif curr_macd < 0:
        score -= 10

    # Trend alignment
    if trend == "bullish":
        score += 10
        signals.append("uptrend")
    elif trend == "bearish":
        score -= 10

    # Order block near current price
    for ob in order_blocks[-5:]:
        if ob["type"] == "bull" and ob["low"] <= current_price <= ob["high"] * 1.02:
            score += 15
            signals.append(f"bullish OB @ {ob['low']:.4f}")
            break

    # FVG fill opportunity
    for fvg in fvgs[-5:]:
        if fvg["type"] == "bull" and fvg["bottom"] <= current_price <= fvg["top"] * 1.01:
            score += 10
            signals.append(f"bullish FVG @ {fvg['bottom']:.4f}-{fvg['top']:.4f}")
            break

    score = max(0, min(100, score))

    return {
        "price": current_price,
        "trend": trend,
        "signal_score": round(score, 1),
        "signals": signals,
        "rsi": round(curr_rsi, 2),
        "macd_hist": round(curr_macd, 6),
        "atr": round(curr_atr, 6),
        "atr_pct": round(curr_atr / current_price * 100, 3),
        "ema20": round(ema20[-1], 6) if ema20 else None,
        "ema50": round(ema50[-1], 6) if ema50 else None,
        "ema200": round(ema200[-1], 6) if ema200 else None,
        "bb_upper": round(bb["upper"][-1], 6) if bb["upper"] else None,
        "bb_lower": round(bb["lower"][-1], 6) if bb["lower"] else None,
        "bb_width_pct": round(bb["width"][-1] * 100, 3) if bb["width"] else None,
        "order_blocks": order_blocks[-3:],
        "fvgs": fvgs[-3:],
        "structure_events": structure,
        "liquidity": {
            "buy_side": liquidity["buy_side"][-2:],
            "sell_side": liquidity["sell_side"][-2:]
        },
    }
