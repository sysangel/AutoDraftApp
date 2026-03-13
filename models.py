"""
SQLAlchemy ORM models for the email draft app.
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Float,
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
    provider = Column(String, nullable=False, default="custom")
    source_folder = Column(String, nullable=False, default="INBOX")
    drafts_folder = Column(String, nullable=False, default="Drafts")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    messages = relationship("Message", back_populates="mailbox")
    conversations = relationship("Conversation", back_populates="mailbox")
    contact_insights = relationship("ContactInsight", back_populates="mailbox")
    domain_insights = relationship("DomainInsight", back_populates="mailbox")


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
    thread_key = Column(String, nullable=True, index=True)
    in_reply_to = Column(String, nullable=True)
    references_header = Column(Text, nullable=True)
    category = Column(String, nullable=True, default="general")
    category_confidence = Column(Float, nullable=True, default=0.5)
    needs_review = Column(Boolean, default=False)
    manual_category = Column(String, nullable=True)
    participant_domain = Column(String, nullable=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id"), nullable=True)
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
    conversation = relationship("Conversation", back_populates="messages")


class Settings(Base):
    """
    User-configurable prompt settings.
    One row per mailbox — controls how the AI drafts replies.
    """
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    mailbox_id = Column(Integer, ForeignKey("mailboxes.id"), nullable=False)
    # Signature appended to every draft
    signature = Column(Text, nullable=True)
    # Custom instructions injected into the AI prompt
    custom_instructions = Column(Text, nullable=True)
    # Tone: professional | friendly | formal | concise
    tone = Column(String, default="professional")
    # Optional footer link (e.g. calendar booking, website)
    footer_link = Column(String, nullable=True)
    footer_link_label = Column(String, nullable=True)
    # Your name / company name for the AI to reference
    sender_name = Column(String, nullable=True)
    company_name = Column(String, nullable=True)
    reply_length = Column(String, default="normal")
    business_context = Column(Text, nullable=True)
    hard_rules = Column(Text, nullable=True)
    escalation_guidance = Column(Text, nullable=True)
    example_phrasing = Column(Text, nullable=True)
    client_prompt = Column(Text, nullable=True)
    sales_prompt = Column(Text, nullable=True)
    support_prompt = Column(Text, nullable=True)
    scheduling_prompt = Column(Text, nullable=True)
    billing_prompt = Column(Text, nullable=True)
    general_prompt = Column(Text, nullable=True)
    lightweight_context_enabled = Column(Boolean, default=True)
    strict_privacy_mode = Column(Boolean, default=False)
    background_mode_enabled = Column(Boolean, default=False)
    signature_html = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    mailbox = relationship("Mailbox", backref="settings")


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


class Conversation(Base):
    """
    Lightweight local thread memory used to keep replies consistent without
    replaying an entire mailbox into the prompt.
    """
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    mailbox_id = Column(Integer, ForeignKey("mailboxes.id"), nullable=False)
    thread_key = Column(String, unique=True, nullable=False, index=True)
    participant_email = Column(String, nullable=True)
    participant_domain = Column(String, nullable=True)
    subject_root = Column(String, nullable=True)
    category = Column(String, nullable=False, default="general")
    summary = Column(Text, nullable=True)
    latest_inbound = Column(Text, nullable=True)
    last_draft_text = Column(Text, nullable=True)
    turn_count = Column(Integer, default=0)
    last_message_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    mailbox = relationship("Mailbox", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation")


class ContactInsight(Base):
    """
    Local-only lightweight profile for a single sender.
    """
    __tablename__ = "contact_insights"

    id = Column(Integer, primary_key=True, index=True)
    mailbox_id = Column(Integer, ForeignKey("mailboxes.id"), nullable=False)
    contact_key = Column(String, unique=True, nullable=False, index=True)
    contact_email = Column(String, nullable=False)
    contact_name = Column(String, nullable=True)
    domain = Column(String, nullable=True)
    category_hint = Column(String, nullable=False, default="general")
    client_preferences_json = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    common_topics = Column(Text, nullable=True)
    message_count = Column(Integer, default=0)
    last_seen_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    mailbox = relationship("Mailbox", back_populates="contact_insights")


class DraftFeedback(Base):
    """
    Explicit human feedback captured for a generated draft or categorization.
    """
    __tablename__ = "draft_feedback"

    id = Column(Integer, primary_key=True, index=True)
    mailbox_id = Column(Integer, ForeignKey("mailboxes.id"), nullable=False)
    message_id_fk = Column(Integer, ForeignKey("messages.id"), nullable=False)
    contact_insight_id = Column(Integer, ForeignKey("contact_insights.id"), nullable=True)
    feedback_type = Column(String, nullable=False, default="draft")
    signal = Column(String, nullable=False, default="up")
    reason = Column(Text, nullable=True)
    original_value = Column(Text, nullable=True)
    updated_value = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    mailbox = relationship("Mailbox", backref="draft_feedback")
    message = relationship("Message", backref="feedback_entries")
    contact = relationship("ContactInsight", backref="feedback_entries")


class DomainInsight(Base):
    """
    Local summary of what a sender domain typically emails about.
    """
    __tablename__ = "domain_insights"

    id = Column(Integer, primary_key=True, index=True)
    mailbox_id = Column(Integer, ForeignKey("mailboxes.id"), nullable=False)
    domain_key = Column(String, unique=True, nullable=False, index=True)
    domain = Column(String, nullable=False)
    summary = Column(Text, nullable=True)
    common_topics = Column(Text, nullable=True)
    top_category = Column(String, nullable=False, default="general")
    message_count = Column(Integer, default=0)
    last_seen_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    mailbox = relationship("Mailbox", back_populates="domain_insights")
