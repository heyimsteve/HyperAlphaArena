from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database.connection import SessionLocal
from repositories import prompt_repo
from database.models import PromptTemplate, Account
from schemas.prompt import (
    PromptListResponse,
    PromptTemplateUpdateRequest,
    PromptTemplateResponse,
    PromptBindingUpsertRequest,
    PromptBindingResponse,
    PromptTemplateCopyRequest,
    PromptTemplateCreateRequest,
    PromptTemplateNameUpdateRequest,
)


router = APIRouter(prefix="/api/prompts", tags=["Prompt Templates"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Support both /api/prompts and /api/prompts/
@router.get("", response_model=PromptListResponse, response_model_exclude_none=True)
@router.get("/", response_model=PromptListResponse, response_model_exclude_none=True)
def list_prompt_templates(db: Session = Depends(get_db)) -> PromptListResponse:
    templates = prompt_repo.get_all_templates(db)
    bindings = prompt_repo.list_bindings(db)

    template_responses = [
        PromptTemplateResponse.from_orm(template)
        for template in templates
    ]

    binding_responses = []
    for binding, account, template in bindings:
        binding_responses.append(
            PromptBindingResponse(
                id=binding.id,
                account_id=account.id,
                account_name=account.name,
                account_model=account.model,
                prompt_template_id=binding.prompt_template_id,
                prompt_key=template.key,
                prompt_name=template.name,
                updated_by=binding.updated_by,
                updated_at=binding.updated_at,
            )
        )

    return PromptListResponse(templates=template_responses, bindings=binding_responses)


@router.put("/{key}", response_model=PromptTemplateResponse, response_model_exclude_none=True)
def update_prompt_template(
    key: str,
    payload: PromptTemplateUpdateRequest,
    db: Session = Depends(get_db),
) -> PromptTemplateResponse:
    try:
        template = prompt_repo.update_template(
            db,
            key=key,
            template_text=payload.template_text,
            description=payload.description,
            updated_by=payload.updated_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return PromptTemplateResponse.from_orm(template)


# Restore endpoint removed - dangerous operation that overwrites user customizations


@router.post("", response_model=PromptTemplateResponse, response_model_exclude_none=True)
@router.post("/", response_model=PromptTemplateResponse, response_model_exclude_none=True)
def create_prompt_template(
    payload: PromptTemplateCreateRequest,
    db: Session = Depends(get_db),
) -> PromptTemplateResponse:
    """Create a new user-defined prompt template"""
    try:
        template = prompt_repo.create_user_template(
            db,
            name=payload.name,
            description=payload.description,
            template_text=payload.template_text,
            created_by=payload.created_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return PromptTemplateResponse.from_orm(template)


@router.post(
    "/{template_id}/copy",
    response_model=PromptTemplateResponse,
    response_model_exclude_none=True,
)
def copy_prompt_template(
    template_id: int,
    payload: PromptTemplateCopyRequest,
    db: Session = Depends(get_db),
) -> PromptTemplateResponse:
    """Copy an existing template to create a new one"""
    try:
        template = prompt_repo.copy_template(
            db,
            template_id=template_id,
            new_name=payload.new_name,
            created_by=payload.created_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return PromptTemplateResponse.from_orm(template)


@router.delete("/{template_id}")
def delete_prompt_template_endpoint(template_id: int, db: Session = Depends(get_db)) -> dict:
    """Soft delete a prompt template with dependency checking."""
    from services.entity_deletion_service import delete_prompt_template
    result = delete_prompt_template(db, template_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "Template not found"))
    return result


@router.patch(
    "/{template_id}/name",
    response_model=PromptTemplateResponse,
    response_model_exclude_none=True,
)
def update_prompt_template_name(
    template_id: int,
    payload: PromptTemplateNameUpdateRequest,
    db: Session = Depends(get_db),
) -> PromptTemplateResponse:
    """Update template name and description"""
    try:
        template = prompt_repo.update_template_name(
            db,
            template_id=template_id,
            name=payload.name,
            description=payload.description,
            updated_by=payload.updated_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return PromptTemplateResponse.from_orm(template)


@router.post(
    "/bindings",
    response_model=PromptBindingResponse,
    response_model_exclude_none=True,
)
def upsert_prompt_binding(
    payload: PromptBindingUpsertRequest,
    db: Session = Depends(get_db),
) -> PromptBindingResponse:
    if not payload.account_id:
        raise HTTPException(status_code=400, detail="accountId is required")
    if not payload.prompt_template_id:
        raise HTTPException(status_code=400, detail="promptTemplateId is required")

    account = db.get(Account, payload.account_id)
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    template = db.get(PromptTemplate, payload.prompt_template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Prompt template not found")

    try:
        binding = prompt_repo.upsert_binding(
            db,
            account_id=payload.account_id,
            prompt_template_id=payload.prompt_template_id,
            updated_by=payload.updated_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return PromptBindingResponse(
        id=binding.id,
        account_id=account.id,
        account_name=account.name,
        account_model=account.model,
        prompt_template_id=binding.prompt_template_id,
        prompt_key=template.key,
        prompt_name=template.name,
        updated_by=binding.updated_by,
        updated_at=binding.updated_at,
    )


@router.delete("/bindings/{binding_id}")
def delete_prompt_binding_endpoint(binding_id: int, db: Session = Depends(get_db)) -> dict:
    """Soft delete a prompt binding."""
    from services.entity_deletion_service import delete_prompt_binding
    result = delete_prompt_binding(db, binding_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "Binding not found"))
    return result


@router.post("/preview")
def preview_prompt(
    payload: dict,
    db: Session = Depends(get_db),
) -> dict:
    """
    Preview filled prompt for selected accounts and symbols.

    Payload:
    {
        "templateText": "...",  # Optional: Use this template text directly (for preview before save)
        "promptTemplateKey": "pro",  # Optional: Fallback to database template if templateText not provided
        "accountIds": [1, 2],
        "symbols": ["BTC", "ETH"],
        "exchanges": ["hyperliquid", "binance"]  # Optional: Array of exchanges (default: ["hyperliquid"])
    }

    Returns:
    {
        "previews": [
            {
                "accountId": 1,
                "accountName": "Trader-A",
                "exchange": "hyperliquid",
                "filledPrompt": "..."
            },
            ...
        ]
    }
    """
    from services.ai_decision_service import (
        _get_portfolio_data,
        _build_prompt_context,
        SafeDict,
        SUPPORTED_SYMBOLS,
    )
    from services.market_data import get_last_price
    from services.news_feed import fetch_latest_news
    from services.sampling_pool import sampling_pool
    from database.models import Account
    import logging
    from services.hyperliquid_symbol_service import (
        get_selected_symbols as get_hyperliquid_selected_symbols,
        get_available_symbol_map as get_hyperliquid_symbol_map,
    )
    from services.binance_symbol_service import (
        get_selected_symbols as get_binance_selected_symbols,
    )

    logger = logging.getLogger(__name__)

    # Priority: use templateText if provided (for preview before save), otherwise query from database
    template_text = payload.get("templateText")
    prompt_key = payload.get("promptTemplateKey", "default")
    account_ids = payload.get("accountIds", [])
    # Support both old "exchange" (string) and new "exchanges" (array) format
    exchanges = payload.get("exchanges") or [payload.get("exchange", "hyperliquid")]

    raw_symbols = [str(sym).upper() for sym in payload.get("symbols", []) if sym]
    requested_symbols: List[str] = []
    seen_requested = set()
    for symbol in raw_symbols:
        if symbol and symbol not in seen_requested:
            seen_requested.add(symbol)
            requested_symbols.append(symbol)

    base_symbol_order = list(SUPPORTED_SYMBOLS.keys())
    hyper_watchlist = get_hyperliquid_selected_symbols()
    hyper_symbol_map = get_hyperliquid_symbol_map()

    if not account_ids:
        raise HTTPException(status_code=400, detail="At least one account must be selected")

    # Get template text: use provided templateText or query from database
    if not template_text:
        # Fallback: query from database using promptTemplateKey
        template = prompt_repo.get_template_by_key(db, prompt_key)
        if not template:
            raise HTTPException(status_code=404, detail=f"Prompt template '{prompt_key}' not found")
        template_text = template.template_text
        logger.info(f"Preview: Using database template '{prompt_key}'")
    else:
        logger.info(f"Preview: Using provided templateText (length: {len(template_text)})")

    # Get news
    try:
        news_summary = fetch_latest_news()
        news_section = news_summary if news_summary else "No recent CoinJournal news available."
    except Exception as err:
        logger.warning(f"Failed to fetch news: {err}")
        news_section = "No recent CoinJournal news available."

    # Import multi-symbol sampling data builder
    from services.ai_decision_service import _build_multi_symbol_sampling_data

    previews = []

    for account_id in account_ids:
        account = db.get(Account, account_id)
        if not account:
            logger.warning(f"Account {account_id} not found, skipping")
            continue

        # Generate preview for each selected exchange
        for exchange in exchanges:
            try:
                preview_result = _generate_single_preview(
                    db=db,
                    account=account,
                    exchange=exchange,
                    template_text=template_text,
                    news_section=news_section,
                    requested_symbols=requested_symbols,
                    base_symbol_order=base_symbol_order,
                    hyper_watchlist=hyper_watchlist,
                    hyper_symbol_map=hyper_symbol_map,
                    sampling_pool=sampling_pool,
                    logger=logger,
                    SUPPORTED_SYMBOLS=SUPPORTED_SYMBOLS,
                    get_last_price=get_last_price,
                    _get_portfolio_data=_get_portfolio_data,
                    _build_prompt_context=_build_prompt_context,
                    _build_multi_symbol_sampling_data=_build_multi_symbol_sampling_data,
                    SafeDict=SafeDict,
                )
                previews.append(preview_result)
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Failed to generate preview for {account.name} ({exchange}): {e}")
                previews.append({
                    "accountId": account.id,
                    "accountName": account.name,
                    "exchange": exchange,
                    "symbols": requested_symbols if requested_symbols else [],
                    "filledPrompt": f"Error generating preview: {str(e)[:200]}",
                })

    return {"previews": previews}


def _generate_single_preview(
    db,
    account,
    exchange: str,
    template_text: str,
    news_section: str,
    requested_symbols,
    base_symbol_order,
    hyper_watchlist,
    hyper_symbol_map,
    sampling_pool,
    logger,
    SUPPORTED_SYMBOLS,
    get_last_price,
    _get_portfolio_data,
    _build_prompt_context,
    _build_multi_symbol_sampling_data,
    SafeDict,
) -> dict:
    """Generate a single preview for one account and one exchange."""
    from typing import Dict

    hyperliquid_state = None
    binance_state = None
    portfolio = None
    environment = "mainnet"

    if exchange == "binance":
        from services.binance_trading_client import BinanceTradingClient
        from database.models import BinanceWallet
        from utils.encryption import decrypt_private_key
        from services.hyperliquid_environment import get_global_trading_mode

        # Get global trading environment (same as Hyperliquid)
        binance_environment = get_global_trading_mode(db)
        environment = binance_environment

        binance_wallet = db.query(BinanceWallet).filter(
            BinanceWallet.account_id == account.id,
            BinanceWallet.environment == binance_environment,
            BinanceWallet.is_active == "true"
        ).first()

        # IMPORTANT: If wallet not configured or API error, use N/A values but continue prompt generation.
        # Any data fetch error should NOT block prompt generation - just show N/A for that section.
        if not binance_wallet or not binance_wallet.api_key_encrypted:
            logger.warning(f"No Binance {binance_environment} wallet for {account.name}, using N/A values")
            portfolio = {
                'cash': 'N/A',
                'frozen_cash': 'N/A',
                'positions': {},
                'total_assets': 'N/A'
            }
            binance_state = None
        else:
            try:
                api_key = decrypt_private_key(binance_wallet.api_key_encrypted)
                secret_key = decrypt_private_key(binance_wallet.secret_key_encrypted)

                client = BinanceTradingClient(
                    api_key=api_key,
                    secret_key=secret_key,
                    environment=binance_environment
                )

                account_state = client.get_account_state(db)
                positions = client.get_positions()

                portfolio = {
                    'cash': account_state['available_balance'],
                    'frozen_cash': account_state.get('used_margin', 0),
                    'positions': {},
                    'total_assets': account_state['total_equity']
                }

                for pos in positions:
                    symbol = pos.get('coin') or pos.get('symbol', '')
                    portfolio['positions'][symbol] = {
                        'quantity': pos.get('szi') or pos.get('size', 0),
                        'avg_cost': pos.get('entry_px') or pos.get('entry_price', 0),
                        'current_value': pos.get('position_value', 0),
                        'unrealized_pnl': pos.get('unrealized_pnl', 0),
                        'leverage': pos.get('leverage', 1)
                    }

                binance_state = {
                    'total_equity': account_state['total_equity'],
                    'available_balance': account_state['available_balance'],
                    'used_margin': account_state.get('used_margin', 0),
                    'margin_usage_percent': account_state.get('margin_usage_percent', 0),
                    'maintenance_margin': account_state.get('maintenance_margin', 0),
                    'positions': positions
                }

                logger.info(f"Preview: Using Binance {environment} data for {account.name}")
            except Exception as bn_err:
                # API error - use N/A values, but continue with prompt generation
                logger.warning(f"Failed to get Binance data for {account.name}: {bn_err}, using N/A values")
                portfolio = {
                    'cash': 'N/A',
                    'frozen_cash': 'N/A',
                    'positions': {},
                    'total_assets': 'N/A'
                }
                binance_state = None

    else:
        # Hyperliquid exchange
        from services.hyperliquid_environment import get_global_trading_mode, get_hyperliquid_client
        from database.models import HyperliquidWallet

        hyperliquid_environment = get_global_trading_mode(db)
        environment = hyperliquid_environment

        if hyperliquid_environment in ["testnet", "mainnet"]:
            # IMPORTANT: Check wallet exists BEFORE calling get_hyperliquid_client()
            # If wallet not configured, use N/A values but still generate the prompt.
            # Any data fetch error should NOT block prompt generation.
            wallet = db.query(HyperliquidWallet).filter(
                HyperliquidWallet.account_id == account.id,
                HyperliquidWallet.environment == hyperliquid_environment,
                HyperliquidWallet.is_active == "true"
            ).first()

            if not wallet or not wallet.private_key_encrypted:
                # No wallet - use N/A values, but continue with prompt generation
                logger.warning(f"No Hyperliquid {hyperliquid_environment} wallet for {account.name}, using N/A values")
                portfolio = {
                    'cash': 'N/A',
                    'frozen_cash': 'N/A',
                    'positions': {},
                    'total_assets': 'N/A'
                }
                hyperliquid_state = None
            else:
                try:
                    client = get_hyperliquid_client(db, account.id, override_environment=hyperliquid_environment)
                    account_state = client.get_account_state(db)
                    positions = client.get_positions(db, include_timing=True)

                    portfolio = {
                        'cash': account_state['available_balance'],
                        'frozen_cash': account_state.get('used_margin', 0),
                        'positions': {},
                        'total_assets': account_state['total_equity']
                    }

                    for pos in positions:
                        symbol = pos['coin']
                        portfolio['positions'][symbol] = {
                            'quantity': pos['szi'],
                            'avg_cost': pos['entry_px'],
                            'current_value': pos['position_value'],
                            'unrealized_pnl': pos['unrealized_pnl'],
                            'leverage': pos['leverage']
                        }

                    hyperliquid_state = {
                        'total_equity': account_state['total_equity'],
                        'available_balance': account_state['available_balance'],
                        'used_margin': account_state.get('used_margin', 0),
                        'margin_usage_percent': account_state['margin_usage_percent'],
                        'maintenance_margin': account_state.get('maintenance_margin', 0),
                        'positions': positions
                    }

                    logger.info(f"Preview: Using Hyperliquid {hyperliquid_environment} data for {account.name}")
                except Exception as hl_err:
                    # API error - use N/A values, but continue with prompt generation
                    logger.warning(f"Failed to get Hyperliquid data for {account.name}: {hl_err}, using N/A values")
                    portfolio = {
                        'cash': 'N/A',
                        'frozen_cash': 'N/A',
                        'positions': {},
                        'total_assets': 'N/A'
                    }
                    hyperliquid_state = None
        else:
            # Environment not recognized - use N/A
            portfolio = {
                'cash': 'N/A',
                'frozen_cash': 'N/A',
                'positions': {},
                'total_assets': 'N/A'
            }
            hyperliquid_state = None

    # Determine active symbols + metadata
    market_param = "binance" if exchange == "binance" else "CRYPTO"

    if exchange == "binance":
        from services.binance_symbol_service import get_selected_symbols as get_binance_selected_symbols
        binance_watchlist = get_binance_selected_symbols()
        active_symbols = requested_symbols or binance_watchlist or base_symbol_order
        symbol_metadata_map = {sym: SUPPORTED_SYMBOLS.get(sym, sym) for sym in active_symbols}
        logger.info(f"[Prompt Preview] Using Binance watchlist: {binance_watchlist}, active: {active_symbols}")
    elif environment in ["testnet", "mainnet"]:
        active_symbols = requested_symbols or hyper_watchlist or base_symbol_order
        symbol_metadata_map = {}
        for sym in active_symbols:
            entry = dict(hyper_symbol_map.get(sym, {}))
            entry.setdefault("name", sym)
            symbol_metadata_map[sym] = entry
    else:
        active_symbols = requested_symbols or base_symbol_order
        symbol_metadata_map = {sym: SUPPORTED_SYMBOLS.get(sym, sym) for sym in active_symbols}

    if not active_symbols:
        active_symbols = base_symbol_order

    prices: Dict[str, float] = {}
    for sym in active_symbols:
        try:
            prices[sym] = get_last_price(sym, market_param, environment=environment or "mainnet")
        except Exception as err:
            logger.warning(f"Failed to get price for {sym}: {err}")
            prices[sym] = 0.0

    # Get sampling interval
    sampling_interval = None
    try:
        from database.models import GlobalSamplingConfig
        config = db.query(GlobalSamplingConfig).first()
        if config:
            sampling_interval = config.sampling_interval
    except Exception:
        pass

    sampling_data = _build_multi_symbol_sampling_data(active_symbols, sampling_pool, sampling_interval)

    sample_trigger_context = {
        "trigger_type": "signal",
        "signal_pool_id": 1,
        "signal_pool_name": "OI Surge Monitor",
        "pool_logic": "OR",
        "triggered_signals": [
            {
                "signal_name": "OI Delta Alert",
                "description": "Open Interest increased significantly",
                "metric": "oi_delta",
                "operator": ">",
                "threshold": 2.0,
                "current_value": 2.5,
                "time_window": "15m",
            }
        ],
        "trigger_symbol": "BTC",
    }

    exchange_state = binance_state if exchange == "binance" else hyperliquid_state

    context = _build_prompt_context(
        account,
        portfolio,
        prices,
        news_section,
        None,
        None,
        exchange_state,
        db=db,
        symbol_metadata=symbol_metadata_map,
        symbol_order=active_symbols,
        sampling_interval=sampling_interval,
        environment=environment or "mainnet",
        template_text=template_text,
        trigger_context=sample_trigger_context,
        exchange=exchange,
    )
    context["sampling_data"] = sampling_data

    try:
        filled_prompt = template_text.format_map(SafeDict(context))
    except Exception as err:
        logger.error(f"Failed to fill prompt for {account.name}: {err}")
        filled_prompt = f"Error filling prompt: {err}"

    return {
        "accountId": account.id,
        "accountName": account.name,
        "exchange": exchange,
        "symbols": requested_symbols if requested_symbols else [],
        "filledPrompt": filled_prompt,
    }


# ============================================================================
# AI Prompt Generation Chat APIs
# ============================================================================

from pydantic import BaseModel, Field
from fastapi.responses import StreamingResponse
from services.ai_prompt_generation_service import (
    generate_prompt_with_ai,
    generate_prompt_with_ai_stream,
    get_conversation_history,
    get_conversation_messages
)
from database.models import User, UserSubscription


class AiChatRequest(BaseModel):
    """Request to send a message to AI prompt generation chat"""
    account_id: int = Field(..., alias="accountId")
    user_message: str = Field(..., alias="userMessage")
    conversation_id: Optional[int] = Field(None, alias="conversationId")
    prompt_id: Optional[int] = Field(None, alias="promptId")
    # SSE direct streaming is unstable (frontend disconnect = task abort). Do NOT set to False.
    use_background_task: bool = Field(True, alias="useBackgroundTask")

    class Config:
        populate_by_name = True


class AiChatResponse(BaseModel):
    """Response from AI prompt generation chat"""
    success: bool
    conversation_id: Optional[int] = Field(None, alias="conversationId")
    message_id: Optional[int] = Field(None, alias="messageId")
    content: Optional[str] = None
    prompt_result: Optional[str] = Field(None, alias="promptResult")
    error: Optional[str] = None

    class Config:
        populate_by_name = True


@router.post("/ai-chat", response_model=AiChatResponse)
def ai_chat(
    request: AiChatRequest,
    db: Session = Depends(get_db)
) -> AiChatResponse:
    """
    Send a message to AI prompt generation assistant

    Premium feature - requires active subscription
    """
    # Get user (default user for now)
    user = db.query(User).filter(User.username == "default").first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get AI Trader account
    account = db.query(Account).filter(Account.id == request.account_id, Account.is_deleted != True).first()
    if not account:
        raise HTTPException(status_code=404, detail="AI Trader not found")

    if account.account_type != "AI":
        raise HTTPException(status_code=400, detail="Selected account is not an AI Trader")

    # Generate response
    result = generate_prompt_with_ai(
        db=db,
        account=account,
        user_message=request.user_message,
        conversation_id=request.conversation_id,
        user_id=user.id,
        prompt_id=request.prompt_id
    )

    return AiChatResponse(
        success=result.get("success", False),
        conversation_id=result.get("conversation_id"),
        message_id=result.get("message_id"),
        content=result.get("content"),
        prompt_result=result.get("prompt_result"),
        error=result.get("error")
    )


@router.post("/ai-chat-stream")
def ai_chat_stream(
    request: AiChatRequest,
    db: Session = Depends(get_db)
):
    """
    Send a message to AI prompt generation assistant.

    Supports two modes:
    - SSE streaming (default): Returns Server-Sent Events directly
    - Background task (useBackgroundTask=true): Returns task_id for polling

    Premium feature - requires active subscription.
    """
    from services.ai_stream_service import get_buffer_manager, generate_task_id, run_ai_task_in_background
    from database.connection import SessionLocal

    # Get user (default user for now)
    user = db.query(User).filter(User.username == "default").first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Get AI Trader account
    account = db.query(Account).filter(Account.id == request.account_id, Account.is_deleted != True).first()
    if not account:
        raise HTTPException(status_code=404, detail="AI Trader not found")

    if account.account_type != "AI":
        raise HTTPException(status_code=400, detail="Selected account is not an AI Trader")

    # Background task mode
    if request.use_background_task:
        task_id = generate_task_id("prompt")
        manager = get_buffer_manager()

        # Check for existing running task on this conversation
        if request.conversation_id:
            existing = manager.get_pending_task_for_conversation(request.conversation_id)
            if existing:
                return {"task_id": existing.task_id, "status": "already_running"}

        # Create task
        manager.create_task(task_id, conversation_id=request.conversation_id)

        # Capture request data for background thread
        account_id = account.id
        user_message = request.user_message
        conversation_id = request.conversation_id
        user_id = user.id
        prompt_id = request.prompt_id

        def generator_func():
            # Create new db session for background thread
            bg_db = SessionLocal()
            try:
                bg_account = bg_db.query(Account).filter(Account.id == account_id, Account.is_deleted != True).first()
                yield from generate_prompt_with_ai_stream(
                    db=bg_db,
                    account=bg_account,
                    user_message=user_message,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    prompt_id=prompt_id
                )
            finally:
                bg_db.close()

        run_ai_task_in_background(task_id, generator_func)
        return {"task_id": task_id, "status": "started"}

    # SSE streaming mode (default, backward compatible)
    return StreamingResponse(
        generate_prompt_with_ai_stream(
            db=db,
            account=account,
            user_message=request.user_message,
            conversation_id=request.conversation_id,
            user_id=user.id,
            prompt_id=request.prompt_id
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.get("/ai-conversations")
def list_ai_conversations(
    limit: int = 20,
    db: Session = Depends(get_db)
) -> Dict:
    """
    Get list of AI prompt generation conversations

    Premium feature - requires active subscription
    """
    # Get user (default user for now)
    user = db.query(User).filter(User.username == "default").first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    conversations = get_conversation_history(
        db=db,
        user_id=user.id,
        limit=limit
    )

    return {"conversations": conversations}


@router.get("/ai-conversations/{conversation_id}/messages")
def get_conversation_messages_api(
    conversation_id: int,
    account_id: Optional[int] = Query(None),
    db: Session = Depends(get_db)
) -> Dict:
    """
    Get all messages in a specific conversation with token usage

    Premium feature - requires active subscription
    """
    # Get user (default user for now)
    user = db.query(User).filter(User.username == "default").first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    messages = get_conversation_messages(
        db=db,
        conversation_id=conversation_id,
        user_id=user.id
    )

    if messages is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Get compression points
    from database.models import AiPromptConversation, HyperAiProfile
    from services.ai_context_compression_service import calculate_token_usage, restore_tool_calls_to_messages
    import json as json_module

    conversation = db.query(AiPromptConversation).filter(
        AiPromptConversation.id == conversation_id,
        AiPromptConversation.user_id == user.id
    ).first()

    compression_points = []
    if conversation and conversation.compression_points:
        try:
            compression_points = json_module.loads(conversation.compression_points)
        except (json_module.JSONDecodeError, TypeError):
            compression_points = []

    # Determine model for token calculation: prefer account model, fallback to global
    token_model = None
    api_format = "openai"
    if account_id:
        acct = db.query(Account).filter(Account.id == account_id, Account.is_deleted != True).first()
        if acct and acct.model:
            token_model = acct.model
            from services.ai_decision_service import detect_api_format
            _, fmt = detect_api_format(acct.base_url or "")
            api_format = fmt or "openai"
    if not token_model:
        profile = db.query(HyperAiProfile).first()
        if profile and profile.llm_model:
            token_model = profile.llm_model
            from services.hyper_ai_service import get_llm_config
            llm_config = get_llm_config(db)
            api_format = llm_config.get("api_format", "openai")

    # Calculate token usage (only messages after compression point + summary)
    token_usage = None
    if token_model and messages:
        from services.ai_context_compression_service import get_last_compression_point
        from database.models import AiPromptMessage

        cp = get_last_compression_point(conversation) if conversation else None
        cp_msg_id = cp.get("message_id", 0) if cp else 0

        history_orm = db.query(AiPromptMessage).filter(
            AiPromptMessage.conversation_id == conversation_id,
            AiPromptMessage.id > cp_msg_id
        ).order_by(AiPromptMessage.created_at).all()

        msg_dicts = [
            {
                "role": m.role,
                "content": m.content,
                "tool_calls_log": m.tool_calls_log,
                "reasoning_snapshot": m.reasoning_snapshot,
            }
            for m in history_orm
        ]
        msg_list = restore_tool_calls_to_messages(msg_dicts, api_format, model=token_model or "")
        if cp and cp.get("summary"):
            msg_list.insert(0, {"role": "system", "content": cp["summary"]})
        token_usage = calculate_token_usage(msg_list, token_model)

    return {
        "messages": messages,
        "compression_points": compression_points,
        "token_usage": token_usage
    }


@router.get("/variables-reference")
def get_variables_reference(lang: str = "en") -> dict:
    """
    Get the prompt variables reference document (Markdown format).
    Used by frontend to display the strategy parameter guide.

    Args:
        lang: Language code ("en" for English, "zh" for Chinese)
    """
    import os

    # Select document based on language
    if lang == "zh":
        filename = "PROMPT_VARIABLES_REFERENCE_ZH.md"
    else:
        filename = "PROMPT_VARIABLES_REFERENCE.md"

    doc_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "config",
        filename
    )

    try:
        with open(doc_path, "r", encoding="utf-8") as f:
            content = f.read()
        return {"content": content}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Reference document not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read document: {str(e)}")
