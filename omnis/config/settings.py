"""
OMNIS AI — Configuration & Settings
Central configuration for all system components
"""
import os
from dataclasses import dataclass, field
from typing import Optional


# ============================================================
# SYSTEM MODES
# ============================================================
class SystemMode:
    RESEARCH = "research"
    PAPER_TRADING = "paper_trading"
    LIVE_TRADING = "live_trading"
    EMERGENCY_STOP = "emergency_stop"


# ============================================================
# RISK PARAMETERS (immutable safety constraints)
# ============================================================
@dataclass
class RiskConfig:
    max_risk_per_trade_pct: float = 1.0          # 0.5–1% per trade
    max_total_exposure_pct: float = 30.0          # max 30% of capital in open trades
    max_leverage: int = 5                          # subaccount hard limit
    default_leverage: int = 3                      # conservative default
    max_position_count: int = 6                    # max concurrent positions
    min_rr_ratio: float = 1.5                      # minimum reward:risk
    max_drawdown_pct: float = 15.0                 # pause trading if hit
    daily_loss_limit_pct: float = 5.0              # daily circuit breaker


# ============================================================
# TRADING PARAMETERS
# ============================================================
@dataclass
class TradingConfig:
    # Symbols to trade (prioritized list)
    universe: list = field(default_factory=lambda: [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
        "XRPUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT",
        "NEARUSDT", "RENDERUSDT", "ICPUSDT", "SUIUSDT",
    ])

    # Timeframes for analysis
    primary_tf: str = "4h"
    secondary_tfs: list = field(default_factory=lambda: ["1h", "15m", "1d"])

    # Scoring thresholds
    min_signal_score: float = 70.0        # min 70/100 to open trade
    strong_signal_score: float = 85.0     # strong signal threshold


# ============================================================
# DATA SOURCE CONFIG
# ============================================================
@dataclass
class DataConfig:
    # Binance
    binance_base_url: str = "https://fapi.binance.com"
    kline_limit: int = 200                 # candles per request

    # LunarCrush thresholds
    lunarcrush_altrank_max: int = 50       # only coins ranked < 50
    lunarcrush_galaxy_min: int = 50        # min galaxy score

    # CoinMarketCap
    cmc_trending_count: int = 20

    # Cache TTLs (seconds)
    price_cache_ttl: int = 30
    market_data_cache_ttl: int = 300
    social_data_cache_ttl: int = 600


# ============================================================
# ML MODEL CONFIG
# ============================================================
@dataclass
class MLConfig:
    model_path: str = "models/"
    prediction_horizon: int = 4            # hours ahead to predict
    min_training_samples: int = 1000
    retrain_interval_hours: int = 24
    ensemble_models: list = field(default_factory=lambda: [
        "xgboost", "lightgbm", "lstm"
    ])


# ============================================================
# SYSTEM CONFIG
# ============================================================
@dataclass
class OmnisConfig:
    mode: str = SystemMode.PAPER_TRADING   # start in paper trading
    version: str = "1.0.0"
    log_level: str = "INFO"

    risk: RiskConfig = field(default_factory=RiskConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    data: DataConfig = field(default_factory=DataConfig)
    ml: MLConfig = field(default_factory=MLConfig)

    # Dashboard
    dashboard_port: int = 8080
    websocket_port: int = 8081


# Default config instance
CONFIG = OmnisConfig()
