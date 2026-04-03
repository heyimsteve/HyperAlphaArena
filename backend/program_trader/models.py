"""
Core data models for Program Trader.
Defines Strategy template, MarketData input, and Decision output structures.
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from abc import ABC, abstractmethod
from enum import Enum


class ActionType(str, Enum):
    """Trading action types."""
    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"
    HOLD = "hold"


@dataclass
class Kline:
    """K-line (candlestick) data."""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Position:
    """Current position information."""
    symbol: str
    side: str  # "long" or "short"
    size: float
    entry_price: float
    unrealized_pnl: float
    leverage: int
    liquidation_price: float
    # Position timing information (aligned with AI Trader's {positions_detail})
    opened_at: Optional[int] = None  # Timestamp in milliseconds when position was opened
    opened_at_str: Optional[str] = None  # Human-readable opened time (e.g., "2026-01-15 10:30:00 UTC")
    holding_duration_seconds: Optional[float] = None  # How long position has been held in seconds
    holding_duration_str: Optional[str] = None  # Human-readable duration (e.g., "2h 30m")


@dataclass
class Trade:
    """Historical trade record."""
    symbol: str
    side: str  # "Long" or "Short"
    size: float
    price: float
    timestamp: int  # milliseconds
    pnl: float
    close_time: str = ""  # UTC string format


@dataclass
class Order:
    """Open order information."""
    order_id: int
    symbol: str
    side: str  # "Buy" or "Sell"
    direction: str  # "Open Long", "Open Short", "Close Long", "Close Short"
    order_type: str  # "Limit", "Stop Limit", "Take Profit Limit"
    size: float
    price: float
    trigger_price: Optional[float] = None
    reduce_only: bool = False
    timestamp: int = 0  # milliseconds


@dataclass
class RegimeInfo:
    """Market regime classification result.

    Attributes:
        regime: Market regime type (breakout/absorption/stop_hunt/exhaustion/trap/continuation/noise)
        conf: Confidence score 0.0-1.0
        direction: Market direction (bullish/bearish/neutral)
        reason: Human-readable explanation of the regime classification
        indicators: Dict of indicator values used for classification
            - cvd_ratio: CVD / total notional
            - oi_delta: Open interest change %
            - taker_ratio: Taker buy/sell ratio
            - price_atr: Price change / ATR
            - rsi: RSI(14) value
    """
    regime: str
    conf: float
    direction: str = "neutral"
    reason: str = ""
    indicators: Dict[str, float] = field(default_factory=dict)


@dataclass
class Decision:
    """
    Strategy decision output - aligned with AI Trader output_format.

    Required fields:
    - operation: "buy" | "sell" | "hold" | "close"
    - symbol: Trading symbol (e.g., "BTC")
    - reason: Explanation of the decision
    - trading_strategy: Entry thesis, risk controls, exit plan

    Required for buy/sell/close:
    - target_portion_of_balance: float 0.1-1.0
    - leverage: int 1-50
    - max_price: required for "buy" or closing SHORT
    - min_price: required for "sell" or closing LONG

    Optional with defaults:
    - time_in_force: "Ioc" | "Gtc" | "Alo" (default: "Ioc")
    - take_profit_price: trigger price for profit taking
    - stop_loss_price: trigger price for loss protection
    - tp_execution: "market" | "limit" (default: "limit")
    - sl_execution: "market" | "limit" (default: "limit")
    """
    # Always required
    operation: str  # "buy" | "sell" | "hold" | "close"
    symbol: str
    reason: str = ""
    trading_strategy: str = ""

    # Required for buy/sell/close
    target_portion_of_balance: float = 0.0  # 0.1-1.0
    leverage: int = 10  # 1-50
    max_price: Optional[float] = None  # required for buy / close short
    min_price: Optional[float] = None  # required for sell / close long

    # Optional with defaults
    time_in_force: str = "Ioc"  # "Ioc" | "Gtc" | "Alo"
    take_profit_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    tp_execution: str = "limit"  # "market" | "limit"
    sl_execution: str = "limit"  # "market" | "limit"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "operation": self.operation,
            "symbol": self.symbol,
            "target_portion_of_balance": self.target_portion_of_balance,
            "leverage": self.leverage,
            "max_price": self.max_price,
            "min_price": self.min_price,
            "time_in_force": self.time_in_force,
            "take_profit_price": self.take_profit_price,
            "stop_loss_price": self.stop_loss_price,
            "tp_execution": self.tp_execution,
            "sl_execution": self.sl_execution,
            "reason": self.reason,
            "trading_strategy": self.trading_strategy,
        }


# Keep ActionType for backward compatibility (used in existing code)
# New code should use Decision.operation string directly


@dataclass
class MarketData:
    """
    Input data structure passed to strategy scripts.
    Provides access to account info, positions, and market data.

    Fields are aligned with AI Trader's prompt context variables to ensure
    Programs have access to the same information as AI Trader.
    """
    # Account info
    available_balance: float = 0.0
    total_equity: float = 0.0
    used_margin: float = 0.0
    margin_usage_percent: float = 0.0
    maintenance_margin: float = 0.0

    # Positions and trades
    positions: Dict[str, Position] = field(default_factory=dict)
    recent_trades: List[Trade] = field(default_factory=list)
    open_orders: List[Order] = field(default_factory=list)

    # Trigger info (basic)
    trigger_symbol: str = ""  # Symbol that triggered (empty string for scheduled triggers)
    trigger_type: str = "signal"  # "signal" or "scheduled"

    # Trigger context (detailed) - matches AI Trader's {trigger_context} variable
    signal_pool_name: str = ""  # Name of the signal pool that triggered
    pool_logic: str = "OR"  # "OR" or "AND" - how signals are combined
    triggered_signals: List[Dict] = field(default_factory=list)  # Full signal details
    signal_source_type: Optional[str] = None  # e.g. "wallet_tracking" for external wallet signals
    wallet_event: Optional[Dict[str, Any]] = None  # Hyper Insight wallet trigger payload

    # Trigger market regime snapshot - matches AI Trader's {trigger_market_regime}
    trigger_market_regime: Optional[RegimeInfo] = None  # Market regime at trigger time

    # Environment info - matches AI Trader's environment variables
    environment: str = "mainnet"  # "mainnet" or "testnet"
    max_leverage: int = 10  # Maximum allowed leverage
    default_leverage: int = 3  # Default leverage setting

    # Data provider (injected at runtime)
    _data_provider: Any = field(default=None, repr=False)

    def get_price(self, symbol: str) -> float:
        """Get current price for symbol."""
        if self._data_provider:
            # Try get_current_prices first (for backtest)
            if hasattr(self._data_provider, 'get_current_prices'):
                prices = self._data_provider.get_current_prices([symbol])
                if prices and symbol in prices:
                    return prices[symbol]
            # Fallback to get_market_data
            data = self._data_provider.get_market_data(symbol)
            if data and 'price' in data:
                return data['price']
        return 0.0

    def get_price_change(self, symbol: str, period: str) -> Dict[str, float]:
        """Get price change for symbol over period."""
        if self._data_provider:
            return self._data_provider.get_price_change(symbol, period)
        return {"change_percent": 0.0, "change_usd": 0.0}

    def get_klines(self, symbol: str, period: str, count: int = 50) -> List[Kline]:
        """Get K-line data."""
        if self._data_provider:
            return self._data_provider.get_klines(symbol, period, count)
        return []

    def get_indicator(self, symbol: str, indicator: str, period: str) -> Dict[str, Any]:
        """Get technical indicator values."""
        if self._data_provider:
            return self._data_provider.get_indicator(symbol, indicator, period)
        return {}

    def get_flow(self, symbol: str, metric: str, period: str) -> Dict[str, Any]:
        """Get market flow metrics."""
        if self._data_provider:
            return self._data_provider.get_flow(symbol, metric, period)
        return {}

    def get_regime(self, symbol: str, period: str) -> RegimeInfo:
        """Get market regime classification."""
        if self._data_provider:
            return self._data_provider.get_regime(symbol, period)
        return RegimeInfo(regime="noise", conf=0.0)

    def get_market_data(self, symbol: str) -> Dict[str, Any]:
        """Get complete market data (price, volume, OI, funding rate).

        Returns dict with fields: symbol, price, oracle_price, change24h,
        percentage24h, volume24h, open_interest, funding_rate.
        """
        if self._data_provider:
            return self._data_provider.get_market_data(symbol)
        return {}

    def get_factor(self, symbol: str, factor_name: str, period: str = "5m") -> Dict[str, Any]:
        """Get factor value and effectiveness metrics for a specific K-line period."""
        if self._data_provider:
            return self._data_provider.get_factor(symbol, factor_name, period)
        return {"factor_name": factor_name, "symbol": symbol, "period": period, "value": None}

    def get_factor_ranking(self, symbol: str, top_n: int = 10) -> List[Dict[str, Any]]:
        """Get top factors ranked by |ICIR|."""
        if self._data_provider:
            return self._data_provider.get_factor_ranking(symbol, top_n)
        return []


class Strategy(ABC):
    """
    Base class for all trading strategies.
    AI generates code that extends this class.
    """

    def __init__(self):
        self.params: Dict[str, Any] = {}

    def init(self, params: Dict[str, Any]) -> None:
        """
        Initialize strategy parameters.
        Override this method to set up strategy-specific parameters.
        """
        self.params = params

    @abstractmethod
    def should_trade(self, data: MarketData) -> Decision:
        """
        Main decision logic. Called each time signal triggers.

        Args:
            data: MarketData object with all market info

        Returns:
            Decision object with action and parameters
        """
        pass
