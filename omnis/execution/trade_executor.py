"""
OMNIS AI — Trade Executor
Autonomous trade execution engine.

Flow:
  1. Receives scored signal from compute_signal() / filter_tradeable()
  2. Checks account balance and open positions against RiskConfig limits
  3. Calculates position size (0.5–1% risk per trade)
  4. Sets leverage (max 5x — subaccount hard limit)
  5. Places MARKET order for entry
  6. Places LIMIT+reduceOnly order for TP (STOP_MARKET blocked on this subaccount)
  7. Returns trade record

IMPORTANT CONSTRAINTS (subaccount permanent limitations):
  - Max leverage = 5x (error -4421 for anything higher)
  - STOP_MARKET / TAKE_PROFIT_MARKET blocked (error -4120)
  - TP workaround: LIMIT + reduceOnly=True
  - SL must be set MANUALLY in Binance app by the user
"""

import os
import time
import json
import hmac
import hashlib
import urllib.request
import urllib.parse
from datetime import datetime

from omnis.config.settings import CONFIG, SystemMode
from omnis.data.binance_client import binance


# ============================================================
# AUTHENTICATED BINANCE CLIENT (for trading operations)
# ============================================================

class BinanceTradingClient:
    """
    Authenticated Binance USD-M Futures client for order placement.
    Reads API keys from environment variables.
    """
    BASE_URL = "https://fapi.binance.com"

    def __init__(self):
        self.api_key = os.environ.get("BINANCE_API_KEY", "")
        self.api_secret = os.environ.get("BINANCE_API_SECRET", "")

    def _sign(self, params: dict) -> str:
        """Generate HMAC-SHA256 signature for authenticated requests."""
        query = urllib.parse.urlencode(params)
        return hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    def _post(self, endpoint: str, params: dict) -> dict:
        """Signed POST request."""
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = self._sign(params)

        data = urllib.parse.urlencode(params).encode("utf-8")
        url = self.BASE_URL + endpoint
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "X-MBX-APIKEY": self.api_key,
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                return json.loads(body)
            except Exception:
                return {"error": body, "code": e.code}
        except Exception as e:
            return {"error": str(e)}

    def _get_signed(self, endpoint: str, params: dict) -> dict:
        """Signed GET request."""
        params["timestamp"] = int(time.time() * 1000)
        params["signature"] = self._sign(params)
        url = self.BASE_URL + endpoint + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url,
            headers={"X-MBX-APIKEY": self.api_key},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except Exception as e:
            return {"error": str(e)}

    def get_account_balance(self) -> list:
        """Get futures account balances."""
        return self._get_signed("/fapi/v3/balance", {})

    def get_positions(self) -> list:
        """Get all open positions."""
        return self._get_signed("/fapi/v2/positionRisk", {})

    def get_usdt_balance(self) -> float:
        """Return available USDT balance for futures trading."""
        balances = self.get_account_balance()
        if isinstance(balances, list):
            for b in balances:
                if b.get("asset") == "USDT":
                    return float(b.get("availableBalance", 0))
        return 0.0

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        """Set leverage for a symbol (max 5x on this subaccount)."""
        leverage = min(leverage, CONFIG.risk.max_leverage)  # never exceed 5x
        return self._post("/fapi/v1/leverage", {
            "symbol": symbol,
            "leverage": leverage,
        })

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> dict:
        """Set margin type (ISOLATED or CROSSED)."""
        return self._post("/fapi/v1/marginType", {
            "symbol": symbol,
            "marginType": margin_type,
        })

    def place_market_order(self, symbol: str, side: str, quantity: float) -> dict:
        """Place a MARKET order for entry."""
        return self._post("/fapi/v1/order", {
            "symbol": symbol,
            "side": side,          # BUY or SELL
            "type": "MARKET",
            "quantity": str(quantity),
        })

    def place_tp_limit_order(self, symbol: str, side: str, quantity: float, price: float) -> dict:
        """
        Place TP as LIMIT + reduceOnly=True.
        STOP_MARKET is blocked on this subaccount (error -4120).
        side: opposite of entry (LONG entry → SELL TP, SHORT entry → BUY TP)
        """
        return self._post("/fapi/v1/order", {
            "symbol": symbol,
            "side": side,
            "type": "LIMIT",
            "quantity": str(quantity),
            "price": str(price),
            "timeInForce": "GTC",
            "reduceOnly": "true",
        })

    def cancel_all_orders(self, symbol: str) -> dict:
        """Cancel all open orders for a symbol."""
        return self._post("/fapi/v1/allOpenOrders", {"symbol": symbol})

    def get_open_orders(self, symbol: str = None) -> list:
        """Get open orders, optionally filtered by symbol."""
        params = {}
        if symbol:
            params["symbol"] = symbol
        return self._get_signed("/fapi/v1/openOrders", params)

    def get_symbol_info(self, symbol: str) -> dict:
        """Get exchange info for symbol (for lot size, price precision)."""
        info = binance.get_exchange_info()
        if "symbols" in info:
            for s in info["symbols"]:
                if s["symbol"] == symbol:
                    return s
        return {}


