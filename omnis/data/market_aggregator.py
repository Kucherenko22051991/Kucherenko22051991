"""
OMNIS AI — Market Aggregator
Combines all external data sources into a unified market intelligence feed:
- Binance: price, klines, funding rate, L/S ratio, OI
- LunarCrush: AltRank, Galaxy Score
- CoinGecko: trending, global market
- CoinMarketCap: narratives, quotes
- Cryptowisser: trending news

Produces MarketIntelligence dict per symbol for compute_signal().
"""
import json
import urllib.request
import urllib.parse
import time
from typing import Optional

from omnis.data.binance_client import binance
from omnis.config.settings import CONFIG


# ============================================================
# LUNARCRUSH ADAPTER
# ============================================================

def fetch_lunarcrush_rankings() -> dict:
    """
    Fetch LunarCrush coin rankings via public API.
    Returns {symbol: {altrank, galaxy_score, social_volume}} dict.
    NOTE: LunarCrush tools available via MCP — this is the stdlib fallback.
    """
    try:
        url = "https://lunarcrush.com/api4/public/coins/list/v1"
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "OMNIS-AI/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            if isinstance(data, dict) and "data" in data:
                result = {}
                for item in data["data"]:
                    sym = item.get("symbol", "").upper()
                    if sym:
                        result[sym] = {
                            "altrank": item.get("alt_rank", 999),
                            "galaxy_score": item.get("galaxy_score", 0),
                            "social_volume": item.get("social_volume", 0),
                            "social_dominance": item.get("social_dominance", 0),
                            "market_dominance": item.get("market_dominance", 0),
                        }
                return result
    except Exception as e:
        pass
    return {}


# ============================================================
# COINGECKO ADAPTER
# ============================================================

def fetch_coingecko_trending() -> list:
    """
    Fetch trending coins from CoinGecko.
    Returns list of coin symbols.
    """
    try:
        url = "https://api.coingecko.com/api/v3/search/trending"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            coins = data.get("coins", [])
            return [c["item"]["symbol"].upper() for c in coins]
    except Exception:
        return []


def fetch_coingecko_global() -> dict:
    """
    Fetch global crypto market data from CoinGecko.
    Returns {total_market_cap_usd, market_cap_change_24h_pct, btc_dominance, ...}
    """
    try:
        url = "https://api.coingecko.com/api/v3/global"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            gdata = data.get("data", {})
            return {
                "total_market_cap_usd": gdata.get("total_market_cap", {}).get("usd", 0),
                "market_cap_change_24h": gdata.get("market_cap_change_percentage_24h_usd", 0),
                "btc_dominance": gdata.get("market_cap_percentage", {}).get("btc", 0),
                "eth_dominance": gdata.get("market_cap_percentage", {}).get("eth", 0),
                "total_volume_usd": gdata.get("total_volume", {}).get("usd", 0),
                "active_cryptocurrencies": gdata.get("active_cryptocurrencies", 0),
            }
    except Exception:
        return {}


def fetch_coingecko_markets(symbols: list) -> dict:
    """
    Fetch market data for specific symbols from CoinGecko.
    Returns {symbol: {price, change_24h, change_7d, volume}} dict.
    """
    # Build a mapping of common symbols to CoinGecko IDs
    symbol_to_id = {
        "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
        "BNB": "binancecoin", "XRP": "ripple", "DOGE": "dogecoin",
        "AVAX": "avalanche-2", "LINK": "chainlink", "NEAR": "near",
        "RENDER": "render-token", "ICP": "internet-computer", "SUI": "sui",
        "ADA": "cardano", "DOT": "polkadot", "MATIC": "matic-network",
        "ATOM": "cosmos", "LTC": "litecoin", "UNI": "uniswap",
        "AAVE": "aave", "ARB": "arbitrum", "OP": "optimism",
        "INJ": "injective-protocol", "TIA": "celestia", "JUP": "jupiter-exchange-solana",
        "WLD": "worldcoin-wld", "PYTH": "pyth-network", "JTO": "jito-governance-token",
    }

    ids = []
    sym_map = {}
    for sym in symbols:
        base = sym.replace("USDT", "").replace("BUSD", "")
        if base in symbol_to_id:
            cg_id = symbol_to_id[base]
            ids.append(cg_id)
            sym_map[cg_id] = base

    if not ids:
        return {}

    try:
        params = urllib.parse.urlencode({
            "ids": ",".join(ids),
            "vs_currency": "usd",
            "include_24hr_change": "true",
            "include_7d_change": "true",
            "include_24hr_vol": "true",
            "price_change_percentage": "24h,7d"
        })
        url = f"https://api.coingecko.com/api/v3/coins/markets?{params}"
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
            result = {}
            for coin in data:
                base = sym_map.get(coin.get("id", ""), "")
                if base:
                    result[base] = {
                        "price": coin.get("current_price", 0),
                        "change_24h": coin.get("price_change_percentage_24h") or 0,
                        "change_7d": coin.get("price_change_percentage_7d_in_currency") or 0,
                        "volume_24h": coin.get("total_volume", 0),
                        "market_cap": coin.get("market_cap", 0),
                        "volume_ratio": 1.0,  # Will be computed below
                    }
            return result
    except Exception:
        return {}


