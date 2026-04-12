"""
News AI Classification Service (Phase 1D)

Batch job that classifies unclassified news articles using the user's
configured LLM. Determines sentiment (bullish/bearish/neutral), refines
symbol tags, and generates one-line summaries.

Runs every 30 minutes via TaskScheduler. Uses the same LLM provider,
model, and API key as AI Trader (from HyperAIProfile).
If LLM is not configured, this service is a no-op.
"""
import json
import logging
import time
from typing import Dict, List, Optional

import requests as http_requests

from database.connection import SessionLocal
from database.models import NewsArticle

logger = logging.getLogger(__name__)

# Max articles per batch to avoid token limits
BATCH_SIZE = 30
# Request timeout for LLM API
LLM_TIMEOUT = 120

CLASSIFICATION_PROMPT = """You are a financial news analyst. Classify each article below.

For each article (numbered), return a JSON array with one object per article:
{{
  "id": <article number>,
  "symbols": ["BTC", "ETH", ...],
  "sentiment": "bullish" | "bearish" | "neutral",
  "summary": "<factual summary, max 400 chars>"
}}

Symbol rules:
- Use BASE TICKER only: "BTC" not "BITCOIN", "BTC-PERP", "BTC/USDT" or "BTC:USDC"
- An article can have MULTIPLE symbols: ["BTC", "ETH", "SOL"] if all are affected
- For macro/economic news (Fed, CPI, GDP, tariffs, geopolitics), use ["_MACRO"]
- If an article is general crypto industry news not tied to a specific coin, use [] (empty array)
- User's watchlist (prioritize matching): {watchlist}
- Known exchange tickers (use these exact names when matching): {exchange_symbols}
- The ticker list is NOT exhaustive. New coins appear regularly. Use the correct ticker for any coin mentioned.

Sentiment rules:
- "bullish": positive short-term price catalyst (ETF approval, adoption, strong earnings)
- "bearish": negative short-term price catalyst (hack, regulation crackdown, sell-off)
- "neutral": informational, no clear directional impact

Summary rules:
- For articles marked [NEEDS_SUMMARY], write a factual summary (max 400 chars):
  - MUST preserve all dollar amounts, percentages, prices, and key metrics from the original
  - MUST keep WHO (companies, people, institutions) and WHAT (actions, events)
  - Example: "BTC +2% at $96,750; ETH +2% at $3,360; BTC ETFs: +$754m inflow. Crypto rallies on largest ETF inflow in 3 months." NOT just "Crypto rallies on ETF news."
  - Use the full 400 chars budget — longer is better than losing data
- For articles marked [HAS_SUMMARY], the summary field is not used — return an empty string ""
- Never add hype or speculation

Articles:
{articles}

Return ONLY a valid JSON array, no other text."""

# Max chars for original summary to be kept as-is
SUMMARY_KEEP_THRESHOLD = 400


def _get_llm_config(db) -> Optional[Dict]:
    """Get user's LLM config. Returns None if not configured."""
    from services.hyper_ai_service import get_llm_config
    config = get_llm_config(db)
    if not config.get("configured"):
        return None
    if not config.get("api_key") or not config.get("base_url"):
        return None
    return config


def _get_symbols_from_config(db, keys: List[str]) -> List[str]:
    """Extract symbol names from SystemConfig JSON arrays."""
    from database.models import SystemConfig
    symbols = set()
    for key in keys:
        config = db.query(SystemConfig).filter(
            SystemConfig.key == key
        ).first()
        if config and config.value:
            try:
                data = json.loads(config.value)
                for item in data:
                    if isinstance(item, dict):
                        symbols.add(item.get("symbol", "").upper())
                    elif isinstance(item, str):
                        symbols.add(item.upper())
            except (json.JSONDecodeError, TypeError):
                pass
    symbols.discard("")
    return sorted(symbols)


def _get_watchlist(db) -> List[str]:
    """Get user's selected watchlist from both exchanges."""
    result = _get_symbols_from_config(db, [
        "hyperliquid_selected_symbols",
        "binance_selected_symbols",
    ])
    return result if result else ["BTC", "ETH"]


def _get_exchange_symbols(db) -> List[str]:
    """Get all tradeable symbols from both exchanges (for AI reference)."""
    result = _get_symbols_from_config(db, [
        "hyperliquid_available_symbols",
        "binance_available_symbols",
    ])
    return result if result else []


def _call_llm(config: Dict, prompt: str) -> Optional[str]:
    """Call user's LLM and return raw response text."""
    from services.ai_decision_service import (
        build_llm_headers, build_llm_payload,
        build_chat_completion_endpoints,
    )

    headers = build_llm_headers(config["api_format"], config["api_key"], config["base_url"])
    payload = build_llm_payload(
        model=config["model"],
        messages=[{"role": "user", "content": prompt}],
        api_format=config["api_format"],
        max_tokens=4096,
        temperature=0.3,
    )
    endpoints = build_chat_completion_endpoints(
        config["base_url"], config["model"]
    )
    if not endpoints:
        logger.error("[NewsAI] No valid endpoint for LLM")
        return None

    for endpoint in endpoints:
        try:
            resp = http_requests.post(
                endpoint, headers=headers, json=payload,
                timeout=LLM_TIMEOUT, verify=False,
            )
            if resp.status_code == 200:
                data = resp.json()
                # OpenAI format
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
                # Anthropic format
                content = data.get("content", [])
                if content:
                    return content[0].get("text", "")
                return None
            elif resp.status_code == 429:
                logger.warning("[NewsAI] Rate limited, skipping batch")
                return None
            else:
                logger.warning(
                    "[NewsAI] LLM HTTP %s from %s",
                    resp.status_code, endpoint
                )
        except Exception as e:
            logger.error("[NewsAI] LLM call error: %s", e)

    return None


