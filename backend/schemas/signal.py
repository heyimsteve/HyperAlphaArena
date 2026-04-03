"""Signal system schemas"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# Signal Definition schemas
class SignalCondition(BaseModel):
    """Single trigger condition"""
    metric: str
    operator: str
    threshold: float
    time_window: str = "5m"


class SignalCompositeCondition(BaseModel):
    """Composite trigger condition with AND/OR logic"""
    logic: str = "AND"
    conditions: List[SignalCondition]


class SignalDefinitionCreate(BaseModel):
    """Create signal definition request"""
    signal_name: str = Field(..., max_length=100)
    description: Optional[str] = None
    trigger_condition: Dict[str, Any]
    enabled: bool = True
    exchange: str = "hyperliquid"


class SignalDefinitionUpdate(BaseModel):
    """Update signal definition request"""
    signal_name: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    trigger_condition: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None
    exchange: Optional[str] = None


class SignalDefinitionResponse(BaseModel):
    """Signal definition response"""
    id: int
    signal_name: str
    description: Optional[str]
    trigger_condition: Dict[str, Any]
    enabled: bool
    exchange: str = "hyperliquid"
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Signal Pool schemas
class SignalPoolCreate(BaseModel):
    """Create signal pool request"""
    pool_name: str = Field(..., max_length=100)
    signal_ids: List[int] = Field(default_factory=list)
    symbols: List[str] = Field(default_factory=list)
    enabled: bool = True
    logic: str = "OR"  # AND or OR logic for signal triggering
    exchange: str = "hyperliquid"
    source_type: str = "market_signals"
    source_config: Dict[str, Any] = Field(default_factory=dict)


class SignalPoolUpdate(BaseModel):
    """Update signal pool request"""
    pool_name: Optional[str] = Field(None, max_length=100)
    signal_ids: Optional[List[int]] = None
    symbols: Optional[List[str]] = None
    enabled: Optional[bool] = None
    logic: Optional[str] = None  # AND or OR logic
    exchange: Optional[str] = None
    source_type: Optional[str] = None
    source_config: Optional[Dict[str, Any]] = None


class SignalPoolResponse(BaseModel):
    """Signal pool response"""
    id: int
    pool_name: str
    signal_ids: List[int]
    symbols: List[str]
    enabled: bool
    logic: str = "OR"  # AND or OR logic
    exchange: str = "hyperliquid"
    source_type: str = "market_signals"
    source_config: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    class Config:
        from_attributes = True


# Signal trigger log schemas
class SignalTriggerLogResponse(BaseModel):
    """Signal trigger log response"""
    id: int
    signal_id: Optional[int]
    pool_id: Optional[int]
    symbol: str
    trigger_value: Optional[Dict[str, Any]]
    triggered_at: datetime
    market_regime: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True


# List responses
class SignalListResponse(BaseModel):
    """List of signals response"""
    signals: List[SignalDefinitionResponse]
    pools: List[SignalPoolResponse]


class SignalTriggerLogsResponse(BaseModel):
    """List of trigger logs response"""
    logs: List[SignalTriggerLogResponse]
    total: int
