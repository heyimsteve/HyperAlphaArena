"""
Data provider for Program Trader.
Connects to existing data services (klines, indicators, flow, regime).
"""

from typing import Dict, List, Any, Optional, Callable
from sqlalchemy.orm import Session

from .models import Kline, Position, Trade, RegimeInfo, Order


def compute_factor_snapshot(
    db: Session,
    symbol: str,
    factor_name: str,
    period: str,
    exchange: str,
    klines_loader: Callable[[str, int], List[Dict[str, Any]]],
    include_effectiveness: bool = True,
) -> Dict[str, Any]:
    """Compute a factor snapshot from K-lines plus optional effectiveness metrics.

    Sync rule:
    - Program live get_factor()
    - Prompt factor variables
    - Program backtest get_factor()
    must keep the same factor-value calculation semantics:
      period-aware, 500 K-lines, same expression engine, same last-value rule.
    If this function changes, verify all three call sites together.
    """
    from services.factor_resolver import (
        compute_factor_value,
        resolve_factor_definition,
        extract_factor_expression,
    )
    from sqlalchemy import text as sa_text

    result = {
        "factor_name": factor_name,
        "symbol": symbol,
        "period": period,
        "value": None,
    }

    factor = resolve_factor_definition(db, factor_name)
    if not factor:
        result["error"] = f"Factor '{factor_name}' not found"
        return result

    result["id"] = factor.get("id")
    result["expression"] = extract_factor_expression(factor)
    result["description"] = factor.get("description") or ""
    result["category"] = factor.get("category")

    try:
        klines = klines_loader(period, 500)
        if klines and len(klines) >= 30:
            value, _, err = compute_factor_value(
                db=db,
                factor_name=factor_name,
                symbol=symbol,
                period=period,
                exchange=exchange,
                klines=klines,
            )
            if value is not None:
                result["value"] = value
            elif err:
                result["error"] = err
    except Exception as e:
        result["error"] = str(e)
        return result

    if include_effectiveness:
        row = db.execute(sa_text(
            "SELECT ic_mean, icir, win_rate, decay_half_life "
            "FROM factor_effectiveness "
            "WHERE factor_name = :fn AND symbol = :sym AND exchange = :ex "
            "AND period = '1h' AND forward_period = '4h' "
            "ORDER BY created_at DESC LIMIT 1"
        ), {"fn": factor_name, "sym": symbol, "ex": exchange}).fetchone()

        if row:
            result["ic"] = round(float(row[0]), 4) if row[0] is not None else None
            result["icir"] = round(float(row[1]), 2) if row[1] is not None else None
            result["win_rate"] = round(float(row[2]), 2) if row[2] is not None else None
            result["decay_half_life_hours"] = int(row[3]) if row[3] is not None else None

    return result


