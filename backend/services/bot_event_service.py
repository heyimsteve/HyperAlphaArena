"""Bot Event Service - System event queue for Bot conversations.

Events (signal triggers, AI decisions) are saved as assistant messages
to the Bot conversation and pushed to all bound channels.
Push = broadcast to ALL bound channels; Reply = unicast to originating channel.
"""
import asyncio
import logging
from typing import Optional, Dict, Any, List
from sqlalchemy.orm import Session

from database.models import HyperAiConversation, HyperAiMessage, BotConfig

logger = logging.getLogger(__name__)


def _format_money(value: Any) -> Optional[str]:
    if not isinstance(value, (int, float)):
        return None
    abs_value = abs(float(value))
    if abs_value >= 1000:
        return f"${value:,.0f}"
    if abs_value >= 1:
        return f"${value:,.2f}"
    return f"${value:,.4f}"


def _format_price(value: Any) -> Optional[str]:
    if not isinstance(value, (int, float)):
        return None
    return f"{float(value):,.6f}".rstrip("0").rstrip(".")


def _wallet_action_label(action: Any) -> Optional[str]:
    mapping = {
        "open": "Opened",
        "add": "Increased",
        "reduce": "Reduced",
        "close": "Closed",
        "flip": "Flipped",
        "update": "Updated",
    }
    if not isinstance(action, str):
        return None
    return mapping.get(action.strip().lower(), action.strip().replace("_", " ").title())


def _wallet_direction_label(direction: Any) -> Optional[str]:
    mapping = {
        "long": "Long",
        "short": "Short",
        "flat": "Flat",
    }
    if not isinstance(direction, str):
        return None
    return mapping.get(direction.strip().lower(), direction.strip().replace("_", " ").title())


def _wallet_event_type_label(event_type: Any) -> Optional[str]:
    mapping = {
        "position_change": "Position Change",
        "equity_change": "Equity Change",
        "fill": "Trade Fill",
        "funding": "Funding",
        "transfer": "Transfer",
        "liquidation": "Liquidation",
    }
    if not isinstance(event_type, str):
        return None
    return mapping.get(event_type.strip().lower(), event_type.strip().replace("_", " ").title())


def get_bot_conversations(db: Session) -> List[HyperAiConversation]:
    """Get all Bot conversations (one per platform)."""
    return db.query(HyperAiConversation).filter(
        HyperAiConversation.is_bot_conversation == True
    ).all()


def get_connected_bot_configs(db: Session) -> List[BotConfig]:
    """Get all connected bot configurations."""
    return db.query(BotConfig).filter(
        BotConfig.status == "connected"
    ).all()


def enqueue_system_event(
    db: Session,
    event_type: str,
    event_data: Dict[str, Any],
    format_message: bool = True
) -> List[Dict[str, Any]]:
    """
    Enqueue a system event to all Bot conversations.

    Args:
        db: Database session
        event_type: Type of event (signal_triggered, ai_decision, etc.)
        event_data: Event payload
        format_message: If True, format event_data into readable message

    Returns:
        List of dicts with conversation_id, message_id, platform for push
    """
    # Format event into readable message
    if format_message:
        content = _format_event_message(event_type, event_data)
    else:
        content = event_data.get("message", str(event_data))

    results = []
    bot_convs = get_bot_conversations(db)

    for conv in bot_convs:
        # Save as assistant message (Bot-initiated)
        message = HyperAiMessage(
            conversation_id=conv.id,
            role="assistant",
            content=content,
            is_complete=True
        )
        db.add(message)
        db.flush()

        results.append({
            "conversation_id": conv.id,
            "message_id": message.id,
            "platform": conv.bot_platform,
            "content": content
        })

    db.commit()
    return results


