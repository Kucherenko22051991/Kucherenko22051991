"""
OMNIS AI — Autonomous Trading Orchestrator
Main trading loop: scan → score → rank → filter → execute
Runs continuously with configurable cycle interval.
"""
import time
import signal
import logging
import os
from datetime import datetime, date
from typing import Optional

from omnis.config.settings import CONFIG, SystemMode
from omnis.data.market_aggregator import aggregator
from omnis.intelligence.signal_scorer import compute_signal, rank_universe, filter_tradeable
from omnis.execution.trade_executor import executor


# ============================================================
# LOGGING SETUP
# ============================================================

def setup_logging() -> logging.Logger:
    """Configure logging to file and stdout."""
    log_dir = os.path.join(os.path.dirname(__file__), "..", "logs")
    os.makedirs(log_dir, exist_ok=True)

    log_file = os.path.join(
        log_dir,
        f"omnis_{datetime.now().strftime('%Y%m%d')}.log"
    )

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),
        ]
    )
    return logging.getLogger("OMNIS")


logger = setup_logging()


# ============================================================
# ORCHESTRATOR
# ============================================================

class OmnisOrchestrator:
    """
    Master autonomous trading orchestrator.
    Runs continuous scan cycles, scores signals, executes trades.
    """

    SCAN_INTERVAL_SECONDS = 900   # 15 minutes between full scans
    MAX_TRADES_PER_CYCLE = 2      # Max new positions per cycle
    EMERGENCY_DAILY_LOSS_PCT = 5.0  # Trigger EMERGENCY_STOP at 5% daily drawdown

    def __init__(self):
        self._running = False
        self._daily_start_balance: Optional[float] = None
        self._daily_date: Optional[date] = None
        self._cycle_count = 0

        # Register shutdown signals
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    # --------------------------------------------------------
    # SHUTDOWN
    # --------------------------------------------------------

    def _handle_shutdown(self, signum, frame):
        logger.warning("Shutdown signal received — stopping after current cycle.")
        self._running = False

    # --------------------------------------------------------
    # DAILY CIRCUIT BREAKER
    # --------------------------------------------------------

    def _check_daily_circuit_breaker(self) -> bool:
        """
        Returns True if daily loss limit is breached and EMERGENCY_STOP was triggered.
        Resets daily baseline at start of new UTC day.
        """
        today = date.today()

        # Get current balance
        try:
            status = executor.get_portfolio_status()
            current_balance = status.get("total_wallet_balance", 0)
        except Exception as e:
            logger.error(f"Failed to get portfolio status for circuit breaker: {e}")
            return False

        # Reset daily baseline on new day
        if self._daily_date != today:
            self._daily_date = today
            self._daily_start_balance = current_balance
            logger.info(f"Daily baseline reset: ${current_balance:,.2f}")
            return False

        if not self._daily_start_balance or self._daily_start_balance <= 0:
            self._daily_start_balance = current_balance
            return False

        # Check daily drawdown
        daily_pnl_pct = (
            (current_balance - self._daily_start_balance)
            / self._daily_start_balance * 100
        )

        if daily_pnl_pct <= -self.EMERGENCY_DAILY_LOSS_PCT:
            logger.critical(
                f"CIRCUIT BREAKER TRIGGERED: Daily loss {daily_pnl_pct:.2f}% "
                f"exceeds limit of -{self.EMERGENCY_DAILY_LOSS_PCT}%. "
                f"Switching to EMERGENCY_STOP."
            )
            CONFIG.mode = SystemMode.EMERGENCY_STOP
            return True

        logger.info(
            f"Daily PnL: {daily_pnl_pct:+.2f}% "
            f"(balance ${current_balance:,.2f} vs baseline ${self._daily_start_balance:,.2f})"
        )
        return False

    # --------------------------------------------------------
    # MARKET REGIME ADJUSTMENT
    # --------------------------------------------------------

    def _get_max_trades_for_regime(self, regime: dict) -> int:
        """
        Adjust aggressiveness based on market regime.
        In bear_momentum: reduce to 1 trade max.
        In bull_momentum: allow up to MAX_TRADES_PER_CYCLE.
        """
        regime_name = regime.get("regime", "unknown")
        alt_env = regime.get("alt_environment", "neutral")

        if regime_name == "bear_momentum":
            logger.info(f"Regime: {regime_name} — reducing max trades to 1")
            return 1
        elif regime_name == "bull_momentum" and alt_env == "alt_season":
            logger.info(f"Regime: {regime_name} + {alt_env} — full aggression {self.MAX_TRADES_PER_CYCLE}")
            return self.MAX_TRADES_PER_CYCLE
        else:
            logger.info(f"Regime: {regime_name} ({alt_env}) — standard {self.MAX_TRADES_PER_CYCLE}")
            return self.MAX_TRADES_PER_CYCLE

    # --------------------------------------------------------
    # MAIN SCAN CYCLE
    # --------------------------------------------------------

    def _run_scan_cycle(self):
        """Execute one complete scan → score → execute cycle."""
        self._cycle_count += 1
        logger.info(f"=== SCAN CYCLE #{self._cycle_count} STARTED ===")

        # 1. Check circuit breaker
        if self._check_daily_circuit_breaker():
            logger.critical("Circuit breaker tripped — skipping this cycle.")
            return

        # 2. Check system mode
        if CONFIG.mode == SystemMode.EMERGENCY_STOP:
            logger.warning("EMERGENCY_STOP mode active — trading suspended.")
            return

        # 3. Get market regime
        try:
            regime = aggregator.get_market_regime()
            logger.info(
                f"Market regime: {regime.get('regime')} | "
                f"BTC dom: {regime.get('btc_dominance', 0):.1f}% | "
                f"Alt env: {regime.get('alt_environment')}"
            )
        except Exception as e:
            logger.error(f"Failed to get market regime: {e}")
            regime = {}

        max_trades = self._get_max_trades_for_regime(regime)

        # 4. Scan universe for intelligence
        logger.info(f"Scanning {len(CONFIG.trading.universe)} symbols...")
        try:
            intel_list = aggregator.scan_universe(CONFIG.trading.universe)
        except Exception as e:
            logger.error(f"Universe scan failed: {e}")
            return

        # 5. Score each symbol
        scored_signals = []
        for intel in intel_list:
            sym = intel.get("symbol", "?")
            if "error" in intel:
                logger.warning(f"  {sym}: error — {intel['error']}")
                continue
            try:
                signal = compute_signal(
                    symbol=sym,
                    candles=intel.get("candles", []),
                    lunarcrush_data=intel.get("lunarcrush_data"),
                    derivatives_data=intel.get("derivatives_data"),
                    momentum_data=intel.get("momentum_data"),
                )
                # Attach trending bonus
                if intel.get("is_trending"):
                    signal["score"] = min(100, signal["score"] + 3)
                    signal["signals"].append("CoinGecko trending")

                scored_signals.append(signal)
                logger.debug(f"  {sym}: score={signal['score']:.1f} dir={signal['direction']}")
            except Exception as e:
                logger.error(f"  {sym}: scoring error — {e}")

        if not scored_signals:
            logger.warning("No scored signals — skipping execution.")
            return

        # 6. Rank and filter
        ranked = rank_universe(scored_signals)
        tradeable = filter_tradeable(ranked, min_score=CONFIG.trading.min_signal_score)

        logger.info(
            f"Signals: {len(scored_signals)} scored, "
            f"{len(tradeable)} above threshold ({CONFIG.trading.min_signal_score})"
        )

        for s in ranked[:5]:
            logger.info(
                f"  {s['symbol']}: {s['score']:.1f} [{s['direction']}] "
                f"signals={s['signals'][:3]}"
            )

        # 7. Execute (dry_run if not LIVE_TRADING)
        is_live = CONFIG.mode == SystemMode.LIVE_TRADING
        dry_run = not is_live

        if dry_run:
            logger.info(
                f"Mode={CONFIG.mode.value} — dry_run=True "
                f"(set mode=LIVE_TRADING for real orders)"
            )

        if tradeable:
            try:
                results = executor.scan_and_trade(
                    tradeable,
                    max_trades=max_trades,
                    dry_run=dry_run,
                )
                for r in results:
                    if r.get("success"):
                        logger.info(
                            f"  TRADE {'[DRY]' if dry_run else '[LIVE]'}: "
                            f"{r.get('symbol')} {r.get('direction')} "
                            f"qty={r.get('quantity')} "
                            f"entry={r.get('entry_price')} "
                            f"sl={r.get('sl_price')} "
                            f"tp={r.get('tp_price')}"
                        )
                    else:
                        logger.warning(
                            f"  TRADE SKIPPED: {r.get('symbol')} — {r.get('reason')}"
                        )
            except Exception as e:
                logger.error(f"Execution error: {e}")
        else:
            logger.info("No tradeable signals this cycle — holding.")

        logger.info(f"=== SCAN CYCLE #{self._cycle_count} COMPLETE ===\n")

    # --------------------------------------------------------
    # PORTFOLIO STATUS LOG
    # --------------------------------------------------------

    def _log_portfolio_status(self):
        """Log current portfolio every cycle."""
        try:
            status = executor.get_portfolio_status()
            positions = status.get("positions", [])
            logger.info(
                f"Portfolio: balance=${status.get('total_wallet_balance', 0):,.2f} "
                f"unrealizedPnL=${status.get('total_unrealized_pnl', 0):+,.2f} "
                f"positions={len(positions)}"
            )
            for p in positions:
                logger.info(
                    f"  {p['symbol']}: {p['side']} qty={p['positionAmt']} "
                    f"entry={p['entryPrice']} pnl=${p['unrealizedProfit']:+.2f}"
                )
        except Exception as e:
            logger.error(f"Failed to log portfolio status: {e}")

    # --------------------------------------------------------
    # MAIN LOOP
    # --------------------------------------------------------

    def run(self):
        """Start the continuous autonomous trading loop."""
        logger.info("=" * 60)
        logger.info("OMNIS AI — Autonomous Trading Orchestrator STARTED")
        logger.info(f"Mode: {CONFIG.mode.value}")
        logger.info(f"Universe: {CONFIG.trading.universe}")
        logger.info(f"Scan interval: {self.SCAN_INTERVAL_SECONDS}s")
        logger.info(f"Min signal score: {CONFIG.trading.min_signal_score}")
        logger.info(f"Max concurrent positions: {CONFIG.risk.max_position_count}")
        logger.info("=" * 60)

        self._running = True

        while self._running:
            cycle_start = time.time()

            try:
                self._log_portfolio_status()
                self._run_scan_cycle()
            except Exception as e:
                logger.error(f"Unhandled error in scan cycle: {e}", exc_info=True)

            # Sleep until next cycle (accounting for execution time)
            elapsed = time.time() - cycle_start
            sleep_time = max(0, self.SCAN_INTERVAL_SECONDS - elapsed)

            if self._running:
                logger.info(
                    f"Next scan in {sleep_time:.0f}s "
                    f"(cycle took {elapsed:.1f}s)"
                )
                # Sleep in 10s chunks to allow clean shutdown
                slept = 0
                while slept < sleep_time and self._running:
                    time.sleep(min(10, sleep_time - slept))
                    slept += 10

        logger.info("OMNIS AI Orchestrator stopped gracefully.")

    def run_once(self):
        """Run a single scan cycle (useful for testing/cron)."""
        logger.info("OMNIS AI — Single scan cycle")
        self._log_portfolio_status()
        self._run_scan_cycle()


# ============================================================
# ENTRY POINT
# ============================================================

def main():
    """Main entry point."""
    import sys

    # Allow overriding mode from CLI: python -m omnis.core.orchestrator live
    if len(sys.argv) > 1:
        mode_arg = sys.argv[1].upper()
        if mode_arg == "LIVE":
            CONFIG.mode = SystemMode.LIVE_TRADING
            logger.info("Mode overridden to LIVE_TRADING via CLI argument.")
        elif mode_arg == "PAPER":
            CONFIG.mode = SystemMode.PAPER_TRADING
        elif mode_arg == "ONCE":
            # Single scan cycle then exit
            orch = OmnisOrchestrator()
            orch.run_once()
            return

    orch = OmnisOrchestrator()
    orch.run()


if __name__ == "__main__":
    main()
