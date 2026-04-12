"""
Market Flow Data Collector Service

Collects real-time market flow data from Hyperliquid using native SDK WebSocket:
- Trades (for CVD, Taker Volume)
- L2 Orderbook (for Depth Ratio, Liquidity)
- Asset Context (for OI, Funding Rate, Premium)

Data is aggregated in 15-second windows and persisted to database.
"""

import json
import time
import logging
import threading
from decimal import Decimal
from typing import Dict, List, Optional, Any
from collections import defaultdict
from dataclasses import dataclass, field

from hyperliquid.info import Info

from services.exchanges.symbol_mapper import SymbolMapper
from services.large_order_threshold_tracker import LargeOrderThresholdTracker

logger = logging.getLogger(__name__)

# Aggregation window in seconds
AGGREGATION_WINDOW_SECONDS = 15

# Connection health check settings
HEALTH_CHECK_INTERVAL_SECONDS = 30
DATA_STALE_THRESHOLD_SECONDS = 30  # Consider data stale if no update for 30s
MAX_RECONNECT_ATTEMPTS = 5
RECONNECT_BASE_DELAY_SECONDS = 5

# Degraded mode settings (infinite retry with longer intervals)
DEGRADED_MODE_RETRY_INTERVAL_SECONDS = 120  # 2 minutes between retries
DEGRADED_MODE_LOG_INTERVAL = 5  # Log warning every 5 failed attempts


@dataclass
class TradeBuffer:
    """Buffer for aggregating trades within a time window"""
    taker_buy_volume: Decimal = Decimal("0")
    taker_sell_volume: Decimal = Decimal("0")
    taker_buy_count: int = 0
    taker_sell_count: int = 0
    taker_buy_notional: Decimal = Decimal("0")
    taker_sell_notional: Decimal = Decimal("0")
    large_buy_notional: Decimal = Decimal("0")
    large_sell_notional: Decimal = Decimal("0")
    large_buy_count: int = 0
    large_sell_count: int = 0
    high_price: Optional[Decimal] = None
    low_price: Optional[Decimal] = None
    total_volume: Decimal = Decimal("0")
    total_notional: Decimal = Decimal("0")

    def reset(self):
        """Reset buffer for next window"""
        self.taker_buy_volume = Decimal("0")
        self.taker_sell_volume = Decimal("0")
        self.taker_buy_count = 0
        self.taker_sell_count = 0
        self.taker_buy_notional = Decimal("0")
        self.taker_sell_notional = Decimal("0")
        self.large_buy_notional = Decimal("0")
        self.large_sell_notional = Decimal("0")
        self.large_buy_count = 0
        self.large_sell_count = 0
        self.high_price = None
        self.low_price = None
        self.total_volume = Decimal("0")
        self.total_notional = Decimal("0")


