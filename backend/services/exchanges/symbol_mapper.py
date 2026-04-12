"""
Symbol mapping utilities for multi-exchange support.

Handles bidirectional conversion between internal symbol format (e.g., "BTC")
and exchange-specific formats (e.g., "BTCUSDT" for Binance).
"""

from typing import Dict, Optional
import logging

logger = logging.getLogger(__name__)


class SymbolMapper:
    """
    Bidirectional symbol mapper for exchange-specific symbol formats.

    Internal format: "BTC", "ETH" (base currency only)
    Binance format: "BTCUSDT", "ETHUSDT" (with quote currency suffix)
    Hyperliquid format: "BTC", "ETH" for standard perps, "xyz:GOLD" for HIP-3
    """

    # Quote currency for each exchange's perpetual contracts
    EXCHANGE_QUOTE_CURRENCY = {
        "binance": "USDT",
        "hyperliquid": "",  # Hyperliquid uses base currency only
    }

    # Special symbol mappings (if any symbol has non-standard naming)
    SPECIAL_MAPPINGS = {
        "binance": {
            # internal -> exchange
            "BTC": "BTCUSDT",
            "ETH": "ETHUSDT",
            # Add special cases here if needed
        }
    }

    REVERSE_MAPPINGS = {
        "binance": {
            # exchange -> internal
            "BTCUSDT": "BTC",
            "ETHUSDT": "ETH",
        }
    }

    _hip3_mappings: Dict[str, str] = {}

    @classmethod
    def register_hip3_mapping(cls, internal: str, exchange: str) -> None:
        """Register a Hyperliquid HIP-3 internal -> exchange symbol mapping."""
        internal_symbol = str(internal or "").upper()
        exchange_symbol = str(exchange or "")
        if not internal_symbol or not exchange_symbol:
            return
        cls._hip3_mappings[internal_symbol] = exchange_symbol

    @classmethod
    def is_hip3_symbol(cls, symbol: str) -> bool:
        """Return True when the symbol is a registered Hyperliquid HIP-3 symbol."""
        return str(symbol or "").upper() in cls._hip3_mappings

    @classmethod
    def clear_hip3_mappings(cls) -> None:
        """Clear registered HIP-3 mappings."""
        cls._hip3_mappings.clear()

    @classmethod
    def to_exchange(cls, symbol: str, exchange: str) -> str:
        """
        Convert internal symbol to exchange-specific format.

        Args:
            symbol: Internal symbol (e.g., "BTC")
            exchange: Exchange name (e.g., "binance")

        Returns:
            Exchange-specific symbol (e.g., "BTCUSDT")
        """
        exchange = exchange.lower()

        if exchange == "hyperliquid":
            upper = str(symbol or "").upper()
            if upper in cls._hip3_mappings:
                return cls._hip3_mappings[upper]
            return symbol

        # Check special mappings first
        special = cls.SPECIAL_MAPPINGS.get(exchange, {})
        if symbol in special:
            return special[symbol]

        # Default conversion: append quote currency
        quote = cls.EXCHANGE_QUOTE_CURRENCY.get(exchange, "")
        if quote:
            return f"{symbol}{quote}"

        return symbol

    @classmethod
    def to_internal(cls, symbol: str, exchange: str) -> str:
        """
        Convert exchange-specific symbol to internal format.

        Args:
            symbol: Exchange symbol (e.g., "BTCUSDT")
            exchange: Exchange name (e.g., "binance")

        Returns:
            Internal symbol (e.g., "BTC")
        """
        exchange = exchange.lower()

        if exchange == "hyperliquid":
            symbol_str = str(symbol or "")
            if symbol_str.lower().startswith("xyz:"):
                return symbol_str.split(":", 1)[1].upper()
            return symbol

        # Check reverse mappings first
        reverse = cls.REVERSE_MAPPINGS.get(exchange, {})
        if symbol in reverse:
            return reverse[symbol]

        # Default conversion: strip quote currency suffix
        quote = cls.EXCHANGE_QUOTE_CURRENCY.get(exchange, "")
        if quote and symbol.endswith(quote):
            return symbol[:-len(quote)]

        return symbol

    @classmethod
    def get_supported_symbols(cls, exchange: str) -> list:
        """Get list of supported symbols for an exchange."""
        # This could be expanded to fetch from exchange API
        return ["BTC", "ETH", "SOL", "DOGE", "XRP", "ADA", "AVAX", "LINK"]
