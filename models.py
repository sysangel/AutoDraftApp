"""
SQLAlchemy ORM models for the email draft app.
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Boolean,
    DateTime, ForeignKey
)
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


class Mailbox(Base):
    """
    Stores IMAP mailbox configuration.
    Passwords are NOT stored here — only a key name pointing to an env variable.
    In production, replace with a secrets manager (e.g. AWS Secrets Manager, Vault).
    """
    __tablename__ = "mailboxes"

    id = Column(Integer, primary_key=True, index=True)
    email_address = Column(String, unique=True, nullable=False)
    imap_host = Column(String, nullable=False)
    imap_port = Column(Integer, default=993)
    username = Column(String, nullable=False)
    # Store the env variable KEY name, not the actual password.
    # e.g. "MAILBOX_PASSWORD" — app will call os.getenv("MAILBOX_PASSWORD")
    password_env_key = Column(String, nullable=False, default="MAILBOX_PASSWORD")
    drafts_folder = Column(String, nullable=False, default="Drafts")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    messages = relationship("Message", back_populates="mailbox")


class Message(Base):
    """
    Tracks every inbound email we've encountered.
    message_id is the RFC 2822 Message-ID header and serves as the dedupe key.
    """
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    mailbox_id = Column(Integer, ForeignKey("mailboxes.id"), nullable=False)
    # RFC 2822 Message-ID — our primary dedupe key. Never process twice.
    message_id = Column(String, unique=True, nullable=False, index=True)
    # IMAP UID — useful for flagging/marking read on the server
    uid = Column(String, nullable=True)
    from_email = Column(String, nullable=True)
    from_name = Column(String, nullable=True)
    subject = Column(String, nullable=True)
    received_at = Column(DateTime, nullable=True)
    body_text = Column(Text, nullable=True)
    cleaned_text = Column(Text, nullable=True)
    is_read_marked = Column(Boolean, default=False)
    draft_appended = Column(Boolean, default=False)
    # Statuses: new | drafted | appended_to_drafts | marked_read | complete | error
    status = Column(String, default="new")
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    mailbox = relationship("Mailbox", back_populates="messages")
    drafts = relationship("Draft", back_populates="message")


class Draft(Base):
    """
    Stores the AI-generated draft text for a given message.
    """
    __tablename__ = "drafts"

    id = Column(Integer, primary_key=True, index=True)
    message_id_fk = Column(Integer, ForeignKey("messages.id"), nullable=False)
    draft_text = Column(Text, nullable=False)
    model_name = Column(String, nullable=True)
    prompt_version = Column(String, default="v1")
    created_at = Column(DateTime, default=datetime.utcnow)

    message = relationship("Message", back_populates="drafts")