class MarketFlowCollector:
    """
    Singleton service for collecting market flow data via WebSocket.
    Aggregates data in 15-second windows and persists to database.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self.info: Optional[Info] = None
        self.running = False
        self.subscribed_symbols: List[str] = []
        self.subscription_ids: Dict[str, Dict[str, int]] = defaultdict(dict)

        # Data buffers
        self.trade_buffers: Dict[str, TradeBuffer] = {}
        self.latest_orderbook: Dict[str, Any] = {}
        self.latest_asset_ctx: Dict[str, Any] = {}

        # Data freshness tracking (timestamp of last update for each data source)
        self.last_update_time: Dict[str, float] = {
            "l2book": 0.0,
            "asset_ctx": 0.0,
            "trades": 0.0,
        }

        # Timing
        self.last_flush_time = time.time()
        self.flush_thread: Optional[threading.Thread] = None
        self.health_thread: Optional[threading.Thread] = None
        self.flush_wakeup = threading.Event()
        self.health_wakeup = threading.Event()
        self.flush_in_progress = threading.Lock()
        self.active_flush_started_at: Optional[float] = None

        # Reconnection state
        self.reconnect_attempts = 0
        self.is_reconnecting = False
        self.reconnect_lock = threading.Lock()

        # Degraded mode state (infinite retry when normal retries exhausted)
        self.degraded_mode = False
        self.degraded_retry_count = 0
        self.next_degraded_retry_at: Optional[float] = None

        # Thread safety
        self.buffer_lock = threading.Lock()
        self.large_order_tracker = LargeOrderThresholdTracker(exchange="hyperliquid")

        logger.info("MarketFlowCollector initialized")

    def _create_info_client(self, base_url: str) -> Info:
        """Create SDK Info, falling back to standard perps if HIP-3 metadata fails."""
        try:
            return Info(base_url=base_url, skip_ws=False, perp_dexs=['', 'xyz'])
        except Exception as hip3_error:
            logger.warning(
                "Failed to load HIP-3 metadata for market flow collector; "
                "falling back to standard perps only: %s",
                hip3_error,
            )
            return Info(base_url=base_url, skip_ws=False, perp_dexs=[''])

    def start(self, symbols: Optional[List[str]] = None):
        """Start the collector with given symbols or from watchlist"""
        if self.running:
            logger.warning("MarketFlowCollector already running")
            return

        # Get symbols from watchlist if not provided
        if symbols is None:
            from services.hyperliquid_symbol_service import get_selected_symbols
            symbols = get_selected_symbols()

        if not symbols:
            logger.warning("No symbols to monitor, collector not started")
            return

        # Store symbols for retry
        self._pending_symbols = symbols
        self.reconnect_attempts = 0
        self.large_order_tracker.initialize_from_history(symbols)

        # Try to start with retry logic
        self._start_with_retry()

    def _start_with_retry(self):
        """Internal method to start collector with retry on failure"""
        symbols = getattr(self, '_pending_symbols', None)
        if not symbols:
            logger.warning("No pending symbols for retry")
            return

        try:
            base_url = "https://api.hyperliquid.xyz"
            logger.info(f"[Start] Connecting to Hyperliquid API: {base_url}")
            self.info = self._create_info_client(base_url)

            self.running = True
            self.subscribed_symbols = []
            self.reconnect_attempts = 0

            for symbol in symbols:
                self._subscribe_symbol(symbol)

            self._ensure_worker_threads()

            logger.info(f"MarketFlowCollector started with symbols: {symbols}")

        except Exception as e:
            logger.error(f"Failed to start MarketFlowCollector: {e}", exc_info=True)
            self.running = False

            self.reconnect_attempts += 1
            if self.reconnect_attempts <= MAX_RECONNECT_ATTEMPTS:
                delay = RECONNECT_BASE_DELAY_SECONDS * (2 ** (self.reconnect_attempts - 1))
                logger.warning(
                    f"[Start] Will retry in {delay}s "
                    f"(attempt {self.reconnect_attempts}/{MAX_RECONNECT_ATTEMPTS})"
                )
                retry_timer = threading.Timer(delay, self._start_with_retry)
                retry_timer.daemon = True
                retry_timer.start()
            else:
                logger.error(
                    f"[Start] FAILED after {MAX_RECONNECT_ATTEMPTS} attempts. "
                    f"Manual restart required!"
                )

    def stop(self):
        """Stop the collector and cleanup"""
        if not self.running:
            return

        self.running = False
        self.flush_wakeup.set()
        self.health_wakeup.set()

        if self.flush_thread and self.flush_thread.is_alive():
            self.flush_thread.join(timeout=5)
        self.flush_thread = None

        if self.health_thread and self.health_thread.is_alive():
            self.health_thread.join(timeout=5)
        self.health_thread = None

        # Reset degraded mode state
        self.degraded_mode = False
        self.degraded_retry_count = 0
        self.next_degraded_retry_at = None

        # Flush remaining data
        self._flush_to_database()

        # Unsubscribe all
        for symbol in list(self.subscribed_symbols):
            self._unsubscribe_symbol(symbol)

        # Disconnect WebSocket
        self._cleanup_old_connection()
        logger.info("MarketFlowCollector stopped")

    def refresh_subscriptions(self, new_symbols: List[str]):
        """Update subscriptions when watchlist changes"""
        if not self.running:
            return

        current = set(self.subscribed_symbols)
        new = set(new_symbols)

        # Unsubscribe removed symbols
        for symbol in current - new:
            self._unsubscribe_symbol(symbol)

        # Subscribe new symbols
        for symbol in new - current:
            self._subscribe_symbol(symbol)

    def _get_original_coin_name(self, symbol: str) -> str:
        """Get the original coin name from Hyperliquid SDK (case-sensitive).

        Hyperliquid uses mixed-case names like 'kSHIB', 'kPEPE', but our watchlist
        stores uppercase versions. This method finds the original name.
        """
        if not self.info:
            return symbol

        # Try exact match first
        if symbol in self.info.name_to_coin:
            return symbol

        # Try case-insensitive match
        symbol_upper = symbol.upper()
        for coin in self.info.name_to_coin:
            if coin.upper() == symbol_upper:
                return coin

        return symbol

    def _subscribe_symbol(self, symbol: str):
        """Subscribe to all data streams for a symbol"""
        if not self.info:
            return

        # Convert to original Hyperliquid coin name (e.g., KSHIB -> kSHIB, GOLD -> xyz:GOLD)
        exchange_symbol = SymbolMapper.to_exchange(symbol, "hyperliquid")
        coin = self._get_original_coin_name(exchange_symbol)

        try:
            # Initialize buffer (use original symbol for internal tracking)
            self.trade_buffers[symbol] = TradeBuffer()
            self.large_order_tracker.ensure_symbols([symbol])

            # Subscribe to trades
            trades_id = self.info.subscribe(
                {"type": "trades", "coin": coin},
                lambda msg, s=symbol: self._on_trades(s, msg)
            )
            self.subscription_ids[symbol]["trades"] = trades_id

            # Subscribe to L2 orderbook
            l2_id = self.info.subscribe(
                {"type": "l2Book", "coin": coin},
                lambda msg, s=symbol: self._on_l2book(s, msg)
            )
            self.subscription_ids[symbol]["l2Book"] = l2_id

            # Subscribe to asset context (OI, funding, etc.)
            ctx_id = self.info.subscribe(
                {"type": "activeAssetCtx", "coin": coin},
                lambda msg, s=symbol: self._on_asset_ctx(s, msg)
            )
            self.subscription_ids[symbol]["activeAssetCtx"] = ctx_id

            self.subscribed_symbols.append(symbol)
            logger.info(f"Subscribed to market flow data for {symbol} (coin: {coin})")

        except Exception as e:
            logger.error(f"Failed to subscribe {symbol}: {e}")

    def _unsubscribe_symbol(self, symbol: str):
        """Unsubscribe from all data streams for a symbol"""
        if not self.info or symbol not in self.subscription_ids:
            return

        # Convert to original Hyperliquid coin name
        exchange_symbol = SymbolMapper.to_exchange(symbol, "hyperliquid")
        coin = self._get_original_coin_name(exchange_symbol)

        try:
            ids = self.subscription_ids[symbol]

            if "trades" in ids:
                self.info.unsubscribe({"type": "trades", "coin": coin}, ids["trades"])
            if "l2Book" in ids:
                self.info.unsubscribe({"type": "l2Book", "coin": coin}, ids["l2Book"])
            if "activeAssetCtx" in ids:
                self.info.unsubscribe({"type": "activeAssetCtx", "coin": coin}, ids["activeAssetCtx"])

            del self.subscription_ids[symbol]
            if symbol in self.subscribed_symbols:
                self.subscribed_symbols.remove(symbol)
            if symbol in self.trade_buffers:
                del self.trade_buffers[symbol]

            logger.info(f"Unsubscribed from {symbol}")

        except Exception as e:
            logger.error(f"Failed to unsubscribe {symbol}: {e}")

    def _on_trades(self, symbol: str, msg: dict):
        """Handle incoming trade messages"""
        try:
            if msg.get("channel") != "trades":
                return

            trades = msg.get("data", [])
            if not trades:
                return

            # Update freshness timestamp
            self.last_update_time["trades"] = time.time()

            with self.buffer_lock:
                buffer = self.trade_buffers.get(symbol)
                if not buffer:
                    return

                for trade in trades:
                    # SDK returns: coin, side (A=ask/sell, B=bid/buy), px, sz, hash, time
                    price = Decimal(str(trade["px"]))
                    size = Decimal(str(trade["sz"]))
                    side = trade["side"]  # "A" = taker sell, "B" = taker buy
                    notional = price * size
                    notional_float = float(notional)
                    # Classify before updating the tracker so the current trade
                    # does not immediately move its own threshold.
                    is_large = self.large_order_tracker.is_large_order(symbol, notional_float)
                    self.large_order_tracker.update(symbol, notional_float)

                    # Update buffer
                    if side == "B":  # Taker buy
                        buffer.taker_buy_volume += size
                        buffer.taker_buy_count += 1
                        buffer.taker_buy_notional += notional
                        if is_large:
                            buffer.large_buy_notional += notional
                            buffer.large_buy_count += 1
                    else:  # Taker sell (side == "A")
                        buffer.taker_sell_volume += size
                        buffer.taker_sell_count += 1
                        buffer.taker_sell_notional += notional
                        if is_large:
                            buffer.large_sell_notional += notional
                            buffer.large_sell_count += 1

                    buffer.total_volume += size
                    buffer.total_notional += notional

                    # Track high/low
                    if buffer.high_price is None or price > buffer.high_price:
                        buffer.high_price = price
                    if buffer.low_price is None or price < buffer.low_price:
                        buffer.low_price = price

        except Exception as e:
            logger.error(f"Error processing trades for {symbol}: {e}")

    def _on_l2book(self, symbol: str, msg: dict):
        """Handle incoming L2 orderbook messages"""
        try:
            if msg.get("channel") != "l2Book":
                return

            data = msg.get("data", {})
            if data:
                self.latest_orderbook[symbol] = data
                # Update freshness timestamp
                self.last_update_time["l2book"] = time.time()

        except Exception as e:
            logger.error(f"Error processing l2book for {symbol}: {e}")

    def _on_asset_ctx(self, symbol: str, msg: dict):
        """Handle incoming asset context messages"""
        try:
            channel = msg.get("channel")
            if channel not in ("activeAssetCtx", "activeSpotAssetCtx"):
                return

            data = msg.get("data", {})
            if data:
                self.latest_asset_ctx[symbol] = data
                # Update freshness timestamp
                self.last_update_time["asset_ctx"] = time.time()

        except Exception as e:
            logger.error(f"Error processing asset ctx for {symbol}: {e}")

    def _ensure_worker_threads(self):
        """Ensure flush/health workers exist exactly once."""
        if not self.flush_thread or not self.flush_thread.is_alive():
            self.flush_wakeup.clear()
            self.flush_thread = threading.Thread(
                target=self._flush_worker_loop,
                daemon=True,
                name="market-flow-flush",
            )
            self.flush_thread.start()
            logger.info("[Collector] Flush worker started")

        if not self.health_thread or not self.health_thread.is_alive():
            self.health_wakeup.clear()
            self.health_thread = threading.Thread(
                target=self._health_worker_loop,
                daemon=True,
                name="market-flow-health",
            )
            self.health_thread.start()
            logger.info("[Collector] Health worker started")

    def _flush_worker_loop(self):
        """Persist data on 15-second boundaries using a single worker thread."""
        while self.running:
            now = time.time()
            next_boundary = (int(now) // AGGREGATION_WINDOW_SECONDS + 1) * AGGREGATION_WINDOW_SECONDS
            delay = max(0.1, next_boundary - now)
            if self.flush_wakeup.wait(delay):
                self.flush_wakeup.clear()
                continue
            self._flush_once()

    def _health_worker_loop(self):
        """Monitor connection health and degraded-mode retries using a single worker thread."""
        while self.running:
            wait_seconds = HEALTH_CHECK_INTERVAL_SECONDS
            if self.degraded_mode and self.next_degraded_retry_at:
                wait_seconds = max(1.0, min(wait_seconds, self.next_degraded_retry_at - time.time()))
            if self.health_wakeup.wait(wait_seconds):
                self.health_wakeup.clear()
                continue
            if not self.running:
                break
            if self.degraded_mode and self.next_degraded_retry_at and time.time() >= self.next_degraded_retry_at:
                self._reconnect()
            else:
                self._check_connection_health()

    def _check_connection_health(self):
        """Check if WebSocket data is stale and trigger reconnect if needed"""
        if self.is_reconnecting:
            logger.debug("Health check skipped - reconnection in progress")
            return

        if self.degraded_mode:
            return

        now = time.time()
        # Check l2book and asset_ctx freshness (these should update frequently)
        l2book_age = now - self.last_update_time["l2book"] if self.last_update_time["l2book"] > 0 else -1
        asset_ctx_age = now - self.last_update_time["asset_ctx"] if self.last_update_time["asset_ctx"] > 0 else -1
        trades_age = now - self.last_update_time["trades"] if self.last_update_time["trades"] > 0 else -1

        # Log current health status
        logger.info(
            f"[HealthCheck] Data freshness - l2book: {l2book_age:.0f}s, "
            f"asset_ctx: {asset_ctx_age:.0f}s, trades: {trades_age:.0f}s "
            f"(threshold: {DATA_STALE_THRESHOLD_SECONDS}s)"
        )

        # If both are stale, connection is likely dead
        if (self.last_update_time["l2book"] > 0 and l2book_age > DATA_STALE_THRESHOLD_SECONDS and
            self.last_update_time["asset_ctx"] > 0 and asset_ctx_age > DATA_STALE_THRESHOLD_SECONDS):
            logger.warning(
                f"[HealthCheck] STALE DATA DETECTED! WebSocket likely disconnected. "
                f"l2book: {l2book_age:.0f}s ago, asset_ctx: {asset_ctx_age:.0f}s ago. "
                f"Initiating reconnect..."
            )
            self._reconnect()

    def _reconnect(self):
        """Reconnect WebSocket with exponential backoff, then degraded mode"""
        with self.reconnect_lock:
            if self.is_reconnecting:
                logger.debug("[Reconnect] Already reconnecting, skipping")
                return
            self.is_reconnecting = True

        try:
            # Check if we should enter or continue degraded mode
            if self.reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
                if not self.degraded_mode:
                    # First time entering degraded mode
                    self.degraded_mode = True
                    self.degraded_retry_count = 0
                    logger.warning(
                        f"[Reconnect] Normal retries exhausted ({MAX_RECONNECT_ATTEMPTS}). "
                        f"Entering DEGRADED MODE - will retry every "
                        f"{DEGRADED_MODE_RETRY_INTERVAL_SECONDS}s indefinitely."
                    )

                self.degraded_retry_count += 1
                self.next_degraded_retry_at = time.time() + DEGRADED_MODE_RETRY_INTERVAL_SECONDS
                # Log every DEGRADED_MODE_LOG_INTERVAL attempts
                if self.degraded_retry_count % DEGRADED_MODE_LOG_INTERVAL == 1:
                    logger.warning(
                        f"[Reconnect] DEGRADED MODE attempt #{self.degraded_retry_count} "
                        f"(logging every {DEGRADED_MODE_LOG_INTERVAL} attempts)"
                    )
            else:
                # Normal mode with exponential backoff
                self.reconnect_attempts += 1
                delay = RECONNECT_BASE_DELAY_SECONDS * (2 ** (self.reconnect_attempts - 1))
                logger.warning(
                    f"[Reconnect] Attempt {self.reconnect_attempts}/{MAX_RECONNECT_ATTEMPTS} "
                    f"starting after {delay}s delay..."
                )
                time.sleep(delay)

            # Save current symbols (use _pending_symbols as fallback)
            symbols_to_restore = list(self.subscribed_symbols) if self.subscribed_symbols else \
                                 getattr(self, '_pending_symbols', [])
            logger.info(f"[Reconnect] Will restore {len(symbols_to_restore)} symbols")

            # Disconnect old WebSocket and clean up
            self._cleanup_old_connection()

            # Create new Info client
            logger.info("[Reconnect] Creating new Hyperliquid Info client...")
            base_url = "https://api.hyperliquid.xyz"
            self.info = self._create_info_client(base_url)
            logger.info("[Reconnect] New Info client created")

            # Resubscribe to all symbols
            for symbol in symbols_to_restore:
                self._subscribe_symbol(symbol)

            # SUCCESS - reset all reconnection state
            self.reconnect_attempts = 0
            self.degraded_mode = False
            self.degraded_retry_count = 0
            self.next_degraded_retry_at = None
            now = time.time()
            self.last_update_time["l2book"] = now
            self.last_update_time["asset_ctx"] = now
            self.last_update_time["trades"] = now

            self.flush_wakeup.set()
            self.health_wakeup.set()

            logger.warning(
                f"[Reconnect] SUCCESS! Resubscribed to {len(symbols_to_restore)} symbols. "
                f"Data collection resumed."
            )

        except Exception as e:
            logger.error(f"Reconnect failed: {e}", exc_info=True)
            # Ensure info is None on failure to avoid using corrupted object
            self.info = None
            if self.degraded_mode and self.next_degraded_retry_at is None:
                self.next_degraded_retry_at = time.time() + DEGRADED_MODE_RETRY_INTERVAL_SECONDS
        finally:
            self.is_reconnecting = False

    def _cleanup_old_connection(self):
        """Clean up old WebSocket connection and subscription state"""
        if self.info and self.info.ws_manager:
            ws_manager = self.info.ws_manager
            try:
                self.info.disconnect_websocket()
                logger.info("[Reconnect] Old WebSocket disconnected")
            except Exception as e:
                logger.warning(f"[Reconnect] Error disconnecting old websocket: {e}")
            try:
                ws_manager.join(timeout=5)
                if ws_manager.is_alive():
                    logger.warning(
                        "[Reconnect] Old WebSocket manager did not exit cleanly; "
                        "threads=%s symbols=%s",
                        len(threading.enumerate()),
                        list(self.subscribed_symbols),
                    )
            except Exception as e:
                logger.warning(f"[Reconnect] Error joining old websocket manager: {e}")
        self.info = None
        self.subscribed_symbols = []
        self.subscription_ids.clear()

    def _flush_once(self):
        """Run at most one flush at a time and emit diagnostics for abnormal delays."""
        if not self.flush_in_progress.acquire(blocking=False):
            if self.active_flush_started_at:
                logger.warning(
                    "[Flush] Previous flush still running; skipping this window. "
                    "duration=%.2fs threads=%s symbols=%s",
                    time.time() - self.active_flush_started_at,
                    len(threading.enumerate()),
                    list(self.subscribed_symbols),
                )
            return

        self.active_flush_started_at = time.time()
        try:
            self._flush_to_database()
            duration = time.time() - self.active_flush_started_at
            if duration > AGGREGATION_WINDOW_SECONDS:
                logger.warning(
                    "[Flush] Slow flush detected: duration=%.2fs threads=%s symbols=%s",
                    duration,
                    len(threading.enumerate()),
                    list(self.subscribed_symbols),
                )
        finally:
            self.active_flush_started_at = None
            self.flush_in_progress.release()

    def _flush_to_database(self):
        """Flush all buffered data to database"""
        if not self.subscribed_symbols:
            return

        timestamp_ms = int(time.time() * 1000)
        # Align to 15-second boundary
        timestamp_ms = (timestamp_ms // (AGGREGATION_WINDOW_SECONDS * 1000)) * (AGGREGATION_WINDOW_SECONDS * 1000)

        try:
            from database.connection import SessionLocal
            from database.models import MarketTradesAggregated, MarketOrderbookSnapshots, MarketAssetMetrics

            db = SessionLocal()
            try:
                for symbol in self.subscribed_symbols:
                    self._flush_trades(db, symbol, timestamp_ms)
                    self._flush_orderbook(db, symbol, timestamp_ms)
                    self._flush_asset_metrics(db, symbol, timestamp_ms)

                db.commit()
                logger.debug(f"Flushed market flow data for {len(self.subscribed_symbols)} symbols")

                # Run signal detection after data flush
                self._run_signal_detection()

            except Exception as e:
                db.rollback()
                logger.error(f"Failed to flush market flow data: {e}")
            finally:
                db.close()

        except Exception as e:
            logger.error(f"Database error in flush: {e}")

    def _run_signal_detection(self):
        """Run signal detection for all subscribed symbols (Hyperliquid only)"""
        try:
            from services.signal_detection_service import signal_detection_service

            for symbol in self.subscribed_symbols:
                # Build market data context for signal detection
                market_data = {
                    "asset_ctx": self.latest_asset_ctx.get(symbol, {}),
                    "orderbook": self.latest_orderbook.get(symbol, {}),
                }

                # Detect signals for Hyperliquid pools only
                triggered = signal_detection_service.detect_signals(
                    symbol, market_data, exchange="hyperliquid"
                )
                if triggered:
                    logger.info(f"Pools triggered for {symbol}: {[p['pool_name'] for p in triggered]}")

        except Exception as e:
            logger.error(f"Error in signal detection: {e}", exc_info=True)

    def _flush_trades(self, db, symbol: str, timestamp_ms: int):
        """Flush trade buffer for a symbol using native PostgreSQL upsert"""
        with self.buffer_lock:
            buffer = self.trade_buffers.get(symbol)
            if not buffer or buffer.total_volume == 0:
                return

            vwap = None
            if buffer.total_volume > 0:
                vwap = buffer.total_notional / buffer.total_volume

            from sqlalchemy.dialects.postgresql import insert as pg_insert
            from database.models import MarketTradesAggregated

            values = dict(
                exchange="hyperliquid", symbol=symbol, timestamp=timestamp_ms,
                taker_buy_volume=buffer.taker_buy_volume,
                taker_sell_volume=buffer.taker_sell_volume,
                taker_buy_count=buffer.taker_buy_count,
                taker_sell_count=buffer.taker_sell_count,
                taker_buy_notional=buffer.taker_buy_notional,
                taker_sell_notional=buffer.taker_sell_notional,
                large_buy_notional=buffer.large_buy_notional,
                large_sell_notional=buffer.large_sell_notional,
                large_buy_count=buffer.large_buy_count,
                large_sell_count=buffer.large_sell_count,
                vwap=vwap, high_price=buffer.high_price, low_price=buffer.low_price,
            )
            update_cols = {k: v for k, v in values.items() if k not in ("exchange", "symbol", "timestamp")}
            stmt = pg_insert(MarketTradesAggregated).values(**values).on_conflict_do_update(
                index_elements=["exchange", "symbol", "timestamp"],
                set_=update_cols,
            )
            db.execute(stmt)
            buffer.reset()

    def _flush_orderbook(self, db, symbol: str, timestamp_ms: int):
        """Flush orderbook snapshot for a symbol using native PostgreSQL upsert"""
        # Skip if data is stale (WebSocket disconnected)
        l2book_age = time.time() - self.last_update_time["l2book"]
        if self.last_update_time["l2book"] > 0 and l2book_age > DATA_STALE_THRESHOLD_SECONDS:
            logger.warning(f"[StaleData] Skipping orderbook flush for {symbol} - data is {l2book_age:.0f}s old")
            return

        data = self.latest_orderbook.get(symbol)
        if not data:
            return

        try:
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            from database.models import MarketOrderbookSnapshots

            levels = data.get("levels", [[], []])
            bids = levels[0] if len(levels) > 0 else []
            asks = levels[1] if len(levels) > 1 else []

            best_bid = Decimal(bids[0]["px"]) if bids else None
            best_ask = Decimal(asks[0]["px"]) if asks else None
            spread = (best_ask - best_bid) if (best_bid and best_ask) else None

            bid_depth_5 = sum(Decimal(b["sz"]) for b in bids[:5])
            ask_depth_5 = sum(Decimal(a["sz"]) for a in asks[:5])
            bid_depth_10 = sum(Decimal(b["sz"]) for b in bids[:10])
            ask_depth_10 = sum(Decimal(a["sz"]) for a in asks[:10])

            bid_orders = sum(b.get("n", 1) for b in bids)
            ask_orders = sum(a.get("n", 1) for a in asks)

            values = dict(
                exchange="hyperliquid", symbol=symbol, timestamp=timestamp_ms,
                best_bid=best_bid, best_ask=best_ask, spread=spread,
                bid_depth_5=bid_depth_5, ask_depth_5=ask_depth_5,
                bid_depth_10=bid_depth_10, ask_depth_10=ask_depth_10,
                bid_orders_count=bid_orders, ask_orders_count=ask_orders,
                raw_levels=json.dumps(levels),
            )
            update_cols = {k: v for k, v in values.items() if k not in ("exchange", "symbol", "timestamp")}
            stmt = pg_insert(MarketOrderbookSnapshots).values(**values).on_conflict_do_update(
                index_elements=["exchange", "symbol", "timestamp"],
                set_=update_cols,
            )
            db.execute(stmt)

        except Exception as e:
            logger.error(f"Error flushing orderbook for {symbol}: {e}")

    def _flush_asset_metrics(self, db, symbol: str, timestamp_ms: int):
        """Flush asset metrics for a symbol using native PostgreSQL upsert"""
        # Skip if data is stale (WebSocket disconnected)
        asset_ctx_age = time.time() - self.last_update_time["asset_ctx"]
        if self.last_update_time["asset_ctx"] > 0 and asset_ctx_age > DATA_STALE_THRESHOLD_SECONDS:
            logger.warning(f"[StaleData] Skipping asset metrics flush for {symbol} - data is {asset_ctx_age:.0f}s old")
            return

        data = self.latest_asset_ctx.get(symbol)
        if not data:
            return

        try:
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            from database.models import MarketAssetMetrics

            ctx = data.get("ctx", {})
            values = dict(
                exchange="hyperliquid", symbol=symbol, timestamp=timestamp_ms,
                open_interest=Decimal(ctx["openInterest"]) if ctx.get("openInterest") else None,
                funding_rate=Decimal(ctx["funding"]) if ctx.get("funding") else None,
                mark_price=Decimal(ctx["markPx"]) if ctx.get("markPx") else None,
                oracle_price=Decimal(ctx["oraclePx"]) if ctx.get("oraclePx") else None,
                mid_price=Decimal(ctx["midPx"]) if ctx.get("midPx") else None,
                premium=Decimal(ctx["premium"]) if ctx.get("premium") else None,
                day_notional_volume=Decimal(ctx["dayNtlVlm"]) if ctx.get("dayNtlVlm") else None,
            )
            update_cols = {k: v for k, v in values.items() if k not in ("exchange", "symbol", "timestamp")}
            stmt = pg_insert(MarketAssetMetrics).values(**values).on_conflict_do_update(
                index_elements=["exchange", "symbol", "timestamp"],
                set_=update_cols,
            )
            db.execute(stmt)

        except Exception as e:
            logger.error(f"Error flushing asset metrics for {symbol}: {e}")


# Singleton instance
market_flow_collector = MarketFlowCollector()


# Data retention settings
DATA_RETENTION_DAYS = 365


def get_retention_days(exchange: str = "hyperliquid") -> int:
    """Get retention days from SystemConfig for specific exchange, fallback to default"""
    try:
        from database.connection import SessionLocal
        from database.models import SystemConfig

        key = f"{exchange}_retention_days"
        db = SessionLocal()
        try:
            config = db.query(SystemConfig).filter(
                SystemConfig.key == key
            ).first()
            if config and config.value:
                return int(config.value)
        finally:
            db.close()
    except Exception:
        pass
    return DATA_RETENTION_DAYS


def cleanup_old_market_flow_data():
    """
    Delete market flow data older than configured retention days.
    Cleans up data for each exchange based on their individual retention settings.
    This function is designed to be called by a scheduled task.
    """
    import time
    from database.connection import SessionLocal
    from database.models import (
        MarketTradesAggregated,
        MarketOrderbookSnapshots,
        MarketAssetMetrics,
        MarketSentimentMetrics,
    )

    db = SessionLocal()
    try:
        total_deleted = 0

        # Clean up Hyperliquid data
        hl_retention = get_retention_days("hyperliquid")
        hl_cutoff_ms = int((time.time() - hl_retention * 86400) * 1000)

        hl_trades = (
            db.query(MarketTradesAggregated)
            .filter(
                MarketTradesAggregated.exchange == "hyperliquid",
                MarketTradesAggregated.timestamp < hl_cutoff_ms
            )
            .delete(synchronize_session=False)
        )
        hl_orderbook = (
            db.query(MarketOrderbookSnapshots)
            .filter(
                MarketOrderbookSnapshots.exchange == "hyperliquid",
                MarketOrderbookSnapshots.timestamp < hl_cutoff_ms
            )
            .delete(synchronize_session=False)
        )
        hl_metrics = (
            db.query(MarketAssetMetrics)
            .filter(
                MarketAssetMetrics.exchange == "hyperliquid",
                MarketAssetMetrics.timestamp < hl_cutoff_ms
            )
            .delete(synchronize_session=False)
        )
        hl_total = hl_trades + hl_orderbook + hl_metrics
        if hl_total > 0:
            logger.info(
                f"Hyperliquid cleanup: {hl_trades} trades, {hl_orderbook} orderbook, "
                f"{hl_metrics} metrics (older than {hl_retention} days)"
            )
        total_deleted += hl_total

        # Clean up Binance data
        bn_retention = get_retention_days("binance")
        bn_cutoff_ms = int((time.time() - bn_retention * 86400) * 1000)

        bn_trades = (
            db.query(MarketTradesAggregated)
            .filter(
                MarketTradesAggregated.exchange == "binance",
                MarketTradesAggregated.timestamp < bn_cutoff_ms
            )
            .delete(synchronize_session=False)
        )
        bn_orderbook = (
            db.query(MarketOrderbookSnapshots)
            .filter(
                MarketOrderbookSnapshots.exchange == "binance",
                MarketOrderbookSnapshots.timestamp < bn_cutoff_ms
            )
            .delete(synchronize_session=False)
        )
        bn_metrics = (
            db.query(MarketAssetMetrics)
            .filter(
                MarketAssetMetrics.exchange == "binance",
                MarketAssetMetrics.timestamp < bn_cutoff_ms
            )
            .delete(synchronize_session=False)
        )
        bn_sentiment = (
            db.query(MarketSentimentMetrics)
            .filter(
                MarketSentimentMetrics.exchange == "binance",
                MarketSentimentMetrics.timestamp < bn_cutoff_ms
            )
            .delete(synchronize_session=False)
        )
        bn_total = bn_trades + bn_orderbook + bn_metrics + bn_sentiment
        if bn_total > 0:
            logger.info(
                f"Binance cleanup: {bn_trades} trades, {bn_orderbook} orderbook, "
                f"{bn_metrics} metrics, {bn_sentiment} sentiment (older than {bn_retention} days)"
            )
        total_deleted += bn_total

        db.commit()

        if total_deleted == 0:
            logger.debug("Market flow data cleanup: no old records to delete")

    except Exception as e:
        db.rollback()
        logger.error(f"Market flow data cleanup failed: {e}")
    finally:
        db.close()
