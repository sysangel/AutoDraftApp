"""
Database setup: SQLite engine, session factory, and table creation.
"""

import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./email_drafts.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}  # needed for SQLite + threading
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """Create all tables if they don't exist yet."""
    Base.metadata.create_all(bind=engine)
    _run_lightweight_migrations()


def _run_lightweight_migrations():
    """
    Keep SQLite schema forward-compatible without a full migration tool.
    """
    if not DATABASE_URL.startswith("sqlite:///"):
        return

    migration_map = {
        "mailboxes": {
            "provider": "ALTER TABLE mailboxes ADD COLUMN provider VARCHAR NOT NULL DEFAULT 'custom'",
            "source_folder": "ALTER TABLE mailboxes ADD COLUMN source_folder VARCHAR NOT NULL DEFAULT 'INBOX'",
        },
        "messages": {
            "thread_key": "ALTER TABLE messages ADD COLUMN thread_key VARCHAR",
            "in_reply_to": "ALTER TABLE messages ADD COLUMN in_reply_to VARCHAR",
            "references_header": "ALTER TABLE messages ADD COLUMN references_header TEXT",
            "category": "ALTER TABLE messages ADD COLUMN category VARCHAR DEFAULT 'general'",
            "category_confidence": "ALTER TABLE messages ADD COLUMN category_confidence FLOAT DEFAULT 0.5",
            "needs_review": "ALTER TABLE messages ADD COLUMN needs_review BOOLEAN DEFAULT 0",
            "manual_category": "ALTER TABLE messages ADD COLUMN manual_category VARCHAR",
            "participant_domain": "ALTER TABLE messages ADD COLUMN participant_domain VARCHAR",
            "conversation_id": "ALTER TABLE messages ADD COLUMN conversation_id INTEGER",
        },
        "settings": {
            "reply_length": "ALTER TABLE settings ADD COLUMN reply_length VARCHAR DEFAULT 'normal'",
            "business_context": "ALTER TABLE settings ADD COLUMN business_context TEXT",
            "hard_rules": "ALTER TABLE settings ADD COLUMN hard_rules TEXT",
            "escalation_guidance": "ALTER TABLE settings ADD COLUMN escalation_guidance TEXT",
            "example_phrasing": "ALTER TABLE settings ADD COLUMN example_phrasing TEXT",
            "client_prompt": "ALTER TABLE settings ADD COLUMN client_prompt TEXT",
            "sales_prompt": "ALTER TABLE settings ADD COLUMN sales_prompt TEXT",
            "support_prompt": "ALTER TABLE settings ADD COLUMN support_prompt TEXT",
            "scheduling_prompt": "ALTER TABLE settings ADD COLUMN scheduling_prompt TEXT",
            "billing_prompt": "ALTER TABLE settings ADD COLUMN billing_prompt TEXT",
            "general_prompt": "ALTER TABLE settings ADD COLUMN general_prompt TEXT",
            "lightweight_context_enabled": "ALTER TABLE settings ADD COLUMN lightweight_context_enabled BOOLEAN DEFAULT 1",
            "strict_privacy_mode": "ALTER TABLE settings ADD COLUMN strict_privacy_mode BOOLEAN DEFAULT 0",
            "background_mode_enabled": "ALTER TABLE settings ADD COLUMN background_mode_enabled BOOLEAN DEFAULT 0",
            "signature_html": "ALTER TABLE settings ADD COLUMN signature_html TEXT",
        },
    }
    migration_map["contact_insights"] = {
        "client_preferences_json": "ALTER TABLE contact_insights ADD COLUMN client_preferences_json TEXT",
    }

    with engine.begin() as conn:
        inspector = inspect(conn)
        for table_name, columns in migration_map.items():
            if not inspector.has_table(table_name):
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table_name)}
            for column_name, statement in columns.items():
                if column_name in existing_columns:
                    continue
                conn.execute(text(statement))

        # Add helpful indexes for high-volume local datasets.
        index_statements = [
            "CREATE INDEX IF NOT EXISTS ix_messages_mailbox_created_at ON messages (mailbox_id, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_messages_review_created_at ON messages (needs_review, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_messages_thread_key ON messages (thread_key)",
            "CREATE INDEX IF NOT EXISTS ix_conversations_mailbox_updated_at ON conversations (mailbox_id, updated_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_contact_insights_mailbox_count ON contact_insights (mailbox_id, message_count DESC)",
            "CREATE INDEX IF NOT EXISTS ix_domain_insights_mailbox_count ON domain_insights (mailbox_id, message_count DESC)",
            "CREATE INDEX IF NOT EXISTS ix_drafts_message_created_at ON drafts (message_id_fk, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS ix_feedback_message_created_at ON draft_feedback (message_id_fk, created_at DESC)",
        ]
        for statement in index_statements:
            conn.execute(text(statement))


def get_db():
    """
    FastAPI dependency that yields a DB session and closes it afterward.
    Usage: db: Session = Depends(get_db)
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