# Singleton trading client
trading_client = BinanceTradingClient()


# ============================================================
# POSITION SIZE CALCULATOR
# ============================================================

def calculate_position_size(
    symbol: str,
    entry_price: float,
    sl_price: float,
    account_balance: float,
    risk_pct: float = None,
    leverage: int = None,
) -> dict:
    """
    Calculate position size based on risk parameters.

    Parameters
    ----------
    symbol : str
        Trading pair
    entry_price : float
        Expected entry price
    sl_price : float
        Stop loss price (used to compute risk per unit)
    account_balance : float
        Available USDT balance
    risk_pct : float
        Risk as % of capital (default: RiskConfig.max_risk_per_trade_pct)
    leverage : int
        Leverage to use (default: RiskConfig.default_leverage, max 5x)

    Returns
    -------
    dict
        {quantity, notional, risk_usdt, leverage, margin_required, sl_distance_pct}
    """
    risk_pct = risk_pct or CONFIG.risk.max_risk_per_trade_pct
    leverage = min(leverage or CONFIG.risk.default_leverage, CONFIG.risk.max_leverage)

    # Risk amount in USDT
    risk_usdt = account_balance * (risk_pct / 100)

    # Distance from entry to SL
    sl_distance = abs(entry_price - sl_price)
    if sl_distance == 0:
        return {"error": "SL price equals entry price — cannot calculate size"}

    sl_distance_pct = (sl_distance / entry_price) * 100

    # Position size (units of base currency)
    # risk_usdt = quantity * sl_distance  →  quantity = risk_usdt / sl_distance
    quantity = risk_usdt / sl_distance

    # Notional value
    notional = quantity * entry_price

    # Margin required (at leverage)
    margin_required = notional / leverage

    # Safety: don't use more than 25% of balance as margin for one trade
    max_margin = account_balance * 0.25
    if margin_required > max_margin:
        # Scale down proportionally
        scale = max_margin / margin_required
        quantity *= scale
        notional = quantity * entry_price
        margin_required = notional / leverage

    return {
        "symbol": symbol,
        "quantity": round(quantity, 4),
        "notional": round(notional, 2),
        "risk_usdt": round(risk_usdt, 2),
        "leverage": leverage,
        "margin_required": round(margin_required, 2),
        "sl_distance_pct": round(sl_distance_pct, 3),
        "entry_price": entry_price,
        "sl_price": sl_price,
    }


# ============================================================
# SYMBOL PRECISION HELPERS
# ============================================================