def _format_event_message(event_type: str, data: Dict[str, Any]) -> str:
    """Format event data into human-readable message."""
    header = "【🤖 Hyper AI Notification】\n\n"

    if event_type == "signal_triggered":
        pool_name = data.get('pool_name', 'Unknown')
        symbol = data.get('symbol', 'N/A')
        wallet_event = data.get('wallet_event')
        if isinstance(wallet_event, dict):
            event_type_name = _wallet_event_type_label(wallet_event.get('event_type')) or "Wallet Event"
            summary = wallet_event.get('summary') or 'Wallet event triggered'
            detail = wallet_event.get('detail') if isinstance(wallet_event.get('detail'), dict) else {}
            address = str(wallet_event.get('address', ''))[:6]
            address_tail = str(wallet_event.get('address', ''))[-4:]
            short_address = f"{address}...{address_tail}" if address and address_tail else "N/A"
            action = _wallet_action_label(detail.get("action"))
            direction = _wallet_direction_label(detail.get("direction"))
            notional_value = _format_money(detail.get("notional_value"))
            closed_pnl = _format_money(detail.get("closed_pnl"))
            average_price = _format_price(detail.get("average_price"))

            extra_lines = []
            if action:
                if direction and direction != "Flat":
                    extra_lines.append(f"Action: {action} {direction}")
                else:
                    extra_lines.append(f"Action: {action}")
            elif direction:
                extra_lines.append(f"Direction: {direction}")

            extra_lines.append(f"Type: {event_type_name}")
            extra_lines.append(f"Wallet: {short_address}")
            extra_lines.append(f"Symbol: {symbol}")
            if notional_value:
                extra_lines.append(f"Notional: {notional_value}")
            if closed_pnl:
                extra_lines.append(f"Realized PnL: {closed_pnl}")
            if average_price:
                extra_lines.append(f"Avg Price: {average_price}")

            return (
                f"{header}"
                f"🔔 【{pool_name}】 Wallet signal triggered\n"
                f"{summary}\n"
                f"{chr(10).join(extra_lines)}"
            )
        triggered = data.get('triggered_signals', [])
        signals_text = ", ".join(
            f"{s.get('signal_name', s.get('metric', 'N/A'))}={s.get('current_value', 'N/A'):.4f}"
            if isinstance(s.get('current_value'), (int, float))
            else f"{s.get('signal_name', s.get('metric', 'N/A'))}={s.get('current_value', 'N/A')}"
            for s in triggered[:3]
        ) if triggered else "N/A"
        return (
            f"{header}"
            f"🔔 【{pool_name}】 {symbol} triggered\n"
            f"{signals_text}"
        )
    elif event_type == "ai_decision":
        trader = data.get('trader_name', 'AI Trader')
        op = data.get('operation', 'HOLD')
        symbol = data.get('symbol', 'N/A')
        portion = data.get('target_portion', 'N/A')
        reason = data.get('reason', '')
        return (
            f"{header}"
            f"🤖 【{trader}】 {op} {symbol} {portion}\n"
            f"{reason}"
        )
    elif event_type == "program_decision":
        program = data.get('program_name', 'Program')
        op = data.get('operation', 'HOLD')
        symbol = data.get('symbol', 'N/A')
        size = data.get('size_usd', 'N/A')
        leverage = data.get('leverage', '')
        reason = data.get('reason', '')
        return (
            f"{header}"
            f"⚙️ 【{program}】 {op} {symbol} {size} {leverage}\n"
            f"{reason}"
        )
    elif event_type == "trade_executed":
        return (
            f"{header}"
            f"✅ **Trade Executed**\n"
            f"Symbol: {data.get('symbol', 'N/A')}\n"
            f"Side: {data.get('side', 'N/A')}\n"
            f"Size: {data.get('size', 'N/A')}\n"
            f"Price: {data.get('price', 'N/A')}"
        )
    else:
        return f"{header}📢 {event_type}: {data.get('message', str(data))}"


async def push_event_to_all_channels(
    db: Session,
    event_results: List[Dict[str, Any]]
):
    """
    Push broadcast: send event notification to ALL bound channels on ALL connected platforms.
    Called after enqueue_system_event to notify users on Telegram/Discord/etc.

    Uses adapter pattern - automatically works with any registered platform adapter.
    """
    from database.models import BotChatBinding
    from services.bot_adapter import get_adapter

    if not event_results:
        return

    # Get content from first result (all results have the same content)
    content = event_results[0].get("content", "")
    if not content:
        return

    # Query all active bindings across all platforms
    bindings = db.query(BotChatBinding).filter(
        BotChatBinding.is_active == True
    ).all()

    for binding in bindings:
        adapter = get_adapter(binding.platform)
        if not adapter:
            logger.warning(f"No adapter registered for platform: {binding.platform}")
            continue

        if not adapter.is_ready():
            logger.debug(f"Adapter {binding.platform} not ready, skipping push to {binding.chat_id}")
            continue

        try:
            await adapter.send_message(binding.chat_id, content)
        except Exception as e:
            logger.error(f"Failed to push to {binding.platform} chat {binding.chat_id}: {e}")


# Legacy functions kept for backward compatibility during transition
# These can be removed once all callers use the adapter pattern

async def _push_to_telegram(db: Session, content: str):
    """[DEPRECATED] Use adapter pattern instead. Push message to all known Telegram chat_ids."""
    from services.bot_adapter import get_adapter
    from database.models import BotChatBinding

    adapter = get_adapter("telegram")
    if not adapter or not adapter.is_ready():
        return

    bindings = db.query(BotChatBinding).filter(
        BotChatBinding.platform == "telegram",
        BotChatBinding.is_active == True
    ).all()

    for binding in bindings:
        try:
            await adapter.send_message(binding.chat_id, content)
        except Exception as e:
            logger.error(f"Failed to push to Telegram chat {binding.chat_id}: {e}")


async def _push_to_discord(db: Session, content: str):
    """[DEPRECATED] Use adapter pattern instead. Push message to all known Discord user_ids."""
    from services.bot_adapter import get_adapter
    from database.models import BotChatBinding

    adapter = get_adapter("discord")
    if not adapter or not adapter.is_ready():
        logger.warning("Discord adapter not ready, skipping push")
        return

    bindings = db.query(BotChatBinding).filter(
        BotChatBinding.platform == "discord",
        BotChatBinding.is_active == True
    ).all()

    for binding in bindings:
        try:
            await adapter.send_message(binding.chat_id, content)
        except Exception as e:
            logger.error(f"Failed to push to Discord user {binding.chat_id}: {e}")
