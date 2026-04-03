"""Runtime client for Hyper Insight wallet tracking integration.

This service intentionally keeps a single async connection loop to avoid
thread leaks. It stores only the latest access token in SystemConfig so the
client can recover across short restarts, but token refresh still depends on
the user revisiting HAA.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

import websockets
from websockets.client import WebSocketClientProtocol

from database.connection import SessionLocal
from database.models import SignalPool, SignalTriggerLog, SystemConfig

logger = logging.getLogger(__name__)

HYPER_INSIGHT_WS_URL = "wss://hyper.akooi.com/ws/events"
CONFIG_ENABLED = "hyper_insight_wallet_enabled"
CONFIG_ACCESS_TOKEN = "hyper_insight_wallet_access_token"
CONFIG_TOKEN_SYNCED_AT = "hyper_insight_wallet_token_synced_at"

MARKET_SIGNAL_SOURCE = "market_signals"
WALLET_TRACKING_SOURCE = "wallet_tracking"
WALLET_TRIGGER_SYMBOL = "WALLET"
MAX_RECENT_EVENT_KEYS = 4096


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_local_storage_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _parse_json_text(value: Any, fallback: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    if value is None:
        return fallback
    return value


class HyperInsightWalletService:
    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._runner_task: Optional[asyncio.Task] = None
        self._callback_worker_task: Optional[asyncio.Task] = None
        self._callback_queue: Optional[asyncio.Queue[tuple[str, dict[str, Any], dict[str, Any]]]] = None
        self._refresh_event: Optional[asyncio.Event] = None
        self._shutdown = False
        self._ws: Optional[WebSocketClientProtocol] = None
        self._state_lock = asyncio.Lock()
        self._recent_event_keys: deque[str] = deque(maxlen=MAX_RECENT_EVENT_KEYS)
        self._recent_event_key_set: set[str] = set()
        self._state: dict[str, Any] = {
            "enabled": False,
            "status": "disabled",
            "tier": None,
            "synced_addresses": [],
            "last_connected_at": None,
            "last_message_at": None,
            "last_event_at": None,
            "last_error": None,
            "active_wallet_pool_count": 0,
            "token_synced_at": None,
        }

    async def startup(self) -> None:
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        if self._callback_queue is None:
            self._callback_queue = asyncio.Queue(maxsize=100)
        if self._refresh_event is None:
            self._refresh_event = asyncio.Event()
        if self._runner_task is None or self._runner_task.done():
            self._shutdown = False
            self._runner_task = asyncio.create_task(self._runner_loop(), name="hyper-insight-wallet-service")
        if self._callback_worker_task is None or self._callback_worker_task.done():
            self._callback_worker_task = asyncio.create_task(
                self._callback_worker_loop(),
                name="hyper-insight-wallet-callback-worker",
            )
        await self.refresh_runtime()

    async def shutdown(self) -> None:
        self._shutdown = True
        if self._refresh_event is not None:
            self._refresh_event.set()
        await self._close_ws()
        if self._callback_worker_task and not self._callback_worker_task.done():
            self._callback_worker_task.cancel()
            try:
                await self._callback_worker_task
            except asyncio.CancelledError:
                pass
        self._callback_worker_task = None
        if self._runner_task and not self._runner_task.done():
            self._runner_task.cancel()
            try:
                await self._runner_task
            except asyncio.CancelledError:
                pass
        self._runner_task = None

    def request_refresh(self) -> None:
        if not self._loop or not self._refresh_event:
            return
        self._loop.call_soon_threadsafe(self._refresh_event.set)

    async def refresh_runtime(self) -> None:
        if self._refresh_event is not None:
            self._refresh_event.set()

    async def sync_access_token(self, access_token: str) -> None:
        previous_token = self._load_runtime_config().get("access_token") or ""
        timestamp = _utcnow_naive().isoformat()
        with SessionLocal() as db:
            self._set_config_value(db, CONFIG_ACCESS_TOKEN, access_token, "Latest Hyper Insight access token for runtime sync")
            self._set_config_value(db, CONFIG_TOKEN_SYNCED_AT, timestamp, "Last Hyper Insight token sync time")
            db.commit()
        async with self._state_lock:
            self._state["token_synced_at"] = timestamp
            self._state["last_error"] = None
        if previous_token and previous_token != access_token:
            await self._close_ws()
        await self.refresh_runtime()

    async def clear_access_token(self) -> None:
        with SessionLocal() as db:
            self._set_config_value(db, CONFIG_ACCESS_TOKEN, "", "Latest Hyper Insight access token for runtime sync")
            db.commit()
        await self._close_ws()
        async with self._state_lock:
            self._state["tier"] = None
            self._state["synced_addresses"] = []
            self._state["last_message_at"] = None
            self._state["last_event_at"] = None
            self._state["last_error"] = None
        await self.refresh_runtime()

    async def set_enabled(self, enabled: bool) -> None:
        with SessionLocal() as db:
            self._set_config_value(db, CONFIG_ENABLED, "true" if enabled else "false", "Whether Hyper Insight wallet tracking integration is enabled")
            db.commit()
        if not enabled:
            await self._close_ws()
        async with self._state_lock:
            self._state["enabled"] = enabled
            if not enabled:
                self._state["status"] = "disabled"
                self._state["tier"] = None
                self._state["synced_addresses"] = []
                self._state["last_message_at"] = None
                self._state["last_event_at"] = None
                self._state["last_error"] = None
        await self.refresh_runtime()

    def get_status_snapshot(self) -> dict[str, Any]:
        with SessionLocal() as db:
            enabled = self._get_config_value(db, CONFIG_ENABLED) == "true"
            token_synced_at = self._get_config_value(db, CONFIG_TOKEN_SYNCED_AT)
            active_wallet_pool_count = self._count_enabled_wallet_pools(db)
        snapshot = dict(self._state)
        snapshot["enabled"] = enabled
        snapshot["token_synced_at"] = token_synced_at
        snapshot["active_wallet_pool_count"] = active_wallet_pool_count
        snapshot["synced_addresses"] = list(snapshot.get("synced_addresses") or [])
        return snapshot

    def _set_config_value(self, db, key: str, value: str, description: str | None = None) -> None:
        row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        if row:
            row.value = value
            if description and not row.description:
                row.description = description
        else:
            row = SystemConfig(key=key, value=value, description=description)
            db.add(row)

    def _get_config_value(self, db, key: str) -> Optional[str]:
        row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        return row.value if row else None

    def _count_enabled_wallet_pools(self, db) -> int:
        return (
            db.query(SignalPool)
            .filter(
                SignalPool.enabled == True,  # noqa: E712
                SignalPool.is_deleted != True,  # noqa: E712
                SignalPool.source_type == WALLET_TRACKING_SOURCE,
            )
            .count()
        )

    async def _runner_loop(self) -> None:
        backoff_seconds = 1
        while not self._shutdown:
            runtime = self._load_runtime_config()
            await self._apply_idle_state(runtime)

            should_connect = runtime["enabled"] and runtime["access_token"]
            if not should_connect:
                if self._refresh_event is None:
                    await asyncio.sleep(1)
                else:
                    self._refresh_event.clear()
                    await self._refresh_event.wait()
                backoff_seconds = 1
                continue

            try:
                await self._connect_once(runtime["access_token"])
                backoff_seconds = 1
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                async with self._state_lock:
                    self._state["status"] = "error"
                    self._state["last_error"] = str(exc)
                logger.warning("[HyperInsight] Wallet runtime connection error: %s", exc)
                if self._refresh_event is None:
                    await asyncio.sleep(backoff_seconds)
                else:
                    self._refresh_event.clear()
                    try:
                        await asyncio.wait_for(self._refresh_event.wait(), timeout=backoff_seconds)
                    except asyncio.TimeoutError:
                        pass
                backoff_seconds = min(backoff_seconds * 2, 30)

    def _load_runtime_config(self) -> dict[str, Any]:
        with SessionLocal() as db:
            enabled = self._get_config_value(db, CONFIG_ENABLED) == "true"
            access_token = self._get_config_value(db, CONFIG_ACCESS_TOKEN) or ""
            token_synced_at = self._get_config_value(db, CONFIG_TOKEN_SYNCED_AT)
            active_wallet_pool_count = self._count_enabled_wallet_pools(db)
        return {
            "enabled": enabled,
            "access_token": access_token,
            "token_synced_at": token_synced_at,
            "active_wallet_pool_count": active_wallet_pool_count,
        }

    async def _apply_idle_state(self, runtime: dict[str, Any]) -> None:
        async with self._state_lock:
            self._state["enabled"] = runtime["enabled"]
            self._state["token_synced_at"] = runtime["token_synced_at"]
            self._state["active_wallet_pool_count"] = runtime["active_wallet_pool_count"]
            if not runtime["enabled"]:
                self._state["status"] = "disabled"
            elif not runtime["access_token"]:
                self._state["status"] = "waiting_for_token"
            elif self._state.get("status") not in {"connected", "connecting"}:
                self._state["status"] = "connecting"

    async def _connect_once(self, access_token: str) -> None:
        url = f"{HYPER_INSIGHT_WS_URL}?token={access_token}"
        async with self._state_lock:
            self._state["status"] = "connecting"
            self._state["last_error"] = None
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            self._ws = ws
            async with self._state_lock:
                self._state["status"] = "connected"
                self._state["last_connected_at"] = _utcnow_naive().isoformat()
            while not self._shutdown:
                try:
                    raw_message = await asyncio.wait_for(ws.recv(), timeout=30)
                except asyncio.TimeoutError:
                    continue
                self._touch_last_message()
                message = json.loads(raw_message)
                await self._handle_message(ws, message)

    def _touch_last_message(self) -> None:
        timestamp = _utcnow_naive().isoformat()
        self._state["last_message_at"] = timestamp

    async def _handle_message(self, ws: WebSocketClientProtocol, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        if message_type == "connected":
            async with self._state_lock:
                self._state["tier"] = message.get("tier")
                self._state["synced_addresses"] = list(message.get("addresses") or [])
            return
        if message_type == "subscription_update":
            address = message.get("address")
            action = message.get("action")
            async with self._state_lock:
                addresses = set(self._state.get("synced_addresses") or [])
                if address:
                    if action == "added":
                        addresses.add(address)
                    elif action == "removed":
                        addresses.discard(address)
                self._state["synced_addresses"] = sorted(addresses)
            return
        if message_type == "ping":
            await ws.send(json.dumps({"type": "pong"}))
            return
        if message_type == "error":
            detail = message.get("detail") or "Unknown upstream error"
            async with self._state_lock:
                self._state["status"] = "auth_error" if "unauthor" in detail.lower() else "error"
                self._state["last_error"] = detail
            raise RuntimeError(detail)

        if message.get("version") == 1 and message.get("address") and message.get("event_type"):
            await self._process_wallet_event(message)

    async def _process_wallet_event(self, event: dict[str, Any]) -> None:
        if self._is_duplicate_event(event):
            return

        triggered_at = self._event_timestamp_to_naive_datetime(event.get("timestamp"))
        event_address = str(event.get("address") or "").strip().lower()
        event_type = str(event.get("event_type") or "").strip()
        callback_payloads: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        with SessionLocal() as db:
            pools = (
                db.query(SignalPool)
                .filter(
                    SignalPool.enabled == True,  # noqa: E712
                    SignalPool.is_deleted != True,  # noqa: E712
                    SignalPool.source_type == WALLET_TRACKING_SOURCE,
                )
                .all()
            )

            for pool in pools:
                source_config = _parse_json_text(pool.source_config, {})
                if not isinstance(source_config, dict):
                    continue
                addresses = source_config.get("addresses") or []
                event_types = source_config.get("event_types") or []
                normalized_addresses = {
                    str(address).strip().lower()
                    for address in addresses
                    if isinstance(address, str) and address.strip()
                }
                if event_address not in normalized_addresses:
                    continue
                normalized_event_types = {
                    str(item).strip()
                    for item in event_types
                    if isinstance(item, str) and item.strip()
                }
                # Early test pools may still store "fill" from the raw realtime phase.
                # Treat aggregated position_change as a compatible successor so existing
                # wallet pools do not silently stop matching after the upstream cleanup.
                if event_type == "position_change" and "fill" in normalized_event_types:
                    normalized_event_types.add("position_change")
                if normalized_event_types and event_type not in normalized_event_types:
                    continue

                trigger_value = {
                    "source": "hyper_insight",
                    "source_type": WALLET_TRACKING_SOURCE,
                    "address": event_address,
                    "event_type": event_type,
                    "event_level": event.get("event_level"),
                    "tier": event.get("tier"),
                    "summary": event.get("summary"),
                    "detail": event.get("detail"),
                    "event_timestamp": event.get("timestamp"),
                }
                trigger_log = SignalTriggerLog(
                    signal_id=None,
                    pool_id=pool.id,
                    symbol=(event.get("symbol") or WALLET_TRIGGER_SYMBOL)[:20],
                    trigger_value=json.dumps(trigger_value),
                    triggered_at=triggered_at,
                    market_regime=None,
                )
                db.add(trigger_log)
                db.flush()
                callback_payloads.append(
                    (
                        (event.get("symbol") or WALLET_TRIGGER_SYMBOL)[:20],
                        {
                            "pool_id": pool.id,
                            "pool_name": pool.pool_name,
                            "logic": pool.logic or "OR",
                            "trigger_log_id": trigger_log.id,
                            # Downstream consumers first identify this as a wallet-origin event,
                            # then normalize it back into the existing signal family where needed.
                            "trigger_type": "wallet_signal",
                            "wallet_event": trigger_value,
                            "signals_triggered": [],
                        },
                        {},
                    )
                )

            db.commit()

        async with self._state_lock:
            self._state["last_event_at"] = triggered_at.isoformat()

        if callback_payloads and self._callback_queue is not None:
            for payload in callback_payloads:
                try:
                    self._callback_queue.put_nowait(payload)
                except asyncio.QueueFull:
                    logger.warning(
                        "[HyperInsightWallet] Callback queue full; dropping wallet callback for pool=%s symbol=%s",
                        payload[1].get("pool_id"),
                        payload[0],
                    )

    def _event_timestamp_to_naive_datetime(self, timestamp_ms: Any) -> datetime:
        if isinstance(timestamp_ms, (int, float)) and timestamp_ms > 0:
            return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
        return _utcnow_naive()

    def _build_event_key(self, event: dict[str, Any]) -> str:
        detail = event.get("detail") or {}
        detail_hash = detail.get("hash") if isinstance(detail, dict) else None
        return "|".join(
            [
                str(event.get("address") or ""),
                str(event.get("event_type") or ""),
                str(event.get("timestamp") or ""),
                str(detail_hash or ""),
            ]
        )

    def _is_duplicate_event(self, event: dict[str, Any]) -> bool:
        key = self._build_event_key(event)
        if key in self._recent_event_key_set:
            return True
        if len(self._recent_event_keys) == self._recent_event_keys.maxlen:
            oldest = self._recent_event_keys.popleft()
            self._recent_event_key_set.discard(oldest)
        self._recent_event_keys.append(key)
        self._recent_event_key_set.add(key)
        return False

    async def _close_ws(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    async def _callback_worker_loop(self) -> None:
        from services.signal_detection_service import signal_detection_service

        while not self._shutdown:
            try:
                if self._callback_queue is None:
                    await asyncio.sleep(0.1)
                    continue
                symbol, pool_trigger, market_data = await self._callback_queue.get()
                try:
                    await asyncio.to_thread(
                        signal_detection_service._notify_callbacks,
                        symbol,
                        pool_trigger,
                        market_data,
                    )
                finally:
                    self._callback_queue.task_done()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("[HyperInsightWallet] Callback worker error: %s", exc)


hyper_insight_wallet_service = HyperInsightWalletService()