def _parse_classification(raw: str) -> List[Dict]:
    """Parse LLM response into classification list."""
    if not raw:
        return []
    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        # Try to find JSON array in response
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
    logger.warning("[NewsAI] Failed to parse LLM response")
    return []


def classify_pending_articles():
    """
    Main entry point called by TaskScheduler every 30 minutes.
    Fetches unclassified articles, sends to LLM in batches, updates DB.
    """
    db = SessionLocal()
    try:
        # Check LLM config first
        config = _get_llm_config(db)
        if not config:
            logger.debug("[NewsAI] LLM not configured, skipping")
            return

        # Get unclassified articles
        pending = db.query(NewsArticle).filter(
            NewsArticle.classified == False
        ).order_by(NewsArticle.id.asc()).limit(BATCH_SIZE).all()

        if not pending:
            return

        watchlist = _get_watchlist(db)
        exchange_symbols = _get_exchange_symbols(db)
        logger.info(
            "[NewsAI] Classifying %d articles (watchlist: %s)",
            len(pending), watchlist
        )

        # Build article text for prompt
        # Mark articles that need AI summary vs those with short-enough originals
        article_lines = []
        id_map = {}  # prompt index -> article DB id
        short_summary_map = {}  # article DB id -> original summary (kept as-is)
        for idx, article in enumerate(pending, 1):
            title = article.title or ""
            orig_summary = (article.summary or "").strip()
            needs_summary = len(orig_summary) > SUMMARY_KEEP_THRESHOLD or not orig_summary

            if not needs_summary:
                # Original is short enough, keep it — tell AI to skip summary
                short_summary_map[article.id] = orig_summary
                tag = "[HAS_SUMMARY]"
                line = f"{idx}. {tag} [{article.source_domain}] {title}"
            else:
                # Need AI to summarize — send original for context
                tag = "[NEEDS_SUMMARY]"
                line = f"{idx}. {tag} [{article.source_domain}] {title}"
                if orig_summary:
                    line += f" - {orig_summary[:600]}"

            article_lines.append(line)
            id_map[idx] = article.id

        prompt = CLASSIFICATION_PROMPT.format(
            watchlist=", ".join(watchlist),
            exchange_symbols=", ".join(exchange_symbols),
            articles="\n".join(article_lines),
        )

        # Call LLM
        raw_response = _call_llm(config, prompt)
        classifications = _parse_classification(raw_response)

        if not classifications:
            logger.warning(
                "[NewsAI] No valid classifications returned, "
                "marking batch as classified anyway"
            )
            # Mark as classified to avoid infinite retry
            for article in pending:
                article.classified = True
            db.commit()
            return

        # Apply classifications
        updated = 0
        for cls in classifications:
            idx = cls.get("id")
            article_id = id_map.get(idx)
            if not article_id:
                continue

            article = db.query(NewsArticle).filter(
                NewsArticle.id == article_id
            ).first()
            if not article:
                continue

            # Update symbols (merge with existing keyword-matched ones)
            new_symbols = cls.get("symbols", [])
            if new_symbols:
                # Normalize: uppercase, strip whitespace, reject bad formats
                cleaned = []
                for s in new_symbols:
                    if not isinstance(s, str):
                        continue
                    s = s.strip().upper()
                    # Reject pair formats like BTC/USDT, BTC:USDC, BTC-PERP
                    if not s or "/" in s or ":" in s or "-" in s:
                        continue
                    # Reject unreasonably long tickers
                    if len(s) > 10:
                        continue
                    cleaned.append(s)

                if cleaned:
                    existing = []
                    if article.symbols:
                        try:
                            existing = json.loads(article.symbols)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    merged = list(set(existing + cleaned))
                    article.symbols = json.dumps(merged)

            # Update sentiment
            sentiment = cls.get("sentiment")
            if sentiment in ("bullish", "bearish", "neutral"):
                article.sentiment = sentiment
                article.sentiment_source = "ai"

            # Update summary: use original if short enough, else AI summary
            if article.id in short_summary_map:
                article.ai_summary = short_summary_map[article.id][:400]
            else:
                ai_summary = cls.get("summary", "")
                if ai_summary:
                    article.ai_summary = ai_summary[:400]

            article.classified = True
            updated += 1

        # Mark any articles not in response as classified too
        for article in pending:
            if not article.classified:
                article.classified = True

        db.commit()
        logger.info(
            "[NewsAI] Classified %d/%d articles", updated, len(pending)
        )

    except Exception as e:
        db.rollback()
        logger.error("[NewsAI] Classification error: %s", e, exc_info=True)
    finally:
        db.close()
