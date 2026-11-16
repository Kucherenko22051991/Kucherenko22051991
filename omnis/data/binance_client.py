"""
OMNIS AI — Binance Data Client
Fetches market data from Binance USD-M Futures
"""
import time
import hashlib
import hmac
import os
import json
import urllib.request
import urllib.parse
from datetime import datetime
from typing import Optional


class BinanceFuturesClient:
    """
    Binance USD-M Futures REST client.
    Uses only public endpoints for market data (no API key needed for read).
    """
    BASE_URL = "https://fapi.binance.com"

    def _get(self, endpoint: str, params: dict = None) -> dict:
        url = self.BASE_URL + endpoint
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as e:
            return {"error": str(e)}

    # --------------------------------------------------------
    # PRICE DATA
    # --------------------------------------------------------
    def get_price(self, symbol: str) -> float:
        """Current mark price for symbol"""
        data = self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
        if "markPrice" in data:
            return float(data["markPrice"])
        return 0.0

    def get_all_prices(self) -> dict:
        """All symbol prices as {symbol: price}"""
        data = self._get("/fapi/v1/ticker/price")
        if isinstance(data, list):
            return {d["symbol"]: float(d["price"]) for d in data}
        return {}

    def get_24h_ticker(self, symbol: str) -> dict:
        """24h stats for a symbol"""
        return self._get("/fapi/v1/ticker/24hr", {"symbol": symbol})

    # --------------------------------------------------------
    # CANDLESTICK DATA
    # --------------------------------------------------------
    def get_klines(self, symbol: str, interval: str = "4h", limit: int = 200) -> list:
        """
        OHLCV candles. Returns list of dicts:
        [{"time", "open", "high", "low", "close", "volume"}, ...]
        """
        data = self._get("/fapi/v1/klines", {
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        })
        if isinstance(data, list):
            return [
                {
                    "time": d[0],
                    "open": float(d[1]),
                    "high": float(d[2]),
                    "low": float(d[3]),
                    "close": float(d[4]),
                    "volume": float(d[5]),
                    "close_time": d[6],
                    "quote_volume": float(d[7]),
                    "trades": d[8],
                }
                for d in data
            ]
        return []

    # --------------------------------------------------------
    # MARKET DEPTH / ORDER BOOK
    # --------------------------------------------------------
    def get_orderbook(self, symbol: str, limit: int = 20) -> dict:
        """Order book snapshot"""
        return self._get("/fapi/v1/depth", {"symbol": symbol, "limit": limit})

    def get_trades(self, symbol: str, limit: int = 100) -> list:
        """Recent trades"""
        return self._get("/fapi/v1/trades", {"symbol": symbol, "limit": limit})

    # --------------------------------------------------------
    # FUNDING RATE
    # --------------------------------------------------------
    def get_funding_rate(self, symbol: str) -> dict:
        """Current funding rate and next funding time"""
        data = self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
        if isinstance(data, dict) and "lastFundingRate" in data:
            return {
                "symbol": symbol,
                "funding_rate": float(data.get("lastFundingRate", 0)),
                "next_funding_time": data.get("nextFundingTime", 0),
                "mark_price": float(data.get("markPrice", 0)),
                "index_price": float(data.get("indexPrice", 0)),
            }
        return {}

    def get_all_funding_rates(self) -> list:
        """All symbols funding rates"""
        data = self._get("/fapi/v1/premiumIndex")
        if isinstance(data, list):
            return [
                {
                    "symbol": d["symbol"],
                    "funding_rate": float(d.get("lastFundingRate", 0)),
                    "mark_price": float(d.get("markPrice", 0)),
                }
                for d in data
            ]
        return []

    # --------------------------------------------------------
    # OPEN INTEREST
    # --------------------------------------------------------
    def get_open_interest(self, symbol: str) -> dict:
        """Current open interest for symbol"""
        return self._get("/fapi/v1/openInterest", {"symbol": symbol})

    def get_open_interest_hist(self, symbol: str, period: str = "4h", limit: int = 48) -> list:
        """Historical open interest"""
        return self._get("/futures/data/openInterestHist", {
            "symbol": symbol,
            "period": period,
            "limit": limit
        })

    # --------------------------------------------------------
    # LONG/SHORT RATIO
    # --------------------------------------------------------
    def get_long_short_ratio(self, symbol: str, period: str = "4h", limit: int = 48) -> list:
        """Global long/short ratio history"""
        return self._get("/futures/data/globalLongShortAccountRatio", {
            "symbol": symbol,
            "period": period,
            "limit": limit
        })

    def get_top_trader_ratio(self, symbol: str, period: str = "4h", limit: int = 48) -> list:
        """Top traders long/short ratio (whale sentiment)"""
        return self._get("/futures/data/topLongShortPositionRatio", {
            "symbol": symbol,
            "period": period,
            "limit": limit
        })

    # --------------------------------------------------------
    # LIQUIDATIONS
    # --------------------------------------------------------
    def get_liquidation_orders(self, symbol: str = None, limit: int = 100) -> list:
        """Recent liquidation orders"""
        params = {"limit": limit}
        if symbol:
            params["symbol"] = symbol
        return self._get("/fapi/v1/allForceOrders", params)

    # --------------------------------------------------------
    # EXCHANGE INFO
    # --------------------------------------------------------
    def get_exchange_info(self) -> dict:
        """Exchange info including all trading pairs"""
        return self._get("/fapi/v1/exchangeInfo")

    def get_active_symbols(self) -> list:
        """List of all active USDT perpetual symbols"""
        info = self.get_exchange_info()
        if "symbols" in info:
            return [
                s["symbol"] for s in info["symbols"]
                if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT"
            ]
        return []


# Singleton instance
binance = BinanceFuturesClient()