# ============================================================
# BINANCE DERIVATIVES DATA
# ============================================================

def fetch_derivatives_data(symbol: str) -> dict:
    """
    Fetch derivatives intelligence for a symbol from Binance.
    Returns {funding_rate, long_short_ratio, oi_change_pct}
    """
    result = {}

    try:
        # Funding rate
        fr = binance.get_funding_rate(symbol)
        if fr:
            result["funding_rate"] = fr.get("funding_rate", 0)
            result["mark_price"] = fr.get("mark_price", 0)
    except Exception:
        pass

    try:
        # Long/short ratio
        ls = binance.get_long_short_ratio(symbol, period="4h", limit=2)
        if isinstance(ls, list) and len(ls) >= 1:
            result["long_short_ratio"] = float(ls[-1].get("longShortRatio", 1.0))
    except Exception:
        pass

    try:
        # OI change
        oi_hist = binance.get_open_interest_hist(symbol, period="4h", limit=2)
        if isinstance(oi_hist, list) and len(oi_hist) >= 2:
            curr_oi = float(oi_hist[-1].get("sumOpenInterest", 0))
            prev_oi = float(oi_hist[-2].get("sumOpenInterest", 1))
            if prev_oi > 0:
                result["oi_change_pct"] = (curr_oi - prev_oi) / prev_oi * 100
    except Exception:
        pass

    return result


def fetch_momentum_data(symbol: str, cg_data: dict = None) -> dict:
    """
    Build momentum data for a symbol.
    Uses CoinGecko data if available, falls back to Binance 24h ticker.
    Returns {change_24h, change_7d, volume_ratio}
    """
    base = symbol.replace("USDT", "").replace("BUSD", "")

    # Try CoinGecko first (has 7d data)
    if cg_data and base in cg_data:
        coin = cg_data[base]
        return {
            "change_24h": coin.get("change_24h", 0),
            "change_7d": coin.get("change_7d", 0),
            "volume_ratio": 1.0,
        }

    # Fallback: Binance 24h ticker
    try:
        ticker = binance.get_24h_ticker(symbol)
        if isinstance(ticker, dict) and "priceChangePercent" in ticker:
            change_24h = float(ticker.get("priceChangePercent", 0))
            volume = float(ticker.get("volume", 0))
            quote_vol = float(ticker.get("quoteVolume", 0))
            return {
                "change_24h": change_24h,
                "change_7d": 0,  # Not available from Binance 24h
                "volume_ratio": 1.0,
                "volume_24h": quote_vol,
            }
    except Exception:
        pass

    return {}


# ============================================================
# MASTER AGGREGATOR
# ============================================================

