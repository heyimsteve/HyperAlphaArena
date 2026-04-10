"""
Backtest engine for Program Trader.
Simulates strategy execution on historical data.

DEPRECATED:
This is a legacy lightweight backtest engine kept for backward compatibility.
Do not add new backtest features here.

The active Program Trader backtest engine is implemented under backend/backtest/
and exposed through POST /api/programs/backtest. New backtest capabilities
(factor APIs, data alignment fixes, TP/SL behavior, exchange support, etc.)
must be added to backend/backtest/* instead of this file.
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
import time

from .models import MarketData, Decision, ActionType, Kline, Position, RegimeInfo


@dataclass
class BacktestTrade:
    """Record of a simulated trade."""
    timestamp: int
    symbol: str
    side: str  # "long" or "short"
    action: str  # "open" or "close"
    price: float
    size: float
    pnl: float = 0.0
    reason: str = ""


@dataclass
class BacktestResult:
    """Result of backtest run."""
    success: bool
    error: Optional[str] = None

    # Performance metrics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0

    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0

    # Equity curve
    equity_curve: List[Dict[str, Any]] = field(default_factory=list)

    # Trade history
    trades: List[BacktestTrade] = field(default_factory=list)


class BacktestDataProvider:
    """Provides historical data for backtesting.

    Deprecated legacy provider. Do not extend this class for new backtest
    features. Use backend/backtest/historical_data_provider.py instead.
    """

    def __init__(self, klines: Dict[str, List[Kline]], indicators: Dict = None):
        self.klines = klines  # {symbol_period: [Kline, ...]}
        self.indicators = indicators or {}
        self.current_index = 0

    def get_klines(self, symbol: str, period: str, count: int = 50) -> List[Kline]:
        key = f"{symbol}_{period}"
        if key not in self.klines:
            return []
        end_idx = min(self.current_index + 1, len(self.klines[key]))
        start_idx = max(0, end_idx - count)
        return self.klines[key][start_idx:end_idx]

    def get_indicator(self, symbol: str, indicator: str, period: str) -> Dict:
        key = f"{symbol}_{indicator}_{period}_{self.current_index}"
        return self.indicators.get(key, {})

    def get_flow(self, symbol: str, metric: str, period: str) -> Dict:
        key = f"{symbol}_{metric}_{period}_{self.current_index}"
        return self.indicators.get(key, {})

    def get_regime(self, symbol: str, period: str) -> RegimeInfo:
        key = f"{symbol}_regime_{period}_{self.current_index}"
        data = self.indicators.get(key, {})
        return RegimeInfo(
            regime=data.get("regime", "noise"),
            conf=data.get("conf", 0.0),
        )

    def get_price_change(self, symbol: str, period: str) -> Dict:
        return {"change_percent": 0.0, "change_usd": 0.0}

    def get_factor(self, symbol: str, factor_name: str) -> Dict:
        """Compute factor value from historical K-line slice at current_index."""
        from database.connection import SessionLocal
        from services.factor_resolver import (
            compute_factor_value,
            resolve_factor_definition,
            extract_factor_expression,
        )

        result = {"factor_name": factor_name, "symbol": symbol, "value": None}
        try:
            db = SessionLocal()
            try:
                factor = resolve_factor_definition(db, factor_name)
                if not factor:
                    result["error"] = f"Factor '{factor_name}' not found"
                    return result
            finally:
                db.close()

            # Use 5m klines up to current_index
            key = f"{symbol}_5m"
            if key not in self.klines:
                return result
            end_idx = min(self.current_index + 1, len(self.klines[key]))
            slice_klines = self.klines[key][max(0, end_idx - 300):end_idx]
            if len(slice_klines) < 30:
                return result

            # Convert Kline objects to dicts for expression engine
            kline_dicts = [
                {"open": k.open, "high": k.high, "low": k.low,
                 "close": k.close, "volume": k.volume, "timestamp": k.timestamp}
                for k in slice_klines
            ]
            result["expression"] = extract_factor_expression(factor)
            result["description"] = factor.get("description") or ""
            result["category"] = factor.get("category")
            result["id"] = factor.get("id")
            db = SessionLocal()
            try:
                value, _, err = compute_factor_value(
                    db=db,
                    factor_name=factor_name,
                    symbol=symbol,
                    period="5m",
                    exchange=self.exchange,
                    klines=kline_dicts,
                )
            finally:
                db.close()
            if value is not None:
                result["value"] = value
            elif err:
                result["error"] = err
        except Exception as e:
            result["error"] = str(e)
        return result

    def get_factor_ranking(self, symbol: str, top_n: int = 10) -> List[Dict]:
        """Not available in backtest mode."""
        return []


class BacktestEngine:
    """Runs backtest simulation on historical data.

    Deprecated legacy engine. The production Program Trader backtest path uses
    backend/backtest/engine.py (ProgramBacktestEngine), not this class.
    """

    def __init__(
        self,
        initial_balance: float = 10000.0,
        fee_rate: float = 0.0006,  # 0.06% taker fee
    ):
        self.initial_balance = initial_balance
        self.fee_rate = fee_rate

    def run(
        self,
        code: str,
        klines: Dict[str, List[Kline]],
        symbol: str,
        period: str = "5m",
        params: Dict[str, Any] = None,
    ) -> BacktestResult:
        """Run backtest on historical klines."""
        from .executor import SandboxExecutor

        if not klines or f"{symbol}_{period}" not in klines:
            return BacktestResult(success=False, error="No kline data provided")

        kline_data = klines[f"{symbol}_{period}"]
        if len(kline_data) < 10:
            return BacktestResult(success=False, error="Insufficient kline data")

        # Initialize state
        balance = self.initial_balance
        position: Optional[Position] = None
        trades: List[BacktestTrade] = []
        equity_curve: List[Dict] = []
        peak_equity = self.initial_balance
        max_drawdown = 0.0

        # Create data provider and executor
        data_provider = BacktestDataProvider(klines)
        executor = SandboxExecutor(timeout_seconds=2)

        # Iterate through klines
        for i in range(50, len(kline_data)):
            data_provider.current_index = i
            current_kline = kline_data[i]
            current_price = current_kline.close

            # Build market data
            market_data = MarketData(
                available_balance=balance,
                total_equity=balance + (self._calc_unrealized_pnl(position, current_price) if position else 0),
                trigger_symbol=symbol,
                trigger_type="signal",
                prices={symbol: current_price},
                positions={symbol: position} if position else {},
                _data_provider=data_provider,
            )

            # Execute strategy
            result = executor.execute(code, market_data, params or {})
            if not result.success:
                continue

            decision = result.decision
            if not decision:
                continue

            # Process decision
            if decision.action == ActionType.BUY and position is None:
                # Open long
                size = min(decision.size_usd, balance * 0.95) / current_price
                fee = size * current_price * self.fee_rate
                balance -= fee
                position = Position(
                    symbol=symbol, side="long", size=size,
                    entry_price=current_price, unrealized_pnl=0,
                    leverage=decision.leverage, liquidation_price=0,
                )
                trades.append(BacktestTrade(
                    timestamp=current_kline.timestamp, symbol=symbol,
                    side="long", action="open", price=current_price,
                    size=size, reason=decision.reason,
                ))

            elif decision.action == ActionType.SELL and position is None:
                # Open short
                size = min(decision.size_usd, balance * 0.95) / current_price
                fee = size * current_price * self.fee_rate
                balance -= fee
                position = Position(
                    symbol=symbol, side="short", size=size,
                    entry_price=current_price, unrealized_pnl=0,
                    leverage=decision.leverage, liquidation_price=0,
                )
                trades.append(BacktestTrade(
                    timestamp=current_kline.timestamp, symbol=symbol,
                    side="short", action="open", price=current_price,
                    size=size, reason=decision.reason,
                ))

            elif decision.action == ActionType.CLOSE and position is not None:
                # Close position
                pnl = self._calc_realized_pnl(position, current_price)
                fee = position.size * current_price * self.fee_rate
                balance += pnl - fee
                trades.append(BacktestTrade(
                    timestamp=current_kline.timestamp, symbol=symbol,
                    side=position.side, action="close", price=current_price,
                    size=position.size, pnl=pnl, reason=decision.reason,
                ))
                position = None

            # Record equity
            equity = balance + (self._calc_unrealized_pnl(position, current_price) if position else 0)
            equity_curve.append({"timestamp": current_kline.timestamp, "equity": equity})

            # Track drawdown
            if equity > peak_equity:
                peak_equity = equity
            drawdown = (peak_equity - equity) / peak_equity
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        # Calculate final metrics
        return self._calculate_metrics(trades, equity_curve, max_drawdown)

    def _calc_unrealized_pnl(self, position: Position, current_price: float) -> float:
        if position.side == "long":
            return (current_price - position.entry_price) * position.size
        else:
            return (position.entry_price - current_price) * position.size

    def _calc_realized_pnl(self, position: Position, exit_price: float) -> float:
        return self._calc_unrealized_pnl(position, exit_price)

    def _calculate_metrics(
        self, trades: List[BacktestTrade], equity_curve: List[Dict], max_drawdown: float
    ) -> BacktestResult:
        close_trades = [t for t in trades if t.action == "close"]
        winning = [t for t in close_trades if t.pnl > 0]
        losing = [t for t in close_trades if t.pnl <= 0]

        total_pnl = sum(t.pnl for t in close_trades)
        win_rate = len(winning) / len(close_trades) if close_trades else 0.0

        return BacktestResult(
            success=True,
            total_trades=len(close_trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate=win_rate,
            total_pnl=total_pnl,
            max_drawdown=max_drawdown,
            equity_curve=equity_curve,
            trades=trades,
        )