class DataProvider:
    """
    Provides market data to strategy scripts.
    Wraps existing data services for unified access.
    """

    def __init__(
        self,
        db: Session,
        account_id: int,
        environment: str = "mainnet",
        trading_client: Any = None,
        record_queries: bool = False,
        exchange: str = "hyperliquid"
    ):
        self.db = db
        self.account_id = account_id
        self.environment = environment
        self.trading_client = trading_client
        self.record_queries = record_queries
        self.exchange = exchange  # "hyperliquid" or "binance"
        self._query_log: List[Dict[str, Any]] = []
        self._kline_cache: Dict[str, List[Kline]] = {}
        self._account_cache: Optional[Dict[str, Any]] = None
        self._positions_cache: Optional[Dict[str, Position]] = None
        self._open_orders_cache: Optional[List[Order]] = None
        self._recent_trades_cache: Optional[List[Trade]] = None

    def _get_market_param(self) -> str:
        """Get market parameter for data services based on exchange."""
        return "binance" if self.exchange == "binance" else "CRYPTO"

    def _log_query(self, method: str, args: Dict[str, Any], result: Any) -> None:
        """Record a data query for preview run debugging."""
        if self.record_queries:
            self._query_log.append({
                "method": method,
                "args": args,
                "result": result
            })

    def get_query_log(self) -> List[Dict[str, Any]]:
        """Get all recorded data queries."""
        return self._query_log

    def get_price_change(self, symbol: str, period: str) -> Dict[str, float]:
        """Get price change for symbol over period.

        Returns:
            Dict with change_percent (percentage) and change_usd (absolute USD change)
        """
        from services.market_flow_indicators import get_flow_indicators_for_prompt
        import time

        current_time_ms = int(time.time() * 1000)
        result = {"change_percent": 0.0, "change_usd": 0.0}
        try:
            # Use get_flow_indicators_for_prompt to get full data structure
            # _get_price_change_data returns: {current, start_price, end_price, last_5, period}
            # Pass exchange parameter to route to correct data source
            results = get_flow_indicators_for_prompt(
                self.db, symbol, period, ["PRICE_CHANGE"], current_time_ms,
                exchange=self.exchange
            )
            data = results.get("PRICE_CHANGE")
            if data:
                change_pct = data.get("current", 0.0)
                start_price = data.get("start_price", 0.0)
                end_price = data.get("end_price", 0.0)
                change_usd = (end_price - start_price) if start_price and end_price else 0.0
                result = {
                    "change_percent": change_pct,
                    "change_usd": change_usd,
                }
        except Exception:
            pass
        self._log_query("get_price_change", {"symbol": symbol, "period": period, "exchange": self.exchange}, result)
        return result

    def get_klines(self, symbol: str, period: str, count: int = 50) -> List[Kline]:
        """Get K-line data from exchange API (real-time).

        Uses the same data source as AI Trader's {BTC_klines_15m} variable.
        Always fetches fresh data from exchange API, not from database.
        After fetching, backfills to database in background for backtest reuse.
        """
        from services.market_data import get_kline_data

        cache_key = f"{symbol}_{period}_{count}"
        if cache_key in self._kline_cache:
            klines = self._kline_cache[cache_key]
            self._log_query("get_klines", {"symbol": symbol, "period": period, "count": count},
                           {"count": len(klines), "cached": True})
            return klines

        klines = []
        try:
            # Use same API as AI Trader: get_kline_data()
            # Fetch more candles for indicator calculation, return requested count
            fetch_count = max(count, 100)  # At least 100 for indicator accuracy
            raw_data = get_kline_data(
                symbol=symbol,
                market=self._get_market_param(),
                period=period,
                count=fetch_count,
                environment=self.environment,
                persist=False  # Don't block on DB write
            )
            if raw_data:
                # Convert to Kline objects, take last 'count' candles
                all_klines = [
                    Kline(
                        timestamp=int(row.get('timestamp', 0)),
                        open=float(row.get('open', 0)),
                        high=float(row.get('high', 0)),
                        low=float(row.get('low', 0)),
                        close=float(row.get('close', 0)),
                        volume=float(row.get('volume', 0)),
                    )
                    for row in raw_data
                ]
                klines = all_klines[-count:] if len(all_klines) > count else all_klines
                # Cache the full fetch for indicator calculation reuse
                self._kline_cache[f"{symbol}_{period}_raw"] = raw_data
                self._kline_cache[cache_key] = klines
                # Backfill to database in background (non-blocking)
                self._backfill_klines_async(symbol, period, raw_data)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"get_klines failed for {symbol} {period}: {e}")
        self._log_query("get_klines", {"symbol": symbol, "period": period, "count": count},
                       {"count": len(klines)})
        return klines

    def _backfill_klines_async(self, symbol: str, period: str, raw_data: list):
        """Backfill kline data to database in a background thread (non-blocking)."""
        import threading
        import logging

        exchange = self.exchange
        environment = self.environment

        def _do_backfill():
            if environment != "mainnet":
                return
            try:
                from database.connection import SessionLocal
                from database.models import CryptoKline
                from datetime import datetime, timezone

                db = SessionLocal()
                try:
                    inserted = 0
                    for k in raw_data:
                        ts = int(k.get('timestamp', 0))
                        ts_sec = ts if ts < 1e12 else ts // 1000
                        existing = db.query(CryptoKline).filter(
                            CryptoKline.symbol == symbol,
                            CryptoKline.period == period,
                            CryptoKline.exchange == exchange,
                            CryptoKline.timestamp == ts_sec,
                        ).first()
                        if not existing:
                            dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
                            record = CryptoKline(
                                exchange=exchange, symbol=symbol,
                                market="CRYPTO", period=period,
                                timestamp=ts_sec,
                                datetime_str=dt.strftime("%Y-%m-%d %H:%M:%S"),
                                environment="mainnet",
                                open_price=float(k.get('open', 0) or 0),
                                high_price=float(k.get('high', 0) or 0),
                                low_price=float(k.get('low', 0) or 0),
                                close_price=float(k.get('close', 0) or 0),
                                volume=float(k.get('volume', 0) or 0),
                            )
                            db.add(record)
                            inserted += 1
                    if inserted > 0:
                        db.commit()
                        logging.getLogger(__name__).debug(
                            f"Backfilled {inserted} {exchange} klines for {symbol}/{period}"
                        )
                finally:
                    db.close()
            except Exception as e:
                logging.getLogger(__name__).debug(f"Kline backfill failed: {e}")

        thread = threading.Thread(target=_do_backfill, daemon=True)
        thread.start()

    def get_indicator(self, symbol: str, indicator: str, period: str) -> Dict[str, Any]:
        """Get technical indicator values based on real-time K-line data.

        Uses the same calculation flow as AI Trader:
        1. Fetch real-time K-line data from Hyperliquid API
        2. Calculate indicator using calculate_indicators()

        This ensures Programs and AI Trader see identical indicator values.
        """
        from services.market_data import get_kline_data
        from services.technical_indicators import calculate_indicators
        import logging

        logger = logging.getLogger(__name__)
        result = {}
        try:
            # Always use 500 candles for indicator calculation accuracy.
            # Do NOT reuse _raw cache from get_klines() which may have fewer
            # candles (e.g. 100 from a count=1 call), causing EMA100 to have
            # only 1 valid value instead of 400+.
            indicator_cache_key = f"{symbol}_{period}_indicator_raw"
            if indicator_cache_key in self._kline_cache:
                kline_data = self._kline_cache[indicator_cache_key]
            else:
                kline_data = get_kline_data(
                    symbol=symbol,
                    market=self._get_market_param(),
                    period=period,
                    count=500,
                    environment=self.environment,
                    persist=False
                )
                if kline_data:
                    self._kline_cache[indicator_cache_key] = kline_data

            if not kline_data:
                logger.warning(f"No kline data for indicator {indicator} on {symbol} {period}")
                self._log_query("get_indicator", {"symbol": symbol, "indicator": indicator, "period": period, "exchange": self.exchange}, result)
                return result

            # Calculate indicator using same function as AI Trader
            indicator_upper = indicator.upper()
            calculated = calculate_indicators(kline_data, [indicator_upper])

            if indicator_upper in calculated and calculated[indicator_upper] is not None:
                value = calculated[indicator_upper]
                # Return the latest value(s) - same format as old calculate_indicator()
                if isinstance(value, list):
                    result = {'value': value[-1] if value else None, 'series': value}
                elif isinstance(value, dict):
                    # For MACD, BOLL, STOCH etc. - return latest values
                    latest = {}
                    for k, v in value.items():
                        if isinstance(v, list) and v:
                            latest[k] = v[-1]
                        else:
                            latest[k] = v
                    result = latest
                else:
                    result = {'value': value}
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"get_indicator failed for {symbol} {indicator} {period}: {e}")
        self._log_query("get_indicator", {"symbol": symbol, "indicator": indicator, "period": period, "exchange": self.exchange}, result)
        return result

    def get_flow(self, symbol: str, metric: str, period: str) -> Dict[str, Any]:
        """Get market flow metrics (CVD, OI, TAKER, etc.).

        Returns full data structure including current value, last_5 history, etc.
        Example for CVD: {current: float, last_5: list, cumulative: float, period: str}
        Example for TAKER: {buy: float, sell: float, ratio: float, ratio_last_5: list, ...}
        """
        from services.market_flow_indicators import get_flow_indicators_for_prompt
        import time

        current_time_ms = int(time.time() * 1000)
        result = {}
        try:
            # Use get_flow_indicators_for_prompt to get full data structure
            # Pass exchange parameter to route to correct data source
            results = get_flow_indicators_for_prompt(
                self.db, symbol, period, [metric.upper()], current_time_ms,
                exchange=self.exchange
            )
            result = results.get(metric.upper(), {}) or {}
        except Exception:
            pass
        self._log_query("get_flow", {"symbol": symbol, "metric": metric, "period": period, "exchange": self.exchange}, result)
        return result

    def get_regime(self, symbol: str, period: str) -> RegimeInfo:
        """Get market regime classification using real-time data.

        Uses the same parameters as AI Trader: use_realtime=True ensures
        fresh market regime calculation instead of cached/historical data.
        """
        from services.market_regime_service import get_market_regime

        regime_info = RegimeInfo(regime="noise", conf=0.0)
        try:
            # Use use_realtime=True to match AI Trader behavior
            # Pass exchange parameter to route to correct data source
            result = get_market_regime(
                self.db, symbol, period, use_realtime=True, exchange=self.exchange
            )
            if result:
                regime_info = RegimeInfo(
                    regime=result.get("regime", "noise"),
                    conf=result.get("confidence", 0.0),
                    direction=result.get("direction", "neutral"),
                    reason=result.get("reason", ""),
                    indicators=result.get("indicators", {}),
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"get_regime failed for {symbol} {period}: {e}")
        self._log_query("get_regime", {"symbol": symbol, "period": period, "exchange": self.exchange}, {
            "regime": regime_info.regime,
            "conf": regime_info.conf,
            "direction": regime_info.direction
        })
        return regime_info

    def get_account_info(self) -> Dict[str, Any]:
        """Get account balance and margin info from trading client."""
        if self._account_cache is not None:
            return self._account_cache

        if not self.trading_client:
            # Fallback for backtest or when no trading client
            return {
                "available_balance": 10000.0,
                "total_equity": 10000.0,
                "used_margin": 0.0,
                "margin_usage_percent": 0.0,
                "maintenance_margin": 0.0,
            }

        try:
            state = self.trading_client.get_account_state(self.db)
            self._account_cache = {
                "available_balance": state.get("available_balance", 0.0),
                "total_equity": state.get("total_equity", 0.0),
                "used_margin": state.get("used_margin", 0.0),
                "margin_usage_percent": state.get("margin_usage_percent", 0.0),
                "maintenance_margin": state.get("maintenance_margin", 0.0),
            }
            return self._account_cache
        except Exception:
            return {
                "available_balance": 0.0,
                "total_equity": 0.0,
                "used_margin": 0.0,
                "margin_usage_percent": 0.0,
                "maintenance_margin": 0.0,
            }

    def get_positions(self) -> Dict[str, Position]:
        """Get current open positions from trading client."""
        if self._positions_cache is not None:
            return self._positions_cache

        if not self.trading_client:
            return {}

        try:
            # Request position timing information (aligned with AI Trader)
            raw_positions = self.trading_client.get_positions(self.db, include_timing=True)
            positions = {}
            for pos in raw_positions:
                # HyperliquidTradingClient returns 'coin', not 'symbol'
                symbol = pos.get("coin") or pos.get("symbol", "")
                if not symbol:
                    continue
                # Map field names: szi->size, entry_px->entry_price, etc.
                size = abs(float(pos.get("szi", 0) or pos.get("size", 0)))
                positions[symbol] = Position(
                    symbol=symbol,
                    side=pos.get("side", "long").lower(),
                    size=size,
                    entry_price=float(pos.get("entry_px", 0) or pos.get("entry_price", 0)),
                    unrealized_pnl=float(pos.get("unrealized_pnl", 0)),
                    leverage=int(float(pos.get("leverage", 1) or 1)),
                    liquidation_price=float(pos.get("liquidation_px", 0) or pos.get("liquidation_price", 0)),
                    # Position timing information
                    opened_at=pos.get("opened_at"),
                    opened_at_str=pos.get("opened_at_str"),
                    holding_duration_seconds=pos.get("holding_duration_seconds"),
                    holding_duration_str=pos.get("holding_duration_str"),
                )
            self._positions_cache = positions
            return positions
        except Exception:
            return {}

    def get_recent_trades(self, limit: int = 5) -> List[Trade]:
        """Get recent closed trades from trading client."""
        if self._recent_trades_cache is not None:
            return self._recent_trades_cache

        if not self.trading_client:
            return []

        try:
            raw_trades = self.trading_client.get_recent_closed_trades(self.db, limit)
            trades = []
            for t in raw_trades:
                trades.append(Trade(
                    symbol=t.get("symbol", ""),
                    side=t.get("side", ""),
                    size=float(t.get("size", 0)),
                    price=float(t.get("close_price", 0)),
                    timestamp=int(t.get("close_timestamp", 0)),
                    pnl=float(t.get("realized_pnl", 0)),
                    close_time=t.get("close_time", ""),
                ))
            self._recent_trades_cache = trades
            return trades
        except Exception:
            return []

    def get_open_orders(self, symbol: Optional[str] = None) -> List[Order]:
        """Get current open orders from trading client."""
        if self._open_orders_cache is not None:
            if symbol:
                return [o for o in self._open_orders_cache if o.symbol == symbol]
            return self._open_orders_cache

        if not self.trading_client:
            return []

        try:
            raw_orders = self.trading_client.get_open_orders(self.db, symbol)
            orders = []
            for o in raw_orders:
                orders.append(Order(
                    order_id=int(o.get("order_id", 0)),
                    symbol=o.get("symbol", ""),
                    side=o.get("side", ""),
                    direction=o.get("direction", ""),
                    order_type=o.get("order_type", ""),
                    size=float(o.get("size", 0)),
                    price=float(o.get("price", 0)),
                    trigger_price=float(o.get("trigger_price")) if o.get("trigger_price") else None,
                    reduce_only=o.get("reduce_only", False),
                    timestamp=int(o.get("timestamp", 0)),
                ))
            self._open_orders_cache = orders
            if symbol:
                return [o for o in orders if o.symbol == symbol]
            return orders
        except Exception:
            return []


    def get_market_data(self, symbol: str) -> Dict[str, Any]:
        """Get complete market data for a symbol (price, volume, OI, funding rate).

        Reuses the same data layer as AI Trader's {BTC_market_data} variable.
        Returns dict with fields: symbol, price, oracle_price, change24h, percentage24h,
        volume24h, open_interest, funding_rate.
        """
        from services.market_data import get_ticker_data

        # Check cache first
        cache_key = f"market_data_{symbol}"
        if not hasattr(self, '_market_data_cache'):
            self._market_data_cache = {}

        if cache_key in self._market_data_cache:
            result = self._market_data_cache[cache_key]
            self._log_query("get_market_data", {"symbol": symbol, "exchange": self.exchange}, {"cached": True, **result})
            return result

        # Call the same function AI Trader uses
        try:
            result = get_ticker_data(symbol, self._get_market_param(), self.environment)
            if result:
                self._market_data_cache[cache_key] = result
                self._log_query("get_market_data", {"symbol": symbol, "exchange": self.exchange}, result)
                return result
        except Exception as e:
            self._log_query("get_market_data", {"symbol": symbol, "exchange": self.exchange}, {"error": str(e)})

        return {}

    def get_factor(self, symbol: str, factor_name: str, period: str = "5m") -> Dict[str, Any]:
        """Get factor value and effectiveness for a symbol on a specific K-line period.

        `period` is explicit for new code. The default remains 5m only for
        backward compatibility with existing saved Programs.
        """
        from services.market_data import get_kline_data

        # Sync rule: keep this method aligned with Prompt factor variables and
        # HistoricalDataProvider.get_factor() in backtest mode.
        result = compute_factor_snapshot(
            db=self.db,
            symbol=symbol,
            factor_name=factor_name,
            period=period,
            exchange=self.exchange,
            klines_loader=lambda requested_period, count: get_kline_data(
                symbol,
                market=self._get_market_param(),
                period=requested_period,
                count=count,
                environment=self.environment,
                persist=False,
            ) or [],
            include_effectiveness=True,
        )

        self._log_query("get_factor", {"symbol": symbol, "factor_name": factor_name, "period": period}, result)
        return result

    def get_factor_ranking(self, symbol: str, top_n: int = 10) -> List[Dict[str, Any]]:
        """Get top factors ranked by |ICIR| for a symbol.

        Returns list of dicts with: factor_name, id, expression, description, ic, icir, win_rate, decay_half_life_hours.
        """
        from database.models import CustomFactor
        from sqlalchemy import text as sa_text

        results = []
        try:
            rows = self.db.execute(sa_text(
                "SELECT DISTINCT ON (factor_name) factor_name, ic_mean, icir, win_rate, decay_half_life "
                "FROM factor_effectiveness "
                "WHERE symbol = :sym AND exchange = :ex AND icir IS NOT NULL "
                "AND period = '1h' AND forward_period = '4h' "
                "ORDER BY factor_name, created_at DESC"
            ), {"sym": symbol, "ex": self.exchange}).fetchall()

            # Batch load factor metadata
            factor_names = [r[0] for r in rows]
            factors = self.db.query(CustomFactor).filter(
                CustomFactor.name.in_(factor_names),
                CustomFactor.is_active == True
            ).all()
            factor_meta = {f.name: f for f in factors}

            ranked = []
            for r in rows:
                fname = r[0]
                fobj = factor_meta.get(fname)
                entry = {
                    "factor_name": fname,
                    "id": fobj.id if fobj else None,
                    "expression": fobj.expression if fobj else None,
                    "description": (fobj.description or "") if fobj else "",
                    "ic": round(float(r[1]), 4) if r[1] is not None else None,
                    "icir": round(float(r[2]), 2) if r[2] is not None else None,
                    "win_rate": round(float(r[3]), 2) if r[3] is not None else None,
                    "decay_half_life_hours": int(r[4]) if r[4] is not None else None,
                }
                ranked.append(entry)
            ranked.sort(key=lambda x: abs(x["icir"] or 0), reverse=True)
            results = ranked[:top_n]

        except Exception as e:
            results = [{"error": str(e)}]

        self._log_query("get_factor_ranking", {"symbol": symbol, "top_n": top_n}, results)
        return results