def get_symbol_precision(symbol: str) -> dict:
    """
    Get quantity and price precision for a symbol from Binance exchange info.
    Returns {quantity_precision, price_precision, min_qty, step_size}
    """
    info = trading_client.get_symbol_info(symbol)
    result = {
        "quantity_precision": 3,  # default
        "price_precision": 2,     # default
        "min_qty": 0.001,
        "step_size": 0.001,
    }
    if not info:
        return result

    result["quantity_precision"] = info.get("quantityPrecision", 3)
    result["price_precision"] = info.get("pricePrecision", 2)

    for f in info.get("filters", []):
        if f.get("filterType") == "LOT_SIZE":
            result["min_qty"] = float(f.get("minQty", 0.001))
            result["step_size"] = float(f.get("stepSize", 0.001))

    return result


def round_quantity(quantity: float, symbol: str) -> float:
    """Round quantity to valid step size for symbol."""
    prec = get_symbol_precision(symbol)
    step = prec["step_size"]
    if step <= 0:
        return round(quantity, prec["quantity_precision"])
    # Floor to step size
    qty = (quantity // step) * step
    return round(qty, prec["quantity_precision"])


def round_price(price: float, symbol: str) -> float:
    """Round price to valid precision for symbol."""
    prec = get_symbol_precision(symbol)
    return round(price, prec["price_precision"])


# ============================================================
# TRADE EXECUTOR
# ============================================================

class TradeExecutor:
    """
    Autonomous trade executor for OMNIS AI.
    Executes trades according to signal scores and risk constraints.
    """

    def __init__(self):
        self.client = trading_client
        self.risk = CONFIG.risk
        self.mode = CONFIG.mode

    def _is_live(self) -> bool:
        """True only in LIVE_TRADING mode."""
        return self.mode == SystemMode.LIVE_TRADING

    def get_open_position_count(self) -> int:
        """Count symbols with non-zero position size."""
        positions = self.client.get_positions()
        if not isinstance(positions, list):
            return 0
        return sum(1 for p in positions if abs(float(p.get("positionAmt", 0))) > 0)

    def get_position_for_symbol(self, symbol: str) -> dict:
        """Get current position for a specific symbol."""
        positions = self.client.get_positions()
        if isinstance(positions, list):
            for p in positions:
                if p.get("symbol") == symbol:
                    return p
        return {}

    def preflight_checks(self, symbol: str, direction: str) -> dict:
        """
        Run pre-trade checks. Returns {ok: bool, reason: str}.
        Checks:
          - Mode is LIVE_TRADING
          - API keys configured
          - Max position count not exceeded
          - No existing position in same symbol
          - Sufficient balance
        """
        # Mode check
        if not self._is_live():
            return {
                "ok": False,
                "reason": f"System in {self.mode} mode — not LIVE_TRADING. No trades placed.",
            }

        # API keys
        if not self.client.api_key or not self.client.api_secret:
            return {
                "ok": False,
                "reason": "BINANCE_API_KEY or BINANCE_API_SECRET not set in environment.",
            }

        # Max positions
        open_count = self.get_open_position_count()
        if open_count >= self.risk.max_position_count:
            return {
                "ok": False,
                "reason": f"Max positions reached ({open_count}/{self.risk.max_position_count}). Cannot open new trade.",
            }

        # Existing position in same symbol
        existing = self.get_position_for_symbol(symbol)
        if existing and abs(float(existing.get("positionAmt", 0))) > 0:
            return {
                "ok": False,
                "reason": f"Already have open position in {symbol}. Skipping.",
            }

        # Balance check
        balance = self.client.get_usdt_balance()
        if balance < 10:
            return {
                "ok": False,
                "reason": f"Insufficient balance: ${balance:.2f} USDT (min $10 required).",
            }

        return {"ok": True, "reason": "All preflight checks passed", "balance": balance}

    def execute_signal(self, signal: dict, dry_run: bool = False) -> dict:
        """
        Execute a trade based on a scored signal.

        Parameters
        ----------
        signal : dict
            Output from compute_signal() — must have score, direction, symbol, ta
        dry_run : bool
            If True, calculate everything but don't actually place orders

        Returns
        -------
        dict
            Trade record with status, orders, risk details
        """
        symbol = signal.get("symbol", "")
        direction = signal.get("direction", "NEUTRAL")
        score = signal.get("score", 0)
        ta = signal.get("ta", {})

        timestamp = datetime.utcnow().isoformat() + "Z"

        # Only trade LONG or SHORT signals above threshold
        if direction not in ("LONG", "SHORT"):
            return {
                "status": "skipped",
                "reason": f"Direction is {direction} — not tradeable",
                "symbol": symbol,
                "score": score,
                "timestamp": timestamp,
            }

        # Preflight checks
        checks = self.preflight_checks(symbol, direction)
        if not checks["ok"] and not dry_run:
            return {
                "status": "rejected",
                "reason": checks["reason"],
                "symbol": symbol,
                "score": score,
                "timestamp": timestamp,
            }

        # Get current price
        entry_price = ta.get("price", 0)
        if entry_price <= 0:
            entry_price = binance.get_price(symbol)
        if entry_price <= 0:
            return {
                "status": "error",
                "reason": "Could not get entry price",
                "symbol": symbol,
                "timestamp": timestamp,
            }

        # Calculate SL and TP levels
        atr = ta.get("atr", entry_price * 0.02)  # fallback to 2% if no ATR

        if direction == "LONG":
            sl_price = entry_price - (atr * 2.0)   # 2x ATR below entry
            tp_price = entry_price + (atr * 3.0)   # 3x ATR above entry (1.5 R:R)
            order_side = "BUY"
            tp_side = "SELL"
        else:  # SHORT
            sl_price = entry_price + (atr * 2.0)   # 2x ATR above entry
            tp_price = entry_price - (atr * 3.0)   # 3x ATR below entry
            order_side = "SELL"
            tp_side = "BUY"

        # Validate R:R ratio
        risk_dist = abs(entry_price - sl_price)
        reward_dist = abs(tp_price - entry_price)
        rr_ratio = reward_dist / risk_dist if risk_dist > 0 else 0

        if rr_ratio < self.risk.min_rr_ratio:
            return {
                "status": "rejected",
                "reason": f"R:R ratio {rr_ratio:.2f} below minimum {self.risk.min_rr_ratio}",
                "symbol": symbol,
                "score": score,
                "timestamp": timestamp,
            }

        # Get balance
        balance = checks.get("balance") or self.client.get_usdt_balance()

        # Calculate position size
        leverage = CONFIG.risk.default_leverage
        size_info = calculate_position_size(
            symbol=symbol,
            entry_price=entry_price,
            sl_price=sl_price,
            account_balance=balance,
            leverage=leverage,
        )

        if "error" in size_info:
            return {
                "status": "error",
                "reason": size_info["error"],
                "symbol": symbol,
                "timestamp": timestamp,
            }

        # Round to exchange precision
        quantity = round_quantity(size_info["quantity"], symbol)
        tp_price_rounded = round_price(tp_price, symbol)
        sl_price_rounded = round_price(sl_price, symbol)

        if quantity <= 0:
            return {
                "status": "error",
                "reason": f"Calculated quantity {quantity} is zero or negative",
                "symbol": symbol,
                "timestamp": timestamp,
            }

        # Build trade record
        trade_record = {
            "symbol": symbol,
            "direction": direction,
            "score": score,
            "signals": signal.get("signals", []),
            "entry_price": entry_price,
            "sl_price": sl_price_rounded,
            "tp_price": tp_price_rounded,
            "quantity": quantity,
            "leverage": leverage,
            "risk_usdt": size_info["risk_usdt"],
            "notional": size_info["notional"],
            "margin": size_info["margin_required"],
            "rr_ratio": round(rr_ratio, 2),
            "timestamp": timestamp,
            "dry_run": dry_run,
            "mode": self.mode,
        }

        # --- DRY RUN: return plan without executing ---
        if dry_run:
            trade_record["status"] = "dry_run"
            trade_record["note"] = "Orders NOT placed (dry_run=True)"
            return trade_record

        # --- LIVE EXECUTION ---
        # 1. Set leverage (max 5x — subaccount hard limit)
        lev_result = self.client.set_leverage(symbol, leverage)
        trade_record["leverage_result"] = lev_result

        # 2. Set margin type to ISOLATED (best practice for risk control)
        margin_result = self.client.set_margin_type(symbol, "ISOLATED")
        # -4046 = already ISOLATED — not an error
        trade_record["margin_type_result"] = margin_result

        # 3. Place MARKET entry order
        entry_order = self.client.place_market_order(symbol, order_side, quantity)
        trade_record["entry_order"] = entry_order

        if "code" in entry_order and entry_order["code"] < 0:
            trade_record["status"] = "entry_failed"
            trade_record["reason"] = f"Entry order failed: {entry_order}"
            return trade_record

        # 4. Place TP as LIMIT + reduceOnly
        # (STOP_MARKET is blocked on this subaccount — permanent limitation)
        tp_order = self.client.place_tp_limit_order(
            symbol, tp_side, quantity, tp_price_rounded
        )
        trade_record["tp_order"] = tp_order

        # 5. Final status
        if "code" in tp_order and tp_order["code"] < 0:
            trade_record["status"] = "partial"
            trade_record["note"] = (
                f"Entry placed but TP order failed: {tp_order}. "
                "Set TP manually in Binance app."
            )
        else:
            trade_record["status"] = "executed"
            trade_record["note"] = (
                "IMPORTANT: Set SL manually in Binance app! "
                f"SL level: {sl_price_rounded}"
            )

        return trade_record

    def scan_and_trade(
        self,
        signals: list,
        max_trades: int = 2,
        dry_run: bool = False,
    ) -> list:
        """
        Process a list of ranked signals and execute the best ones.

        Parameters
        ----------
        signals : list
            Ranked, filtered signals from filter_tradeable()
        max_trades : int
            Max new trades to open in this scan cycle
        dry_run : bool
            If True, simulate without placing orders

        Returns
        -------
        list
            List of trade records
        """
        results = []
        trades_opened = 0

        for sig in signals:
            if trades_opened >= max_trades:
                break

            # Check open position count before each trade
            open_count = self.get_open_position_count()
            if open_count >= self.risk.max_position_count:
                results.append({
                    "status": "skipped",
                    "reason": f"Max positions reached ({open_count})",
                    "symbol": sig.get("symbol"),
                })
                break

            result = self.execute_signal(sig, dry_run=dry_run)
            results.append(result)

            if result.get("status") in ("executed", "dry_run"):
                trades_opened += 1
                # Small pause between trades
                time.sleep(1)

        return results

    def get_portfolio_status(self) -> dict:
        """
        Get current portfolio overview.
        Returns {positions, total_pnl, balance, open_count}
        """
        balance = self.client.get_usdt_balance()
        positions = self.client.get_positions()

        open_positions = []
        total_unrealized_pnl = 0.0

        if isinstance(positions, list):
            for p in positions:
                amt = float(p.get("positionAmt", 0))
                if abs(amt) > 0:
                    upnl = float(p.get("unrealizedProfit", 0))
                    total_unrealized_pnl += upnl
                    open_positions.append({
                        "symbol": p.get("symbol"),
                        "size": amt,
                        "entry_price": float(p.get("entryPrice", 0)),
                        "mark_price": float(p.get("markPrice", 0)),
                        "unrealized_pnl": round(upnl, 4),
                        "leverage": int(p.get("leverage", 1)),
                        "side": "LONG" if amt > 0 else "SHORT",
                        "liquidation_price": float(p.get("liquidationPrice", 0)),
                    })

        return {
            "balance_usdt": round(balance, 2),
            "open_count": len(open_positions),
            "positions": open_positions,
            "total_unrealized_pnl": round(total_unrealized_pnl, 4),
            "max_positions": self.risk.max_position_count,
            "mode": self.mode,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }


# Singleton executor
executor = TradeExecutor()