class MarketAggregator:
    """
    Unified market intelligence aggregator.
    Fetches and caches all data sources, produces per-symbol intelligence dicts.
    """

    def __init__(self):
        self._cache = {}
        self._cache_ts = {}
        self.ttl = {
            "lunarcrush": 600,   # 10 min
            "coingecko_global": 300,
            "coingecko_trending": 300,
            "coingecko_markets": 300,
            "derivatives": 60,   # 1 min — derivatives change fast
        }

    def _is_fresh(self, key: str) -> bool:
        """Check if cached data is still fresh."""
        ts = self._cache_ts.get(key, 0)
        ttl = self.ttl.get(key, 300)
        return (time.time() - ts) < ttl

    def get_lunarcrush(self) -> dict:
        """Cached LunarCrush rankings."""
        if not self._is_fresh("lunarcrush") or "lunarcrush" not in self._cache:
            data = fetch_lunarcrush_rankings()
            if data:
                self._cache["lunarcrush"] = data
                self._cache_ts["lunarcrush"] = time.time()
        return self._cache.get("lunarcrush", {})

    def get_global_market(self) -> dict:
        """Cached CoinGecko global market."""
        if not self._is_fresh("coingecko_global") or "coingecko_global" not in self._cache:
            data = fetch_coingecko_global()
            if data:
                self._cache["coingecko_global"] = data
                self._cache_ts["coingecko_global"] = time.time()
        return self._cache.get("coingecko_global", {})

    def get_trending_symbols(self) -> list:
        """Cached CoinGecko trending symbols."""
        if not self._is_fresh("coingecko_trending") or "coingecko_trending" not in self._cache:
            data = fetch_coingecko_trending()
            self._cache["coingecko_trending"] = data
            self._cache_ts["coingecko_trending"] = time.time()
        return self._cache.get("coingecko_trending", [])

    def get_coingecko_markets(self, symbols: list) -> dict:
        """Cached CoinGecko market data."""
        cache_key = "coingecko_markets"
        if not self._is_fresh(cache_key) or cache_key not in self._cache:
            data = fetch_coingecko_markets(symbols)
            if data:
                self._cache[cache_key] = data
                self._cache_ts[cache_key] = time.time()
        return self._cache.get(cache_key, {})

    def build_symbol_intelligence(self, symbol: str, candles_limit: int = 200) -> dict:
        """
        Build complete market intelligence dict for one symbol.
        Returns dict ready to pass to compute_signal().
        """
        base = symbol.replace("USDT", "").replace("BUSD", "")

        # Fetch candles from Binance
        candles = binance.get_klines(symbol, interval=CONFIG.trading.primary_tf, limit=candles_limit)

        # LunarCrush
        lc_all = self.get_lunarcrush()
        lc_data = lc_all.get(base, {})
        lunarcrush_data = None
        if lc_data:
            lunarcrush_data = {
                "altrank": lc_data.get("altrank", 999),
                "galaxy_score": lc_data.get("galaxy_score", 0),
            }

        # Derivatives
        derivatives_data = fetch_derivatives_data(symbol) or None

        # Momentum
        cg_markets = self.get_coingecko_markets(CONFIG.trading.universe)
        momentum_data = fetch_momentum_data(symbol, cg_markets) or None

        # Trending bonus metadata
        trending = self.get_trending_symbols()
        is_trending = base in trending

        return {
            "symbol": symbol,
            "candles": candles,
            "lunarcrush_data": lunarcrush_data,
            "derivatives_data": derivatives_data,
            "momentum_data": momentum_data,
            "is_trending": is_trending,
            "trending_symbols": trending,
        }

    def scan_universe(self, symbols: list = None, candles_limit: int = 200) -> list:
        """
        Scan all symbols in universe and build intelligence for each.
        Returns list of intelligence dicts.
        """
        universe = symbols or CONFIG.trading.universe
        results = []

        for sym in universe:
            try:
                intel = self.build_symbol_intelligence(sym, candles_limit)
                results.append(intel)
                time.sleep(0.1)  # Rate limit friendly
            except Exception as e:
                results.append({
                    "symbol": sym,
                    "candles": [],
                    "error": str(e),
                    "lunarcrush_data": None,
                    "derivatives_data": None,
                    "momentum_data": None,
                })

        return results

    def get_market_regime(self) -> dict:
        """
        Assess overall market regime from global data.
        Returns {regime, btc_dominance, market_direction, confidence}
        """
        global_data = self.get_global_market()
        if not global_data:
            return {"regime": "unknown", "confidence": 0}

        cap_change = global_data.get("market_cap_change_24h", 0)
        btc_dom = global_data.get("btc_dominance", 50)

        # Regime detection
        if cap_change > 3:
            regime = "bull_momentum"
        elif cap_change > 0:
            regime = "bull_mild"
        elif cap_change > -3:
            regime = "bear_mild"
        else:
            regime = "bear_momentum"

        # High BTC dominance = risk-off (alts underperform)
        # Low BTC dominance = risk-on (alt season)
        if btc_dom > 55:
            alt_environment = "risk_off"
        elif btc_dom < 45:
            alt_environment = "alt_season"
        else:
            alt_environment = "neutral"

        return {
            "regime": regime,
            "market_cap_change_24h": cap_change,
            "btc_dominance": btc_dom,
            "alt_environment": alt_environment,
            "total_market_cap_usd": global_data.get("total_market_cap_usd", 0),
            "confidence": 0.8 if abs(cap_change) > 2 else 0.5,
        }


# Singleton instance
aggregator = MarketAggregator()
