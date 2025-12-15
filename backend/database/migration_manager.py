#!/usr/bin/env python3
"""
Database Migration Manager
Automatically runs pending migrations during application startup
"""
import os
import sys
import logging
from pathlib import Path

# Add backend to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from connection import SessionLocal

logger = logging.getLogger(__name__)

# List of migration scripts in execution order
MIGRATIONS = [
    "add_environment_to_crypto_klines.py",
    "add_prompt_template_fields.py",
    "add_ai_prompt_chat.py",
    "fix_timestamp_bigint.py",
    "create_signal_system_tables.py",
    "add_wallet_address_to_snapshot_tables.py",
    "add_wallet_address_to_hyperliquid_trades.py",
    "add_ai_signal_chat.py",
    "add_signal_pool_to_strategy.py",
    "add_logic_to_signal_pools.py",
]

def check_migration_table():
    """Create migrations tracking table if it doesn't exist"""
    db = SessionLocal()
    try:
        db.execute(text("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id SERIAL PRIMARY KEY,
                migration_name VARCHAR(255) UNIQUE NOT NULL,
                executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        db.commit()
        logger.info("Migration tracking table ready")
    except Exception as e:
        logger.error(f"Failed to create migration table: {e}")
        db.rollback()
        raise
    finally:
        db.close()

def is_migration_executed(migration_name: str) -> bool:
    """Check if a migration has already been executed"""
    db = SessionLocal()
    try:
        result = db.execute(text(
            "SELECT COUNT(*) FROM schema_migrations WHERE migration_name = :name"
        ), {"name": migration_name})
        count = result.scalar()
        return count > 0
    except Exception as e:
        logger.error(f"Failed to check migration status: {e}")
        return False
    finally:
        db.close()

def mark_migration_executed(migration_name: str):
    """Mark a migration as executed"""
    db = SessionLocal()
    try:
        db.execute(text(
            "INSERT INTO schema_migrations (migration_name) VALUES (:name)"
        ), {"name": migration_name})
        db.commit()
        logger.info(f"Marked migration {migration_name} as executed")
    except Exception as e:
        logger.error(f"Failed to mark migration as executed: {e}")
        db.rollback()
        raise
    finally:
        db.close()

def run_migration(migration_file: str):
    """Execute a single migration script"""
    migrations_dir = Path(__file__).parent / "migrations"
    migration_path = migrations_dir / migration_file

    if not migration_path.exists():
        logger.error(f"Migration file not found: {migration_path}")
        return False

    try:
        # Import and execute the migration
        spec = __import__(f"database.migrations.{migration_file[:-3]}", fromlist=["upgrade"])
        if hasattr(spec, 'upgrade'):
            logger.info(f"Executing migration: {migration_file}")
            spec.upgrade()
            mark_migration_executed(migration_file)
            return True
        else:
            logger.error(f"Migration {migration_file} missing upgrade function")
            return False
    except Exception as e:
        logger.error(f"Migration {migration_file} failed: {e}")
        logger.warning(f"Continuing with remaining migrations despite error in {migration_file}")
        # DO NOT mark failed migrations as executed - they need to be retried
        return False

def ensure_critical_columns():
    """
    Directly check and add missing critical columns.
    This runs before migration checks to fix any database schema issues.
    """
    db = SessionLocal()
    try:
        # Check if signal_pools table exists first
        result = db.execute(text("""
            SELECT table_name FROM information_schema.tables
            WHERE table_name = 'signal_pools'
        """))
        if not result.fetchone():
            return  # Table doesn't exist yet, migrations will create it

        # Check if logic column exists
        result = db.execute(text("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'signal_pools' AND column_name = 'logic'
        """))
        if not result.fetchone():
            # Directly add the missing column
            db.execute(text("""
                ALTER TABLE signal_pools
                ADD COLUMN logic VARCHAR(10) DEFAULT 'AND'
            """))
            db.commit()
            logger.info("Auto-fix: Added missing 'logic' column to signal_pools")
    except Exception as e:
        logger.debug(f"Schema check skipped: {e}")
        db.rollback()
    finally:
        db.close()


def run_pending_migrations():
    """Run all pending migrations"""
    logger.info("Checking for pending migrations...")

    try:
        check_migration_table()
        ensure_critical_columns()

        executed_count = 0
        failed_count = 0
        for migration in MIGRATIONS:
            if not is_migration_executed(migration):
                logger.info(f"Running pending migration: {migration}")
                if run_migration(migration):
                    executed_count += 1
                else:
                    failed_count += 1
                    logger.warning(f"Migration {migration} failed, but continuing with remaining migrations")
            else:
                logger.debug(f"Migration {migration} already executed, skipping")

        if executed_count > 0:
            logger.info(f"Successfully executed {executed_count} migrations")
        if failed_count > 0:
            logger.warning(f"{failed_count} migrations failed (may be already applied)")
        if executed_count == 0 and failed_count == 0:
            logger.info("No pending migrations")

        # Return True to allow application to start even if some migrations failed
        # (they may have failed because they were already applied)
        return True

    except Exception as e:
        logger.error(f"Migration process failed: {e}")
        return False

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    success = run_pending_migrations()
    sys.exit(0 if success else 1)