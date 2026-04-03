"""
Add source_type and source_config to signal_pools.

Compatibility rules:
- Existing pools remain market_signals by default
- source_config is reserved for wallet_tracking pools only
- Existing market pool behavior must remain unchanged
"""

import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)


def upgrade():
    """Add source fields to signal_pools if they do not exist."""
    from database.connection import SessionLocal

    db = SessionLocal()
    try:
        source_type_exists = db.execute(text("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'signal_pools' AND column_name = 'source_type'
        """)).fetchone()
        if not source_type_exists:
            db.execute(text("""
                ALTER TABLE signal_pools
                ADD COLUMN source_type VARCHAR(30) NOT NULL DEFAULT 'market_signals'
            """))
            logger.info("Added source_type column to signal_pools")

        source_config_exists = db.execute(text("""
            SELECT 1 FROM information_schema.columns
            WHERE table_name = 'signal_pools' AND column_name = 'source_config'
        """)).fetchone()
        if not source_config_exists:
            db.execute(text("""
                ALTER TABLE signal_pools
                ADD COLUMN source_config TEXT NOT NULL DEFAULT '{}'
            """))
            logger.info("Added source_config column to signal_pools")

        db.commit()
        logger.info("Migration add_source_fields_to_signal_pools completed successfully")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
