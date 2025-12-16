"""
Signal Backtest Service

Backtests signals against historical data to show where triggers would occur.
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import text

logger = logging.getLogger(__name__)

# Timeframe to milliseconds mapping
TIMEFRAME_MS = {
    "1m": 60 * 1000,
    "3m": 3 * 60 * 1000,
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "30m": 30 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "2h": 2 * 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
}


class SignalBacktestService:
    """Service for backtesting signals against historical data."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def backtest_signal(
        self, db: Session, signal_id: int, symbol: str,
        kline_min_ts: int = None, kline_max_ts: int = None
    ) -> Dict[str, Any]:
        """
        Backtest a signal against historical data.
        Returns only trigger points - K-lines should be fetched separately via market API.

        Args:
            db: Database session
            signal_id: Signal definition ID
            symbol: Trading symbol (e.g., 'BTC')
            kline_min_ts: Minimum K-line timestamp in milliseconds (for filtering triggers)
            kline_max_ts: Maximum K-line timestamp in milliseconds (for filtering triggers)
        """
        # Clear bucket cache for fresh data
        self._bucket_cache = {}

        # Get signal definition
        result = db.execute(
            text("""
                SELECT id, signal_name, description, trigger_condition, enabled
                FROM signal_definitions WHERE id = :id
            """),
            {"id": signal_id}
        )
        row = result.fetchone()
        if not row:
            return {"error": "Signal not found"}

        signal_def = {
            "id": row[0],
            "signal_name": row[1],
            "description": row[2],
            "trigger_condition": row[3],
            "enabled": row[4]
        }

        condition = signal_def.get("trigger_condition", {})
        metric = condition.get("metric")
        time_window = condition.get("time_window", "5m")

        if not metric:
            return {"error": "Signal has no metric configured"}

        # Find triggers within the specified time range
        triggers = self._find_triggers_in_range(
            db, signal_def, symbol, time_window, kline_min_ts, kline_max_ts
        )

        return {
            "signal_id": signal_id,
            "signal_name": signal_def["signal_name"],
            "symbol": symbol,
            "time_window": time_window,
            "condition": condition,
            "trigger_count": len(triggers),
            "triggers": triggers,
        }

    def backtest_temp_signal(
        self, db: Session, symbol: str, trigger_condition: Dict,
        kline_min_ts: int = None, kline_max_ts: int = None
    ) -> Dict[str, Any]:
        """
        Backtest a temporary signal configuration without saving to database.
        Used for AI signal creation preview.

        Args:
            db: Database session
            symbol: Trading symbol (e.g., 'BTC')
            trigger_condition: Signal trigger condition dict
            kline_min_ts: Minimum K-line timestamp in milliseconds
            kline_max_ts: Maximum K-line timestamp in milliseconds
        """
        # Clear bucket cache for fresh data
        self._bucket_cache = {}

        # Build temporary signal definition
        signal_def = {
            "id": None,
            "signal_name": "Temporary Preview",
            "description": "AI-generated signal preview",
            "trigger_condition": trigger_condition,
            "enabled": True
        }

        metric = trigger_condition.get("metric")
        time_window = trigger_condition.get("time_window", "5m")

        if not metric:
            return {"error": "Signal has no metric configured"}

        # Find triggers within the specified time range
        triggers = self._find_triggers_in_range(
            db, signal_def, symbol, time_window, kline_min_ts, kline_max_ts
        )

        return {
            "signal_id": None,
            "signal_name": "Temporary Preview",
            "symbol": symbol,
            "time_window": time_window,
            "condition": trigger_condition,
            "trigger_count": len(triggers),
            "triggers": triggers,
        }

    def _find_triggers_in_range(
        self, db: Session, signal_def: Dict, symbol: str, time_window: str,
        kline_min_ts: int = None, kline_max_ts: int = None
    ) -> List[Dict]:
        """
        Find trigger points within a time range using 15-second sliding window detection.

        This simulates real-time detection behavior:
        - Check every 15 seconds (matching data collection granularity)
        - At each check point, calculate indicator using data available at that moment
        - Apply edge detection: only trigger on False -> True transitions

        Args:
            db: Database session
            signal_def: Signal definition dict
            symbol: Trading symbol
            time_window: Time window (e.g., '5m', '15m')
            kline_min_ts: Minimum timestamp in milliseconds (optional)
            kline_max_ts: Maximum timestamp in milliseconds (optional)
        """
        condition = signal_def.get("trigger_condition", {})
        metric = condition.get("metric")
        operator = condition.get("operator")
        threshold = condition.get("threshold")

        # Handle taker_volume composite signal
        if metric == "taker_volume":
            return self._find_taker_triggers_in_range(
                db, signal_def, symbol, time_window, kline_min_ts, kline_max_ts
            )

        if not all([metric, operator, threshold is not None]):
            return []

        # Map metric names for backward compatibility
        metric_map = {
            "oi_delta_percent": "oi_delta",
            "funding_rate": "funding",
            "taker_buy_ratio": "taker_ratio",
        }
        metric = metric_map.get(metric, metric)

        interval_ms = TIMEFRAME_MS.get(time_window, 300000)
        check_interval_ms = 15000  # 15 seconds, matching data granularity

        # Load raw 15-second granularity data for the time range
        raw_data = self._load_raw_data_for_metric(
            db, symbol, metric, kline_min_ts, kline_max_ts, interval_ms
        )
        if not raw_data:
            return []

        # Generate check points every 15 seconds
        check_points = self._generate_check_points(
            raw_data, kline_min_ts, kline_max_ts, check_interval_ms
        )

        # Simulate real-time detection with edge triggering
        triggers = []
        was_active = False

        for check_time in check_points:
            # Calculate indicator value at this check point (using only data up to check_time)
            value = self._calculate_indicator_at_time(
                raw_data, metric, check_time, interval_ms
            )

            if value is None:
                continue

            # Check condition
            condition_met = self._evaluate_condition(value, operator, threshold)

            # Edge detection: only trigger on False -> True
            if condition_met and not was_active:
                triggers.append({
                    "timestamp": check_time,
                    "value": value,
                    "threshold": threshold,
                    "operator": operator,
                })

            was_active = condition_met

        return triggers

    def _find_taker_triggers_in_range(
        self, db: Session, signal_def: Dict, symbol: str, time_window: str,
        kline_min_ts: int = None, kline_max_ts: int = None
    ) -> List[Dict]:
        """
        Find taker_volume composite signal triggers using 15-second sliding window.
        Simulates real-time detection with edge triggering.
        """
        condition = signal_def.get("trigger_condition", {})
        direction = condition.get("direction", "any")
        ratio_threshold = condition.get("ratio_threshold", 1.5)
        volume_threshold = condition.get("volume_threshold", 0)

        interval_ms = TIMEFRAME_MS.get(time_window, 300000)

        # Load raw 15-second granularity data
        raw_data = self._load_raw_data_for_metric(
            db, symbol, "taker_ratio", kline_min_ts, kline_max_ts, interval_ms
        )
        if not raw_data:
            return []

        # Generate check points every 15 seconds
        check_points = self._generate_check_points(raw_data, kline_min_ts, kline_max_ts, 15000)

        # Simulate real-time detection with edge triggering
        triggers = []
        was_active = False

        for check_time in check_points:
            # Calculate taker data at this check point
            taker_data = self._calc_taker_data_at_time(raw_data, check_time, interval_ms)
            if not taker_data:
                continue

            ratio = taker_data["ratio"]
            total = taker_data["volume"]

            if total < volume_threshold:
                was_active = False
                continue

            # Check condition
            condition_met = False
            actual_dir = None

            if direction == "buy" and ratio >= ratio_threshold:
                condition_met, actual_dir = True, "buy"
            elif direction == "sell" and ratio <= 1 / ratio_threshold:
                condition_met, actual_dir = True, "sell"
            elif direction == "any":
                if ratio >= ratio_threshold:
                    condition_met, actual_dir = True, "buy"
                elif ratio <= 1 / ratio_threshold:
                    condition_met, actual_dir = True, "sell"

            # Edge detection: only trigger on False -> True
            if condition_met and not was_active:
                triggers.append({
                    "timestamp": check_time,
                    "direction": actual_dir,
                    "ratio": ratio,
                    "ratio_threshold": ratio_threshold,
                    "volume": total,
                    "volume_threshold": volume_threshold,
                })

            was_active = condition_met

        return triggers

    # Legacy method - kept for backward compatibility but no longer used
    def _find_triggers(
        self, db: Session, signal_def: Dict, symbol: str, klines: List[Dict], time_window: str
    ) -> List[Dict]:
        """
        Find trigger points in historical data.

        IMPORTANT: This method iterates over MARKET FLOW DATA (buckets), not K-lines.
        K-lines are only used as a visual background - the actual trigger detection
        is based on market flow indicator values from the database.

        The trigger timestamp is the bucket timestamp, and we find the closest
        K-line to display the price context.
        """
        condition = signal_def.get("trigger_condition", {})
        metric = condition.get("metric")
        operator = condition.get("operator")
        threshold = condition.get("threshold")

        if not all([metric, operator, threshold is not None]):
            # Handle taker_volume composite signal
            if metric == "taker_volume":
                return self._find_taker_triggers(db, signal_def, symbol, klines, time_window)
            return []

        # Map metric names for backward compatibility
        metric_map = {
            "oi_delta_percent": "oi_delta",
            "funding_rate": "funding",
            "taker_buy_ratio": "taker_ratio",
        }
        metric = metric_map.get(metric, metric)

        interval_ms = TIMEFRAME_MS.get(time_window, 300000)

        # Get ALL bucket values from market flow data (this is the PRIMARY data source)
        cache_key = f"{symbol}_{metric}_{interval_ms}"
        if cache_key not in self._bucket_cache:
            self._bucket_cache[cache_key] = self._compute_all_bucket_values(
                db, symbol, metric, interval_ms
            )
        bucket_values = self._bucket_cache[cache_key]

        if not bucket_values:
            return []

        # Build a lookup for K-line prices (for display only)
        from services.market_flow_indicators import floor_timestamp
        kline_prices = {}
        for kline in klines:
            bucket_ts = floor_timestamp(kline["timestamp"], interval_ms)
            kline_prices[bucket_ts] = kline["close"]

        # Get K-line time range for filtering triggers to display
        if klines:
            kline_min_ts = min(floor_timestamp(k["timestamp"], interval_ms) for k in klines)
            kline_max_ts = max(floor_timestamp(k["timestamp"], interval_ms) for k in klines)
        else:
            return []

        # Iterate over ALL buckets and find triggers
        triggers = []
        for bucket_ts, value in sorted(bucket_values.items()):
            # Only include triggers within K-line display range
            if bucket_ts < kline_min_ts or bucket_ts > kline_max_ts:
                continue

            if value is not None and self._evaluate_condition(value, operator, threshold):
                # Get price from K-line (for display context)
                price = kline_prices.get(bucket_ts, 0)
                triggers.append({
                    "timestamp": bucket_ts,
                    "value": value,
                    "threshold": threshold,
                    "operator": operator,
                    "price": price,
                })

        return triggers

    def _get_indicator_at_time(
        self, db: Session, symbol: str, metric: str, timestamp_ms: int, interval_ms: int
    ) -> Optional[float]:
        """
        Get indicator value at a specific timestamp using bucket aggregation.

        IMPORTANT: This method uses the same bucket aggregation logic as
        signal_analysis_service to ensure consistency between:
        - Statistical analysis (threshold suggestions)
        - Preview backtest (trigger visualization)
        - K-line chart indicators

        The key insight is that we need to return the indicator value FOR that
        specific bucket, not the "current" value looking back from that timestamp.
        """
        # Use pre-computed bucket values if available
        cache_key = f"{symbol}_{metric}_{interval_ms}"
        if not hasattr(self, '_bucket_cache'):
            self._bucket_cache = {}

        if cache_key not in self._bucket_cache:
            self._bucket_cache[cache_key] = self._compute_all_bucket_values(
                db, symbol, metric, interval_ms
            )

        bucket_values = self._bucket_cache[cache_key]
        if not bucket_values:
            return None

        # Find the bucket that contains this timestamp
        from services.market_flow_indicators import floor_timestamp
        bucket_ts = floor_timestamp(timestamp_ms, interval_ms)

        return bucket_values.get(bucket_ts)

    def _compute_all_bucket_values(
        self, db: Session, symbol: str, metric: str, interval_ms: int
    ) -> Dict[int, float]:
        """
        Compute indicator values for all buckets using the same logic as
        signal_analysis_service. This ensures consistency between statistical
        analysis and backtest preview.

        Returns a dict mapping bucket_timestamp -> indicator_value
        """
        from services.market_flow_indicators import floor_timestamp
        from database.models import MarketAssetMetrics, MarketTradesAggregated
        from database.models import MarketOrderbookSnapshots
        from datetime import datetime

        # Query 7 days of data (same as signal_analysis_service)
        current_time_ms = int(datetime.utcnow().timestamp() * 1000)
        start_time_ms = current_time_ms - (7 * 24 * 60 * 60 * 1000)

        if metric == "oi_delta":
            return self._compute_oi_delta_buckets(
                db, symbol, interval_ms, start_time_ms, current_time_ms
            )
        elif metric == "cvd":
            return self._compute_cvd_buckets(
                db, symbol, interval_ms, start_time_ms, current_time_ms
            )
        elif metric == "depth_ratio":
            return self._compute_depth_ratio_buckets(
                db, symbol, interval_ms, start_time_ms, current_time_ms
            )
        elif metric == "order_imbalance":
            return self._compute_imbalance_buckets(
                db, symbol, interval_ms, start_time_ms, current_time_ms
            )
        elif metric == "taker_ratio":
            return self._compute_taker_ratio_buckets(
                db, symbol, interval_ms, start_time_ms, current_time_ms
            )
        elif metric == "funding":
            return self._compute_funding_buckets(
                db, symbol, interval_ms, start_time_ms, current_time_ms
            )
        elif metric == "oi":
            return self._compute_oi_buckets(
                db, symbol, interval_ms, start_time_ms, current_time_ms
            )
        else:
            logger.warning(f"Unknown metric for bucket computation: {metric}")
            return {}

    def _compute_oi_delta_buckets(
        self, db, symbol, interval_ms, start_time_ms, current_time_ms
    ) -> Dict[int, float]:
        """Compute OI delta percentage for each bucket (same as signal_analysis)."""
        from services.market_flow_indicators import floor_timestamp
        from database.models import MarketAssetMetrics

        records = db.query(
            MarketAssetMetrics.timestamp,
            MarketAssetMetrics.open_interest
        ).filter(
            MarketAssetMetrics.symbol == symbol.upper(),
            MarketAssetMetrics.timestamp >= start_time_ms,
            MarketAssetMetrics.timestamp <= current_time_ms
        ).order_by(MarketAssetMetrics.timestamp).all()

        if not records:
            return {}

        # Bucket by period
        buckets = {}
        for ts, oi in records:
            bucket_ts = floor_timestamp(ts, interval_ms)
            buckets[bucket_ts] = float(oi) if oi else None

        # Calculate deltas - map each bucket to its delta value
        sorted_times = sorted(buckets.keys())
        result = {}
        for i in range(1, len(sorted_times)):
            prev_oi = buckets[sorted_times[i-1]]
            curr_oi = buckets[sorted_times[i]]
            if prev_oi and curr_oi and prev_oi != 0:
                delta_pct = ((curr_oi - prev_oi) / prev_oi) * 100
                # The delta is associated with the CURRENT bucket
                result[sorted_times[i]] = delta_pct

        return result

    def _compute_cvd_buckets(
        self, db, symbol, interval_ms, start_time_ms, current_time_ms
    ) -> Dict[int, float]:
        """Compute CVD for each bucket."""
        from services.market_flow_indicators import floor_timestamp
        from database.models import MarketTradesAggregated

        records = db.query(
            MarketTradesAggregated.timestamp,
            MarketTradesAggregated.taker_buy_notional,
            MarketTradesAggregated.taker_sell_notional
        ).filter(
            MarketTradesAggregated.symbol == symbol.upper(),
            MarketTradesAggregated.timestamp >= start_time_ms,
            MarketTradesAggregated.timestamp <= current_time_ms
        ).order_by(MarketTradesAggregated.timestamp).all()

        if not records:
            return {}

        buckets = {}
        for ts, buy, sell in records:
            bucket_ts = floor_timestamp(ts, interval_ms)
            if bucket_ts not in buckets:
                buckets[bucket_ts] = {"buy": 0, "sell": 0}
            buckets[bucket_ts]["buy"] += float(buy or 0)
            buckets[bucket_ts]["sell"] += float(sell or 0)

        result = {}
        for ts in buckets:
            result[ts] = buckets[ts]["buy"] - buckets[ts]["sell"]

        return result

    def _compute_depth_ratio_buckets(
        self, db, symbol, interval_ms, start_time_ms, current_time_ms
    ) -> Dict[int, float]:
        """Compute depth ratio (bid/ask) for each bucket."""
        from services.market_flow_indicators import floor_timestamp
        from database.models import MarketOrderbookSnapshots

        records = db.query(
            MarketOrderbookSnapshots.timestamp,
            MarketOrderbookSnapshots.bid_depth_5,
            MarketOrderbookSnapshots.ask_depth_5
        ).filter(
            MarketOrderbookSnapshots.symbol == symbol.upper(),
            MarketOrderbookSnapshots.timestamp >= start_time_ms,
            MarketOrderbookSnapshots.timestamp <= current_time_ms
        ).order_by(MarketOrderbookSnapshots.timestamp).all()

        if not records:
            return {}

        buckets = {}
        for ts, bid, ask in records:
            bucket_ts = floor_timestamp(ts, interval_ms)
            buckets[bucket_ts] = {"bid": float(bid or 0), "ask": float(ask or 0)}

        result = {}
        for ts in buckets:
            ask = buckets[ts]["ask"]
            if ask > 0:
                result[ts] = buckets[ts]["bid"] / ask

        return result

    def _compute_imbalance_buckets(
        self, db, symbol, interval_ms, start_time_ms, current_time_ms
    ) -> Dict[int, float]:
        """Compute order imbalance for each bucket."""
        from services.market_flow_indicators import floor_timestamp
        from database.models import MarketOrderbookSnapshots

        records = db.query(
            MarketOrderbookSnapshots.timestamp,
            MarketOrderbookSnapshots.bid_depth_5,
            MarketOrderbookSnapshots.ask_depth_5
        ).filter(
            MarketOrderbookSnapshots.symbol == symbol.upper(),
            MarketOrderbookSnapshots.timestamp >= start_time_ms,
            MarketOrderbookSnapshots.timestamp <= current_time_ms
        ).order_by(MarketOrderbookSnapshots.timestamp).all()

        if not records:
            return {}

        buckets = {}
        for ts, bid, ask in records:
            bucket_ts = floor_timestamp(ts, interval_ms)
            buckets[bucket_ts] = {"bid": float(bid or 0), "ask": float(ask or 0)}

        result = {}
        for ts in buckets:
            bid, ask = buckets[ts]["bid"], buckets[ts]["ask"]
            total = bid + ask
            if total > 0:
                result[ts] = (bid - ask) / total

        return result

    def _compute_taker_ratio_buckets(
        self, db, symbol, interval_ms, start_time_ms, current_time_ms
    ) -> Dict[int, float]:
        """Compute taker buy/sell ratio for each bucket."""
        from services.market_flow_indicators import floor_timestamp
        from database.models import MarketTradesAggregated

        records = db.query(
            MarketTradesAggregated.timestamp,
            MarketTradesAggregated.taker_buy_notional,
            MarketTradesAggregated.taker_sell_notional
        ).filter(
            MarketTradesAggregated.symbol == symbol.upper(),
            MarketTradesAggregated.timestamp >= start_time_ms,
            MarketTradesAggregated.timestamp <= current_time_ms
        ).order_by(MarketTradesAggregated.timestamp).all()

        if not records:
            return {}

        buckets = {}
        for ts, buy, sell in records:
            bucket_ts = floor_timestamp(ts, interval_ms)
            if bucket_ts not in buckets:
                buckets[bucket_ts] = {"buy": 0, "sell": 0}
            buckets[bucket_ts]["buy"] += float(buy or 0)
            buckets[bucket_ts]["sell"] += float(sell or 0)

        result = {}
        for ts in buckets:
            sell = buckets[ts]["sell"]
            if sell > 0:
                result[ts] = buckets[ts]["buy"] / sell

        return result

    def _compute_funding_buckets(
        self, db, symbol, interval_ms, start_time_ms, current_time_ms
    ) -> Dict[int, float]:
        """Compute funding rate for each bucket."""
        from services.market_flow_indicators import floor_timestamp
        from database.models import MarketAssetMetrics

        records = db.query(
            MarketAssetMetrics.timestamp,
            MarketAssetMetrics.funding_rate
        ).filter(
            MarketAssetMetrics.symbol == symbol.upper(),
            MarketAssetMetrics.timestamp >= start_time_ms,
            MarketAssetMetrics.timestamp <= current_time_ms,
            MarketAssetMetrics.funding_rate.isnot(None)
        ).order_by(MarketAssetMetrics.timestamp).all()

        if not records:
            return {}

        buckets = {}
        for ts, funding in records:
            bucket_ts = floor_timestamp(ts, interval_ms)
            buckets[bucket_ts] = float(funding) * 100  # Convert to percentage

        return buckets

    def _compute_oi_buckets(
        self, db, symbol, interval_ms, start_time_ms, current_time_ms
    ) -> Dict[int, float]:
        """Compute absolute OI for each bucket."""
        from services.market_flow_indicators import floor_timestamp
        from database.models import MarketAssetMetrics

        records = db.query(
            MarketAssetMetrics.timestamp,
            MarketAssetMetrics.open_interest
        ).filter(
            MarketAssetMetrics.symbol == symbol.upper(),
            MarketAssetMetrics.timestamp >= start_time_ms,
            MarketAssetMetrics.timestamp <= current_time_ms,
            MarketAssetMetrics.open_interest.isnot(None)
        ).order_by(MarketAssetMetrics.timestamp).all()

        if not records:
            return {}

        buckets = {}
        for ts, oi in records:
            bucket_ts = floor_timestamp(ts, interval_ms)
            buckets[bucket_ts] = float(oi)

        return buckets

    def _find_taker_triggers(
        self, db: Session, signal_def: Dict, symbol: str, klines: List[Dict], time_window: str
    ) -> List[Dict]:
        """
        Find taker_volume composite signal triggers.

        IMPORTANT: This method iterates over MARKET FLOW DATA (buckets), not K-lines.
        K-lines are only used as a visual background.
        """
        condition = signal_def.get("trigger_condition", {})
        direction = condition.get("direction", "any")
        ratio_threshold = condition.get("ratio_threshold", 1.5)
        volume_threshold = condition.get("volume_threshold", 0)

        interval_ms = TIMEFRAME_MS.get(time_window, 300000)

        # Compute all taker volume buckets from market flow data
        taker_buckets = self._compute_taker_volume_buckets(db, symbol, interval_ms)
        if not taker_buckets:
            return []

        # Build K-line price lookup
        from services.market_flow_indicators import floor_timestamp
        kline_prices = {}
        for kline in klines:
            bucket_ts = floor_timestamp(kline["timestamp"], interval_ms)
            kline_prices[bucket_ts] = kline["close"]

        # Get K-line time range
        if not klines:
            return []
        kline_min_ts = min(floor_timestamp(k["timestamp"], interval_ms) for k in klines)
        kline_max_ts = max(floor_timestamp(k["timestamp"], interval_ms) for k in klines)

        # Iterate over all buckets and find triggers
        triggers = []
        for bucket_ts, data in sorted(taker_buckets.items()):
            # Only include triggers within K-line display range
            if bucket_ts < kline_min_ts or bucket_ts > kline_max_ts:
                continue

            ratio = data["ratio"]
            total = data["volume"]

            if total < volume_threshold:
                continue

            triggered = False
            actual_dir = None

            if direction == "buy" and ratio >= ratio_threshold:
                triggered, actual_dir = True, "buy"
            elif direction == "sell" and ratio <= 1 / ratio_threshold:
                triggered, actual_dir = True, "sell"
            elif direction == "any":
                if ratio >= ratio_threshold:
                    triggered, actual_dir = True, "buy"
                elif ratio <= 1 / ratio_threshold:
                    triggered, actual_dir = True, "sell"

            if triggered:
                triggers.append({
                    "timestamp": bucket_ts,
                    "direction": actual_dir,
                    "ratio": ratio,
                    "ratio_threshold": ratio_threshold,
                    "volume": total,
                    "volume_threshold": volume_threshold,
                    "price": kline_prices.get(bucket_ts, 0),
                })

        return triggers

    def _compute_taker_volume_buckets(
        self, db, symbol, interval_ms
    ) -> Dict[int, Dict]:
        """Compute taker volume data (ratio and volume) for each bucket."""
        from services.market_flow_indicators import floor_timestamp
        from database.models import MarketTradesAggregated
        from datetime import datetime

        current_time_ms = int(datetime.utcnow().timestamp() * 1000)
        start_time_ms = current_time_ms - (7 * 24 * 60 * 60 * 1000)

        records = db.query(
            MarketTradesAggregated.timestamp,
            MarketTradesAggregated.taker_buy_notional,
            MarketTradesAggregated.taker_sell_notional
        ).filter(
            MarketTradesAggregated.symbol == symbol.upper(),
            MarketTradesAggregated.timestamp >= start_time_ms,
            MarketTradesAggregated.timestamp <= current_time_ms
        ).order_by(MarketTradesAggregated.timestamp).all()

        if not records:
            return {}

        buckets = {}
        for ts, buy, sell in records:
            bucket_ts = floor_timestamp(ts, interval_ms)
            if bucket_ts not in buckets:
                buckets[bucket_ts] = {"buy": 0, "sell": 0}
            buckets[bucket_ts]["buy"] += float(buy or 0)
            buckets[bucket_ts]["sell"] += float(sell or 0)

        # Calculate ratio and volume for each bucket
        result = {}
        for ts, data in buckets.items():
            buy, sell = data["buy"], data["sell"]
            total = buy + sell
            if sell > 0 and total > 0:
                result[ts] = {
                    "ratio": buy / sell,
                    "volume": total
                }

        return result

    def backtest_pool(
        self, db: Session, pool_id: int, symbol: str,
        kline_min_ts: int = None, kline_max_ts: int = None
    ) -> Dict[str, Any]:
        """
        Backtest a signal pool against historical data.
        For AND logic: evaluates all signals at each check point with pool-level edge detection.
        For OR logic: combines individual signal triggers.
        """
        self._bucket_cache = {}

        # Get pool definition
        result = db.execute(
            text("""
                SELECT id, pool_name, signal_ids, symbols, enabled, logic
                FROM signal_pools WHERE id = :id
            """),
            {"id": pool_id}
        )
        row = result.fetchone()
        if not row:
            return {"error": "Pool not found"}

        pool_def = {
            "id": row[0],
            "pool_name": row[1],
            "signal_ids": row[2] or [],
            "symbols": row[3] or [],
            "enabled": row[4],
            "logic": row[5] or "OR"
        }

        signal_ids = pool_def["signal_ids"]
        if not signal_ids:
            return {"error": "Pool has no signals configured"}

        logic = pool_def["logic"]

        # For AND logic, use pool-level detection to match real-time behavior
        if logic == "AND":
            return self._backtest_pool_and_logic(
                db, pool_def, signal_ids, symbol, kline_min_ts, kline_max_ts
            )

        # For OR logic, use individual signal triggers
        signal_triggers = {}
        signal_names = {}
        time_window = "5m"

        for signal_id in signal_ids:
            signal_result = self.backtest_signal(
                db, signal_id, symbol, kline_min_ts, kline_max_ts
            )
            if "error" not in signal_result:
                signal_triggers[signal_id] = {
                    t["timestamp"]: t for t in signal_result.get("triggers", [])
                }
                signal_names[signal_id] = signal_result.get("signal_name", f"Signal {signal_id}")
                time_window = signal_result.get("time_window", time_window)

        if not signal_triggers:
            return {"error": "No valid signals in pool"}

        combined_triggers = self._combine_pool_triggers(
            signal_triggers, signal_names, logic
        )

        return {
            "pool_id": pool_id,
            "pool_name": pool_def["pool_name"],
            "symbol": symbol,
            "time_window": time_window,
            "logic": logic,
            "signal_count": len(signal_ids),
            "signal_names": signal_names,
            "trigger_count": len(combined_triggers),
            "triggers": combined_triggers,
        }

    def _backtest_pool_and_logic(
        self, db: Session, pool_def: Dict, signal_ids: List[int], symbol: str,
        kline_min_ts: int, kline_max_ts: int
    ) -> Dict[str, Any]:
        """
        Backtest pool with AND logic using pool-level edge detection.
        Evaluates all signals at each check point simultaneously.
        """
        # Get all signal definitions
        signal_defs = {}
        signal_names = {}
        time_window = "5m"

        for signal_id in signal_ids:
            result = db.execute(
                text("""
                    SELECT id, signal_name, trigger_condition
                    FROM signal_definitions WHERE id = :id
                """),
                {"id": signal_id}
            )
            row = result.fetchone()
            if row:
                signal_defs[signal_id] = {
                    "id": row[0],
                    "signal_name": row[1],
                    "trigger_condition": row[2]
                }
                signal_names[signal_id] = row[1]
                time_window = row[2].get("time_window", time_window)

        if not signal_defs:
            return {"error": "No valid signals in pool"}

        interval_ms = TIMEFRAME_MS.get(time_window, 300000)

        # Load raw data for all metrics needed
        metrics_data = {}
        for signal_id, sig_def in signal_defs.items():
            condition = sig_def["trigger_condition"]
            metric = condition.get("metric")
            if metric and metric not in metrics_data:
                metric_map = {"oi_delta_percent": "oi_delta", "taker_buy_ratio": "taker_ratio"}
                metric = metric_map.get(metric, metric)
                raw_data = self._load_raw_data_for_metric(
                    db, symbol, metric, kline_min_ts, kline_max_ts, interval_ms
                )
                metrics_data[metric] = raw_data

        # Generate check points from all data
        all_timestamps = set()
        for data in metrics_data.values():
            if data:
                all_timestamps.update(r[0] for r in data)

        if kline_min_ts:
            all_timestamps = {ts for ts in all_timestamps if ts >= kline_min_ts}
        if kline_max_ts:
            all_timestamps = {ts for ts in all_timestamps if ts <= kline_max_ts}

        check_points = sorted(all_timestamps)

        # Evaluate all signals at each check point with pool-level edge detection
        triggers = []
        was_active = False

        for check_time in check_points:
            all_met = True
            signal_values = []

            for signal_id, sig_def in signal_defs.items():
                condition = sig_def["trigger_condition"]
                metric = condition.get("metric")
                operator = condition.get("operator")
                threshold = condition.get("threshold")

                metric_map = {"oi_delta_percent": "oi_delta", "taker_buy_ratio": "taker_ratio"}
                metric = metric_map.get(metric, metric)

                raw_data = metrics_data.get(metric, [])
                value = self._calculate_indicator_at_time(raw_data, metric, check_time, interval_ms)

                if value is None:
                    all_met = False
                    break

                condition_met = self._evaluate_condition(value, operator, threshold)
                if not condition_met:
                    all_met = False
                    break

                signal_values.append({
                    "signal_id": signal_id,
                    "signal_name": sig_def["signal_name"],
                    "value": value,
                    "threshold": threshold,
                })

            # Pool-level edge detection
            if all_met and not was_active:
                triggers.append({
                    "timestamp": check_time,
                    "triggered_signals": signal_values,
                    "trigger_type": "all",
                })

            was_active = all_met

        return {
            "pool_id": pool_def["id"],
            "pool_name": pool_def["pool_name"],
            "symbol": symbol,
            "time_window": time_window,
            "logic": "AND",
            "signal_count": len(signal_ids),
            "signal_names": signal_names,
            "trigger_count": len(triggers),
            "triggers": triggers,
        }

    def _combine_pool_triggers(
        self, signal_triggers: Dict[int, Dict], signal_names: Dict[int, str], logic: str
    ) -> List[Dict]:
        """
        Combine triggers from multiple signals based on pool logic.

        Args:
            signal_triggers: Dict mapping signal_id to {timestamp: trigger_data}
            signal_names: Dict mapping signal_id to signal name
            logic: 'AND' or 'OR'
        """
        if logic == "OR":
            # OR: Any signal triggers = pool triggers
            all_timestamps = set()
            for triggers in signal_triggers.values():
                all_timestamps.update(triggers.keys())

            combined = []
            for ts in sorted(all_timestamps):
                triggered_signals = []
                for signal_id, triggers in signal_triggers.items():
                    if ts in triggers:
                        triggered_signals.append({
                            "signal_id": signal_id,
                            "signal_name": signal_names.get(signal_id, f"Signal {signal_id}"),
                            "value": triggers[ts].get("value"),
                            "threshold": triggers[ts].get("threshold"),
                        })
                combined.append({
                    "timestamp": ts,
                    "triggered_signals": triggered_signals,
                    "trigger_type": "any",
                })
            return combined

        else:  # AND
            # AND: All signals must trigger at the same timestamp
            if not signal_triggers:
                return []

            # Find timestamps where ALL signals triggered
            common_timestamps = None
            for triggers in signal_triggers.values():
                ts_set = set(triggers.keys())
                if common_timestamps is None:
                    common_timestamps = ts_set
                else:
                    common_timestamps &= ts_set

            if not common_timestamps:
                return []

            combined = []
            for ts in sorted(common_timestamps):
                triggered_signals = []
                for signal_id, triggers in signal_triggers.items():
                    triggered_signals.append({
                        "signal_id": signal_id,
                        "signal_name": signal_names.get(signal_id, f"Signal {signal_id}"),
                        "value": triggers[ts].get("value"),
                        "threshold": triggers[ts].get("threshold"),
                    })
                combined.append({
                    "timestamp": ts,
                    "triggered_signals": triggered_signals,
                    "trigger_type": "all",
                })
            return combined

    def _load_raw_data_for_metric(
        self, db: Session, symbol: str, metric: str,
        kline_min_ts: int, kline_max_ts: int, interval_ms: int
    ) -> List[tuple]:
        """
        Load raw 15-second granularity data for a metric.
        Returns list of (timestamp, value1, value2, ...) tuples.
        """
        from database.models import MarketTradesAggregated, MarketAssetMetrics, MarketOrderbookSnapshots

        # Extend range to include lookback period for first check point
        lookback_ms = interval_ms * 10
        start_time = (kline_min_ts - lookback_ms) if kline_min_ts else None

        if metric in ("cvd", "taker_ratio"):
            query = db.query(
                MarketTradesAggregated.timestamp,
                MarketTradesAggregated.taker_buy_notional,
                MarketTradesAggregated.taker_sell_notional
            ).filter(MarketTradesAggregated.symbol == symbol.upper())
            if start_time:
                query = query.filter(MarketTradesAggregated.timestamp >= start_time)
            if kline_max_ts:
                query = query.filter(MarketTradesAggregated.timestamp <= kline_max_ts)
            return query.order_by(MarketTradesAggregated.timestamp).all()

        elif metric == "oi_delta":
            query = db.query(
                MarketAssetMetrics.timestamp,
                MarketAssetMetrics.open_interest
            ).filter(MarketAssetMetrics.symbol == symbol.upper())
            if start_time:
                query = query.filter(MarketAssetMetrics.timestamp >= start_time)
            if kline_max_ts:
                query = query.filter(MarketAssetMetrics.timestamp <= kline_max_ts)
            return query.order_by(MarketAssetMetrics.timestamp).all()

        elif metric in ("order_imbalance", "depth_ratio"):
            query = db.query(
                MarketOrderbookSnapshots.timestamp,
                MarketOrderbookSnapshots.bid_depth_5,
                MarketOrderbookSnapshots.ask_depth_5
            ).filter(MarketOrderbookSnapshots.symbol == symbol.upper())
            if start_time:
                query = query.filter(MarketOrderbookSnapshots.timestamp >= start_time)
            if kline_max_ts:
                query = query.filter(MarketOrderbookSnapshots.timestamp <= kline_max_ts)
            return query.order_by(MarketOrderbookSnapshots.timestamp).all()

        return []

    def _generate_check_points(
        self, raw_data: List[tuple], kline_min_ts: int, kline_max_ts: int, check_interval_ms: int
    ) -> List[int]:
        """Generate check points every 15 seconds within the time range."""
        if not raw_data:
            return []

        # Use actual data timestamps as check points (they are already 15s aligned)
        timestamps = [r[0] for r in raw_data]

        # Filter to requested range
        if kline_min_ts:
            timestamps = [ts for ts in timestamps if ts >= kline_min_ts]
        if kline_max_ts:
            timestamps = [ts for ts in timestamps if ts <= kline_max_ts]

        return sorted(set(timestamps))

    def _calculate_indicator_at_time(
        self, raw_data: List[tuple], metric: str, check_time: int, interval_ms: int
    ) -> Optional[float]:
        """
        Calculate indicator value at a specific check time.
        Simulates real-time detection: only uses data up to check_time.
        """
        from services.market_flow_indicators import floor_timestamp

        # Same lookback as real-time detection
        lookback_ms = interval_ms * 10
        start_time = check_time - lookback_ms

        # Filter data: start_time <= timestamp <= check_time
        relevant_data = [r for r in raw_data if start_time <= r[0] <= check_time]
        if not relevant_data:
            return None

        if metric == "cvd":
            return self._calc_cvd_at_time(relevant_data, interval_ms)
        elif metric == "oi_delta":
            return self._calc_oi_delta_at_time(relevant_data, interval_ms)
        elif metric == "order_imbalance":
            return self._calc_imbalance_at_time(relevant_data, interval_ms)
        elif metric == "depth_ratio":
            return self._calc_depth_ratio_at_time(relevant_data, interval_ms)
        elif metric == "taker_ratio":
            return self._calc_taker_ratio_at_time(relevant_data, interval_ms)
        return None

    def _calc_cvd_at_time(self, data: List[tuple], interval_ms: int) -> Optional[float]:
        """Calculate CVD at a specific time (same logic as _get_cvd_data)."""
        from services.market_flow_indicators import floor_timestamp

        buckets = {}
        for ts, buy, sell in data:
            bucket_ts = floor_timestamp(ts, interval_ms)
            if bucket_ts not in buckets:
                buckets[bucket_ts] = {"buy": 0, "sell": 0}
            buckets[bucket_ts]["buy"] += float(buy or 0)
            buckets[bucket_ts]["sell"] += float(sell or 0)

        if not buckets:
            return None

        # Return last bucket's delta (same as real-time detection)
        sorted_times = sorted(buckets.keys())
        last_bucket = buckets[sorted_times[-1]]
        return last_bucket["buy"] - last_bucket["sell"]

    def _calc_oi_delta_at_time(self, data: List[tuple], interval_ms: int) -> Optional[float]:
        """Calculate OI delta percentage at a specific time."""
        from services.market_flow_indicators import floor_timestamp

        buckets = {}
        for ts, oi in data:
            bucket_ts = floor_timestamp(ts, interval_ms)
            buckets[bucket_ts] = float(oi) if oi else None

        sorted_times = sorted(buckets.keys())
        if len(sorted_times) < 2:
            return None

        prev_oi = buckets[sorted_times[-2]]
        curr_oi = buckets[sorted_times[-1]]
        if prev_oi and curr_oi and prev_oi != 0:
            return ((curr_oi - prev_oi) / prev_oi) * 100
        return None

    def _calc_imbalance_at_time(self, data: List[tuple], interval_ms: int) -> Optional[float]:
        """Calculate order book imbalance at a specific time."""
        from services.market_flow_indicators import floor_timestamp

        buckets = {}
        for ts, bid, ask in data:
            bucket_ts = floor_timestamp(ts, interval_ms)
            buckets[bucket_ts] = {"bid": float(bid or 0), "ask": float(ask or 0)}

        if not buckets:
            return None

        sorted_times = sorted(buckets.keys())
        last = buckets[sorted_times[-1]]
        total = last["bid"] + last["ask"]
        if total > 0:
            return (last["bid"] - last["ask"]) / total
        return None

    def _calc_depth_ratio_at_time(self, data: List[tuple], interval_ms: int) -> Optional[float]:
        """Calculate depth ratio (bid/ask) at a specific time."""
        from services.market_flow_indicators import floor_timestamp

        buckets = {}
        for ts, bid, ask in data:
            bucket_ts = floor_timestamp(ts, interval_ms)
            buckets[bucket_ts] = {"bid": float(bid or 0), "ask": float(ask or 0)}

        if not buckets:
            return None

        sorted_times = sorted(buckets.keys())
        last = buckets[sorted_times[-1]]
        if last["ask"] > 0:
            return last["bid"] / last["ask"]
        return None

    def _calc_taker_ratio_at_time(self, data: List[tuple], interval_ms: int) -> Optional[float]:
        """Calculate taker buy/sell ratio at a specific time."""
        from services.market_flow_indicators import floor_timestamp

        buckets = {}
        for ts, buy, sell in data:
            bucket_ts = floor_timestamp(ts, interval_ms)
            if bucket_ts not in buckets:
                buckets[bucket_ts] = {"buy": 0, "sell": 0}
            buckets[bucket_ts]["buy"] += float(buy or 0)
            buckets[bucket_ts]["sell"] += float(sell or 0)

        if not buckets:
            return None

        sorted_times = sorted(buckets.keys())
        last = buckets[sorted_times[-1]]
        if last["sell"] > 0:
            return last["buy"] / last["sell"]
        return None

    def _calc_taker_data_at_time(
        self, raw_data: List[tuple], check_time: int, interval_ms: int
    ) -> Optional[Dict]:
        """Calculate taker volume data (ratio and volume) at a specific time."""
        from services.market_flow_indicators import floor_timestamp

        lookback_ms = interval_ms * 10
        start_time = check_time - lookback_ms

        relevant_data = [r for r in raw_data if start_time <= r[0] <= check_time]
        if not relevant_data:
            return None

        buckets = {}
        for ts, buy, sell in relevant_data:
            bucket_ts = floor_timestamp(ts, interval_ms)
            if bucket_ts not in buckets:
                buckets[bucket_ts] = {"buy": 0, "sell": 0}
            buckets[bucket_ts]["buy"] += float(buy or 0)
            buckets[bucket_ts]["sell"] += float(sell or 0)

        if not buckets:
            return None

        sorted_times = sorted(buckets.keys())
        last = buckets[sorted_times[-1]]
        buy, sell = last["buy"], last["sell"]
        total = buy + sell

        if sell > 0 and total > 0:
            return {"ratio": buy / sell, "volume": total}
        return None

    def _evaluate_condition(self, value: float, operator: str, threshold: float) -> bool:
        """Evaluate if a condition is met."""
        # Support both symbol and text forms of operators
        if operator in (">", "greater_than", "gt"):
            return value > threshold
        elif operator in (">=", "greater_than_or_equal", "gte"):
            return value >= threshold
        elif operator in ("<", "less_than", "lt"):
            return value < threshold
        elif operator in ("<=", "less_than_or_equal", "lte"):
            return value <= threshold
        elif operator in ("==", "equal", "eq"):
            return abs(value - threshold) < 1e-9
        elif operator in ("!=", "not_equal", "ne"):
            return abs(value - threshold) >= 1e-9
        elif operator in ("abs_greater_than", "abs_gt"):
            return abs(value) > threshold
        elif operator in ("abs_less_than", "abs_lt"):
            return abs(value) < threshold
        return False


# Singleton instance
signal_backtest_service = SignalBacktestService()
