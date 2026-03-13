"""
Email Draft App — Main FastAPI application.

What this app does:
  - Reads incoming emails via IMAP
  - Generates reply drafts with OpenAI
  - Saves drafts into the mailbox Drafts folder via IMAP
  - Marks original emails as read
  - Shows a simple admin UI

What this app does NOT do:
  - Send email (no SMTP, ever)
  - Auto-send anything
"""

from dotenv import dotenv_values, load_dotenv
load_dotenv()
import json
import logging
import os
import re
import threading
import traceback
from html import unescape
from html.parser import HTMLParser
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

import db as database
from db import get_db, init_db
from imap_service import (
    append_draft_to_folder,
    clean_body,
    close_connection,
    get_imap_connection,
    list_mailboxes,
    mark_as_read,
    poll_mailbox,
)
from ai_service import generate_draft_reply, PROMPT_VERSION
from models import Conversation, ContactInsight, DomainInsight, Draft, DraftFeedback, Mailbox, Message, Settings
from provider_presets import CATEGORY_OPTIONS, PROVIDER_PRESETS

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _append_startup_trace(message: str):
    trace_path = os.getenv("DRAFT_AI_STARTUP_LOG")
    if not trace_path:
        return
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with Path(trace_path).open("a", encoding="utf-8") as fh:
        fh.write(f"[{timestamp}] [app] {message}\n")


def _configure_logging():
    handlers = [logging.StreamHandler()]
    app_log_path = os.getenv("DRAFT_AI_APP_LOG")
    if app_log_path:
        Path(app_log_path).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(app_log_path, encoding="utf-8"))

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


_configure_logging()
logger = logging.getLogger(__name__)
_append_startup_trace("app.py imported")

MAX_UNREAD_FETCH = 75
MAX_STORED_BODY_CHARS = 20000
MAX_STORED_CLEANED_CHARS = 12000
MESSAGE_PAGE_SIZE = 40
REVIEW_PAGE_SIZE = 30
MAX_IMPORTED_SIGNATURE_HTML_CHARS = 50000
MAX_IMPORTED_SIGNATURE_TEXT_CHARS = 8000

# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
scheduler = BackgroundScheduler()
poll_state = {
    "running": False,
    "last_started_at": None,
    "last_finished_at": None,
    "last_result": None,
    "last_error": None,
    "mailboxes_processed": 0,
    "messages_processed": 0,
    "backlog_detected": False,
    "backlog_mailboxes": [],
    "last_run_summary": "",
}
poll_state_lock = threading.Lock()

# ---------------------------------------------------------------------------
# App lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB and start background scheduler on startup."""
    try:
        _append_startup_trace("lifespan startup entered")
        logger.info("App startup beginning.")
        init_db()
        _append_startup_trace("database initialization complete")
        logger.info("Database initialized.")

        _append_startup_trace("starting mailbox seed from environment")
        _seed_mailbox_from_env()
        _append_startup_trace("mailbox seed complete")

        _append_startup_trace("registering scheduler job")
        scheduler.add_job(
            run_poll_job,
            "interval",
            minutes=2,
            id="email_poll",
            replace_existing=True,
        )
        scheduler.start()
        _append_startup_trace("scheduler started")
        logger.info("Background scheduler started (polling every 2 minutes).")

        yield  # App is running
    except Exception:
        _append_startup_trace(f"lifespan startup crashed:\n{traceback.format_exc()}")
        logger.exception("Application startup failed.")
        raise
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
            logger.info("Scheduler shut down.")
            _append_startup_trace("scheduler shut down")


app = FastAPI(
    title="Draft",
    description="Reads emails, drafts replies, never sends.",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
_STOPWORDS = {
    "the", "and", "for", "that", "with", "this", "from", "have", "your", "about",
    "would", "could", "there", "their", "please", "thanks", "regards", "hello",
    "just", "need", "into", "what", "when", "where", "which", "re", "fwd", "fw",
}
_SUBJECT_PREFIX_RE = re.compile(r"^\s*((re|fw|fwd)\s*:\s*)+", re.IGNORECASE)


def _domain_from_email(email_address: str | None) -> str:
    if not email_address or "@" not in email_address:
        return ""
    return email_address.rsplit("@", 1)[1].lower().strip()


def _normalize_subject(subject: str | None) -> str:
    normalized = _SUBJECT_PREFIX_RE.sub("", (subject or "")).strip()
    return normalized or "(no subject)"


def _thread_key(mailbox_id: int, parsed: dict) -> str:
    if parsed.get("in_reply_to"):
        return f"{mailbox_id}|reply|{parsed['in_reply_to'].strip()}"
    refs = (parsed.get("references_header") or "").split()
    if refs:
        return f"{mailbox_id}|refs|{refs[-1].strip()}"
    domain = _domain_from_email(parsed.get("from_email"))
    return f"{mailbox_id}|subj|{domain}|{_normalize_subject(parsed.get('subject')).lower()}"


def _find_conversation(db: Session, mailbox: Mailbox, parsed: dict) -> Conversation | None:
    candidate_ids = []
    if parsed.get("in_reply_to"):
        candidate_ids.append(parsed["in_reply_to"].strip())
    candidate_ids.extend((parsed.get("references_header") or "").split())

    for candidate in candidate_ids:
        prior = db.query(Message).filter(Message.message_id == candidate).first()
        if prior and prior.conversation_id:
            return db.query(Conversation).filter(Conversation.id == prior.conversation_id).first()

    domain = _domain_from_email(parsed.get("from_email"))
    subject_root = _normalize_subject(parsed.get("subject"))
    return (
        db.query(Conversation)
        .filter(
            Conversation.mailbox_id == mailbox.id,
            Conversation.participant_domain == domain,
            Conversation.subject_root == subject_root,
        )
        .order_by(Conversation.updated_at.desc())
        .first()
    )


def _extract_topics(*chunks: str, max_items: int = 4) -> list[str]:
    counts: dict[str, int] = {}
    for chunk in chunks:
        for token in re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}", chunk or ""):
            word = token.lower()
            if word in _STOPWORDS:
                continue
            counts[word] = counts.get(word, 0) + 1
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [word for word, _ in ordered[:max_items]]


def _classify_message(subject: str, cleaned_text: str, prior_category: str | None = None) -> str:
    haystack = f"{subject}\n{cleaned_text}".lower()
    keyword_map = {
        "billing": ["invoice", "payment", "billing", "quote", "refund", "charge"],
        "scheduling": ["schedule", "meeting", "calendar", "availability", "reschedule", "time"],
        "support": ["issue", "error", "bug", "problem", "support", "help"],
        "sales": ["proposal", "pricing", "demo", "purchase", "buy", "plan"],
        "client": ["project", "deliverable", "update", "revision", "client"],
    }
    for category, words in keyword_map.items():
        if any(word in haystack for word in words):
            return category
    return prior_category or "general"


def _category_confidence(subject: str, cleaned_text: str, category: str) -> float:
    haystack = f"{subject}\n{cleaned_text}".lower()
    keyword_map = {
        "billing": ["invoice", "payment", "billing", "quote", "refund", "charge"],
        "scheduling": ["schedule", "meeting", "calendar", "availability", "reschedule", "time"],
        "support": ["issue", "error", "bug", "problem", "support", "help"],
        "sales": ["proposal", "pricing", "demo", "purchase", "buy", "plan"],
        "client": ["project", "deliverable", "update", "revision", "client"],
    }
    if category not in keyword_map:
        return 0.55
    hits = sum(1 for word in keyword_map[category] if word in haystack)
    if hits >= 2:
        return 0.9
    if hits == 1:
        return 0.78
    return 0.58


def _load_client_preferences(contact: ContactInsight | None) -> dict:
    if not contact or not contact.client_preferences_json:
        return {}
    try:
        value = json.loads(contact.client_preferences_json)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _save_client_preferences(contact: ContactInsight, preferences: dict):
    contact.client_preferences_json = json.dumps(preferences, ensure_ascii=True)


def _style_preferences_text(preferences: dict) -> str:
    parts = []
    if preferences.get("tone"):
        parts.append(f"preferred tone: {preferences['tone']}")
    if preferences.get("closing_style"):
        parts.append(f"preferred closing: {preferences['closing_style']}")
    if preferences.get("meeting_link"):
        parts.append(f"include meeting link preference: {preferences['meeting_link']}")
    notes = preferences.get("refinement_notes") or []
    if notes:
        parts.append("recent refinement notes: " + "; ".join(notes[-3:]))
    if preferences.get("category_examples"):
        examples = ", ".join(
            f"{key} -> {value}" for key, value in list(preferences["category_examples"].items())[:3]
        )
        if examples:
            parts.append("confirmed category examples: " + examples)
    return " | ".join(parts)


def _apply_feedback_to_preferences(contact: ContactInsight | None, signal: str, reason: str | None):
    if not contact:
        return
    preferences = _load_client_preferences(contact)
    notes = preferences.get("refinement_notes") or []
    if reason:
        notes.append(reason.strip())
    preferences["refinement_notes"] = notes[-8:]
    if signal == "down" and reason:
        lowered = reason.lower()
        if "formal" in lowered:
            preferences["tone"] = "less formal"
        if "concise" in lowered or "short" in lowered:
            preferences["tone"] = "more concise"
        if "meeting link" in lowered or "calendar" in lowered:
            preferences["meeting_link"] = "include when relevant"
        if "deadline" in lowered or "timeline" in lowered:
            preferences["closing_style"] = "mention timing expectations clearly"
    _save_client_preferences(contact, preferences)


def _compact_text(value: str | None, max_chars: int = 260) -> str:
    text = " ".join((value or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def _truncate_for_storage(value: str | None, max_chars: int) -> str | None:
    if value is None:
        return None
    if len(value) <= max_chars:
        return value
    return value[:max_chars]


class _SignatureHTMLToTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        if tag in {"br", "hr"}:
            self.parts.append("\n")
        elif tag in {"p", "div", "section", "tr"}:
            if self.parts and not self.parts[-1].endswith("\n"):
                self.parts.append("\n")
        elif tag == "li":
            if self.parts and not self.parts[-1].endswith("\n"):
                self.parts.append("\n")
            self.parts.append("- ")

    def handle_endtag(self, tag: str):
        if tag in {"p", "div", "section", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str):
        if data:
            self.parts.append(data)


def _html_signature_to_text(html_value: str) -> str:
    parser = _SignatureHTMLToTextParser()
    parser.feed(html_value)
    raw_text = unescape("".join(parser.parts))
    raw_text = raw_text.replace("\xa0", " ")
    raw_text = re.sub(r"\n{3,}", "\n\n", raw_text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw_text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _import_signature_payload(form) -> tuple[str | None, str | None]:
    html_from_textarea = (form.get("signature_html_import") or "").strip()
    upload = form.get("signature_html_file")
    html_from_file = ""
    if upload and getattr(upload, "filename", ""):
        file_bytes = upload.file.read()
        html_from_file = file_bytes.decode("utf-8", errors="ignore").strip()

    html_payload = html_from_textarea or html_from_file
    if not html_payload:
        return None, None

    html_payload = _truncate_for_storage(html_payload, MAX_IMPORTED_SIGNATURE_HTML_CHARS) or ""
    text_payload = _html_signature_to_text(html_payload)
    text_payload = _truncate_for_storage(text_payload, MAX_IMPORTED_SIGNATURE_TEXT_CHARS) or ""
    return html_payload or None, text_payload or None


def _conversation_summary(previous_summary: str | None, cleaned: str, category: str, topics: list[str]) -> str:
    bits = []
    if previous_summary:
        bits.append(previous_summary)
    if cleaned:
        bits.append(f"Latest inbound: {_compact_text(cleaned, 180)}")
    if topics:
        bits.append("Topics: " + ", ".join(topics))
    bits.append(f"Category: {category}")
    return _compact_text(" | ".join(bits), 500)


def _recent_history(db: Session, conversation_id: int | None) -> str:
    if not conversation_id:
        return ""
    recent_messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc())
        .limit(3)
        .all()
    )
    recent_messages.reverse()
    lines = []
    for msg in recent_messages:
        lines.append(f"Inbound from {msg.from_email or 'unknown'}: {_compact_text(msg.cleaned_text or msg.body_text, 180)}")
        draft = (
            db.query(Draft)
            .filter(Draft.message_id_fk == msg.id)
            .order_by(Draft.created_at.desc())
            .first()
        )
        if draft:
            lines.append(f"Prior drafted reply: {_compact_text(draft.draft_text, 180)}")
    return "\n".join(lines[:6])


def _update_contact_and_domain_insights(
    db: Session,
    mailbox: Mailbox,
    message: Message,
    cleaned_text: str,
    category: str,
):
    email_address = message.from_email or ""
    domain = _domain_from_email(email_address)
    topics = _extract_topics(message.subject or "", cleaned_text)

    if email_address:
        contact_key = f"{mailbox.id}:{email_address.lower()}"
        contact = db.query(ContactInsight).filter(ContactInsight.contact_key == contact_key).first()
        if not contact:
            contact = ContactInsight(
                mailbox_id=mailbox.id,
                contact_key=contact_key,
                contact_email=email_address.lower(),
            )
            db.add(contact)
        contact.contact_name = message.from_name or contact.contact_name
        contact.domain = domain
        contact.category_hint = category
        contact.message_count = (contact.message_count or 0) + 1
        contact.last_seen_at = message.received_at or datetime.utcnow()
        contact.summary = _compact_text(
            f"{message.from_name or email_address} usually writes about {', '.join(topics) or category}. "
            f"Most recent category: {category}.",
            300,
        )
        contact.common_topics = ", ".join(topics)

    if domain:
        domain_key = f"{mailbox.id}:{domain}"
        domain_insight = db.query(DomainInsight).filter(DomainInsight.domain_key == domain_key).first()
        if not domain_insight:
            domain_insight = DomainInsight(
                mailbox_id=mailbox.id,
                domain_key=domain_key,
                domain=domain,
            )
            db.add(domain_insight)
        domain_insight.top_category = category
        domain_insight.message_count = (domain_insight.message_count or 0) + 1
        domain_insight.last_seen_at = message.received_at or datetime.utcnow()
        domain_insight.summary = _compact_text(
            f"Messages from {domain} typically involve {', '.join(topics) or category}.",
            300,
        )
        domain_insight.common_topics = ", ".join(topics)

    db.commit()


def _build_generation_context(db: Session, conversation: Conversation | None, message: Message, settings: Settings | None) -> dict:
    if settings and settings.strict_privacy_mode:
        return {"category": message.category or "general"}

    contact_summary = ""
    domain_summary = ""
    client_preferences = ""
    if message.from_email:
        contact_key = f"{message.mailbox_id}:{message.from_email.lower()}"
        contact = db.query(ContactInsight).filter(ContactInsight.contact_key == contact_key).first()
        if contact:
            contact_summary = contact.summary or ""
            client_preferences = _style_preferences_text(_load_client_preferences(contact))
    if message.participant_domain:
        domain_key = f"{message.mailbox_id}:{message.participant_domain}"
        domain = db.query(DomainInsight).filter(DomainInsight.domain_key == domain_key).first()
        if domain:
            domain_summary = domain.summary or ""

    if settings and settings.lightweight_context_enabled is False:
        return {"category": message.category or "general"}

    return {
        "category": message.category or "general",
        "conversation_summary": conversation.summary if conversation else "",
        "contact_summary": contact_summary,
        "domain_summary": domain_summary,
        "client_preferences": client_preferences,
        "recent_history": _recent_history(db, conversation.id if conversation else None),
    }


def _message_context_bundle(db: Session, msg: Message) -> dict:
    return _message_context_bundles(db, [msg])[0]


def _message_context_bundles(db: Session, messages: list[Message]) -> list[dict]:
    if not messages:
        return []

    message_ids = [msg.id for msg in messages]
    draft_rows = (
        db.query(Draft)
        .filter(Draft.message_id_fk.in_(message_ids))
        .order_by(Draft.message_id_fk.asc(), Draft.created_at.desc())
        .all()
    )
    drafts_by_message: dict[int, Draft] = {}
    for draft in draft_rows:
        drafts_by_message.setdefault(draft.message_id_fk, draft)

    contact_keys = {
        f"{msg.mailbox_id}:{msg.from_email.lower()}"
        for msg in messages
        if msg.from_email
    }
    domain_keys = {
        f"{msg.mailbox_id}:{msg.participant_domain}"
        for msg in messages
        if msg.participant_domain
    }
    conversation_ids = {msg.conversation_id for msg in messages if msg.conversation_id}

    contacts_by_key = {
        row.contact_key: row
        for row in db.query(ContactInsight).filter(ContactInsight.contact_key.in_(contact_keys)).all()
    } if contact_keys else {}
    domains_by_key = {
        row.domain_key: row
        for row in db.query(DomainInsight).filter(DomainInsight.domain_key.in_(domain_keys)).all()
    } if domain_keys else {}
    conversations_by_id = {
        row.id: row
        for row in db.query(Conversation).filter(Conversation.id.in_(conversation_ids)).all()
    } if conversation_ids else {}

    bundles = []
    for msg in messages:
        contact = contacts_by_key.get(f"{msg.mailbox_id}:{msg.from_email.lower()}") if msg.from_email else None
        domain = domains_by_key.get(f"{msg.mailbox_id}:{msg.participant_domain}") if msg.participant_domain else None
        conversation = conversations_by_id.get(msg.conversation_id) if msg.conversation_id else None
        bundles.append(
            {
                "message": msg,
                "draft": drafts_by_message.get(msg.id),
                "contact": contact,
                "domain": domain,
                "conversation": conversation,
                "client_preferences": _load_client_preferences(contact),
            }
        )
    return bundles


def _recent_activity_summary(enriched: list[dict]) -> dict:
    categories: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for item in enriched:
        message = item["message"]
        category = message.category or "general"
        status = message.status or "new"
        categories[category] = categories.get(category, 0) + 1
        statuses[status] = statuses.get(status, 0) + 1

    return {
        "message_count": len(enriched),
        "top_category": max(categories.items(), key=lambda entry: entry[1])[0] if categories else "general",
        "top_status": max(statuses.items(), key=lambda entry: entry[1])[0] if statuses else "new",
        "categories": sorted(categories.items(), key=lambda entry: entry[1], reverse=True)[:4],
        "statuses": sorted(statuses.items(), key=lambda entry: entry[1], reverse=True)[:4],
    }

def _runtime_env_path() -> Path:
    """
    Return the .env file used by this runtime.
    - Installed desktop app: %APPDATA%\\draft.ai\\.env (via DRAFT_AI_DATA_DIR)
    - Local/dev runs: project/.env
    """
    data_dir = os.getenv("DRAFT_AI_DATA_DIR")
    if data_dir:
        return Path(data_dir) / ".env"
    return Path(".env")


def _read_runtime_config() -> dict:
    """
    Read config from process env first, then fallback to runtime .env file.
    """
    env_file = dotenv_values(_runtime_env_path())

    def _get(name: str, default: str = "") -> str:
        return os.getenv(name) or (env_file.get(name) or default)

    return {
        "provider": _get("MAIL_PROVIDER", "custom"),
        "email": _get("MAILBOX_EMAIL"),
        "imap_host": _get("IMAP_HOST"),
        "imap_port": _get("IMAP_PORT", "993"),
        "imap_username": _get("IMAP_USERNAME"),
        "mailbox_password": _get("MAILBOX_PASSWORD"),
        "source_folder": _get("SOURCE_FOLDER", "INBOX"),
        "drafts_folder": _get("DRAFTS_FOLDER", "Drafts"),
        "polling_enabled": _get("POLLING_ENABLED", "1"),
        "run_in_background": _get("RUN_IN_BACKGROUND", "0"),
        "openai_api_key": _get("OPENAI_API_KEY"),
        "openai_model": _get("OPENAI_MODEL", "gpt-4o-mini"),
    }


def _write_runtime_config(config: dict):
    """
    Write core runtime config to the runtime .env file.
    """
    env_path = _runtime_env_path()
    env_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# draft.ai Configuration",
        "",
        "# Mailbox",
        f"MAIL_PROVIDER={config.get('provider', 'custom')}",
        f"MAILBOX_EMAIL={config['email']}",
        f"IMAP_HOST={config['imap_host']}",
        f"IMAP_PORT={config['imap_port']}",
        f"IMAP_USERNAME={config['imap_username']}",
        f"MAILBOX_PASSWORD={config['mailbox_password']}",
        f"SOURCE_FOLDER={config.get('source_folder', 'INBOX')}",
        f"DRAFTS_FOLDER={config['drafts_folder']}",
        f"POLLING_ENABLED={config.get('polling_enabled', '1')}",
        f"RUN_IN_BACKGROUND={config.get('run_in_background', '0')}",
        "",
        "# OpenAI",
        f"OPENAI_API_KEY={config['openai_api_key']}",
        f"OPENAI_MODEL={config['openai_model']}",
        "",
        "# Database",
        f"DATABASE_URL={os.getenv('DATABASE_URL', 'sqlite:///./email_drafts.db')}",
    ]
    env_path.write_text("\n".join(lines), encoding="utf-8")


def _runtime_db_path() -> Path | None:
    database_url = os.getenv("DATABASE_URL", "sqlite:///./email_drafts.db")
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        return None
    return Path(database_url[len(prefix):])


# ---------------------------------------------------------------------------
# Mailbox seeding from environment
# ---------------------------------------------------------------------------

def _seed_mailbox_from_env():
    """
    If no mailbox exists in the DB, create one from .env values.
    This makes the MVP easy to configure without a UI settings page.
    """
    db = database.SessionLocal()
    try:
        _append_startup_trace("checking for existing mailbox row")
        existing = db.query(Mailbox).first()
        if existing:
            _append_startup_trace("existing mailbox row found; skipping seed")
            return

        email_addr = os.getenv("MAILBOX_EMAIL")
        provider = os.getenv("MAIL_PROVIDER", "custom")
        imap_host = os.getenv("IMAP_HOST")
        imap_port = int(os.getenv("IMAP_PORT", "993"))
        username = os.getenv("IMAP_USERNAME") or email_addr
        source_folder = os.getenv("SOURCE_FOLDER", "INBOX")
        drafts_folder = os.getenv("DRAFTS_FOLDER", "Drafts")
        polling_enabled = os.getenv("POLLING_ENABLED", "1") != "0"

        if not email_addr or not imap_host:
            logger.warning(
                "MAILBOX_EMAIL or IMAP_HOST not set — no mailbox seeded. "
                "Set these in your .env file."
            )
            _append_startup_trace("mailbox seed skipped because required env values were missing")
            return

        mailbox = Mailbox(
            email_address=email_addr,
            provider=provider,
            imap_host=imap_host,
            imap_port=imap_port,
            username=username,
            password_env_key="MAILBOX_PASSWORD",  # The actual password lives in this env var
            source_folder=source_folder,
            drafts_folder=drafts_folder,
            is_active=polling_enabled,
        )
        db.add(mailbox)
        db.commit()
        logger.info("Seeded mailbox for %s from environment variables.", email_addr)
        _append_startup_trace(f"seeded mailbox for {email_addr}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Core poll logic
# ---------------------------------------------------------------------------

def run_poll_job() -> dict:
    """
    Background job: poll all active mailboxes, generate drafts, append to Drafts folder.
    Called by the scheduler and also by the manual /poll endpoint.
    """
    db = database.SessionLocal()
    summary = {
        "mailboxes_processed": 0,
        "messages_processed": 0,
        "backlog_detected": False,
        "backlog_mailboxes": [],
    }
    try:
        mailboxes = db.query(Mailbox).filter(Mailbox.is_active == True).all()
        if not mailboxes:
            logger.info("No active mailboxes configured.")
            return summary

        for mailbox in mailboxes:
            mailbox_summary = _process_mailbox(mailbox, db)
            summary["mailboxes_processed"] += 1
            summary["messages_processed"] += mailbox_summary["messages_processed"]
            if mailbox_summary["backlog_detected"]:
                summary["backlog_detected"] = True
                summary["backlog_mailboxes"].append(
                    f"{mailbox.email_address} ({mailbox_summary['selected_unread_count']} selected, cap {mailbox_summary['max_unread_fetch']})"
                )
    finally:
        db.close()
    return summary


def _start_manual_poll() -> bool:
    with poll_state_lock:
        if poll_state["running"]:
            return False
        poll_state["running"] = True
        poll_state["last_started_at"] = datetime.utcnow()
        poll_state["last_result"] = None
        poll_state["last_error"] = None

    def _runner():
        try:
            summary = run_poll_job()
            with poll_state_lock:
                poll_state["last_result"] = "success"
                poll_state["last_error"] = None
                poll_state["mailboxes_processed"] = summary["mailboxes_processed"]
                poll_state["messages_processed"] = summary["messages_processed"]
                poll_state["backlog_detected"] = summary["backlog_detected"]
                poll_state["backlog_mailboxes"] = summary["backlog_mailboxes"]
                poll_state["last_run_summary"] = (
                    f"Processed {summary['messages_processed']} message(s) across {summary['mailboxes_processed']} mailbox(es)."
                )
        except Exception as exc:
            logger.error("Manual background poll failed: %s", exc, exc_info=True)
            with poll_state_lock:
                poll_state["last_result"] = "error"
                poll_state["last_error"] = str(exc)
        finally:
            with poll_state_lock:
                poll_state["running"] = False
                poll_state["last_finished_at"] = datetime.utcnow()

    threading.Thread(target=_runner, daemon=True).start()
    return True


def _process_mailbox(mailbox: Mailbox, db: Session) -> dict:
    """Process a single mailbox: fetch, draft, append, mark read."""
    summary = {
        "messages_processed": 0,
        "backlog_detected": False,
        "selected_unread_count": 0,
        "max_unread_fetch": MAX_UNREAD_FETCH,
    }
    password = os.getenv(mailbox.password_env_key)
    if not password:
        logger.error(
            "Password env var '%s' is not set for mailbox %s",
            mailbox.password_env_key,
            mailbox.email_address,
        )
        return summary

    logger.info(
        "Polling mailbox %s (message history checks are using indexed lookups, unread fetch cap=%d)",
        mailbox.email_address,
        MAX_UNREAD_FETCH,
    )

    try:
        new_messages, conn, poll_meta = poll_mailbox(
            imap_host=mailbox.imap_host,
            imap_port=mailbox.imap_port,
            username=mailbox.username,
            password=password,
            source_folder=mailbox.source_folder or "INBOX",
            drafts_folder=mailbox.drafts_folder,
            known_message_ids=None,
            max_unread_fetch=MAX_UNREAD_FETCH,
        )
        summary["backlog_detected"] = bool(poll_meta.get("backlog_detected"))
        summary["selected_unread_count"] = int(poll_meta.get("selected_unread_count", 0))
        summary["max_unread_fetch"] = int(poll_meta.get("max_unread_fetch", MAX_UNREAD_FETCH))
    except Exception as exc:
        logger.error("IMAP connection failed for %s: %s", mailbox.email_address, exc)
        return summary

    if not new_messages:
        logger.info("No new messages for %s", mailbox.email_address)
        close_connection(conn)
        return summary

    logger.info("%d new message(s) to process for %s", len(new_messages), mailbox.email_address)

    for parsed in new_messages:
        if parsed.get("message_id"):
            exists = (
                db.query(Message.id)
                .filter(
                    Message.mailbox_id == mailbox.id,
                    Message.message_id == parsed["message_id"],
                )
                .first()
            )
            if exists:
                logger.info(
                    "Skipping already processed message %s for %s",
                    parsed["message_id"],
                    mailbox.email_address,
                )
                continue
        _process_single_message(parsed, mailbox, conn, db)
        summary["messages_processed"] += 1

    close_connection(conn)
    return summary


def _process_single_message(parsed: dict, mailbox: Mailbox, conn, db: Session):
    """
    For one parsed email: clean body, generate draft, append to Drafts, mark read, save to DB.
    """
    msg_id = parsed["message_id"]
    participant_domain = _domain_from_email(parsed.get("from_email"))
    subject = parsed.get("subject")
    existing_conversation = _find_conversation(db, mailbox, parsed)
    thread_key = existing_conversation.thread_key if existing_conversation else _thread_key(mailbox.id, parsed)

    # Create DB record immediately so we don't double-process on crash
    message = Message(
        mailbox_id=mailbox.id,
        message_id=msg_id,
        uid=parsed.get("uid"),
        thread_key=thread_key,
        in_reply_to=parsed.get("in_reply_to"),
        references_header=parsed.get("references_header"),
        participant_domain=participant_domain,
        from_email=parsed.get("from_email"),
        from_name=parsed.get("from_name"),
        subject=subject,
        received_at=parsed.get("received_at"),
        body_text=_truncate_for_storage(parsed.get("body_text"), MAX_STORED_BODY_CHARS),
        category_confidence=0.5,
        needs_review=False,
        status="new",
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    try:
        # Step 1: Clean the body text
        cleaned = clean_body(parsed.get("body_text") or "")
        cleaned = _truncate_for_storage(cleaned, MAX_STORED_CLEANED_CHARS) or ""
        conversation = existing_conversation or db.query(Conversation).filter(Conversation.thread_key == thread_key).first()
        prior_category = conversation.category if conversation else None
        category = _classify_message(subject or "", cleaned, prior_category)
        category_confidence = _category_confidence(subject or "", cleaned, category)
        topics = _extract_topics(subject or "", cleaned)

        if not conversation:
            conversation = Conversation(
                mailbox_id=mailbox.id,
                thread_key=thread_key,
                participant_email=parsed.get("from_email"),
                participant_domain=participant_domain,
                subject_root=_normalize_subject(subject),
                category=category,
                last_message_at=parsed.get("received_at"),
            )
            db.add(conversation)
            db.commit()
            db.refresh(conversation)

        conversation.participant_email = parsed.get("from_email") or conversation.participant_email
        conversation.participant_domain = participant_domain or conversation.participant_domain
        conversation.category = category
        conversation.latest_inbound = _compact_text(cleaned, 220)
        conversation.summary = _conversation_summary(conversation.summary, cleaned, category, topics)
        conversation.turn_count = (conversation.turn_count or 0) + 1
        conversation.last_message_at = parsed.get("received_at") or datetime.utcnow()

        message.cleaned_text = cleaned
        message.category = category
        message.category_confidence = category_confidence
        message.needs_review = category_confidence < 0.8
        message.conversation_id = conversation.id
        message.status = "new"
        db.commit()

        # Step 2: Generate AI draft
        user_settings = db.query(Settings).filter(
            Settings.mailbox_id == mailbox.id
        ).first()
        if not user_settings or not user_settings.strict_privacy_mode:
            _update_contact_and_domain_insights(db, mailbox, message, cleaned, category)
        generation_context = _build_generation_context(db, conversation, message, user_settings)
        draft_text, model_name = generate_draft_reply(
            sender=parsed.get("from_email", ""),
            subject=subject or "",
            cleaned_body=cleaned,
            settings=user_settings,
            context=generation_context,
        )
        conversation.last_draft_text = _compact_text(draft_text, 240)
        conversation.updated_at = datetime.utcnow()
        message.status = "drafted"
        db.commit()

        # Step 3: Save draft to DB
        draft_record = Draft(
            message_id_fk=message.id,
            draft_text=draft_text,
            model_name=model_name,
            prompt_version=PROMPT_VERSION,
        )
        db.add(draft_record)
        db.commit()

        # Step 4: Append draft to Drafts folder (IMAP APPEND — NOT send)
        appended = append_draft_to_folder(
            conn=conn,
            drafts_folder=mailbox.drafts_folder,
            original_from=parsed.get("from_email", ""),
            original_subject=parsed.get("subject", ""),
            draft_body=draft_text,
            to_address=mailbox.email_address,
        )

        if appended:
            message.draft_appended = True
            message.status = "appended_to_drafts"
            db.commit()

            # Step 5: Mark original email as read — only after successful append
            marked = mark_as_read(conn, parsed["uid"])
            if marked:
                message.is_read_marked = True
                message.status = "complete"
            else:
                message.status = "appended_to_drafts"  # partial success
            db.commit()
        else:
            message.status = "error"
            message.error_message = "Failed to append draft to Drafts folder"
            db.commit()

    except Exception as exc:
        logger.error("Error processing message %s: %s", msg_id, exc, exc_info=True)
        message.status = "error"
        message.error_message = str(exc)[:1000]  # Truncate for DB storage
        db.commit()


# ---------------------------------------------------------------------------
# FastAPI routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """Main dashboard — shows recent processed messages."""
    messages = (
        db.query(Message)
        .order_by(Message.created_at.desc())
        .limit(50)
        .all()
    )
    enriched = _message_context_bundles(db, messages)

    with poll_state_lock:
        current_poll_state = dict(poll_state)

    mailbox = db.query(Mailbox).first()
    focus_message_id = request.query_params.get("focus")
    focus_entry = None
    if focus_message_id and focus_message_id.isdigit():
        focus_entry = next((item for item in enriched if item["message"].id == int(focus_message_id)), None)
    featured_entry = focus_entry or (enriched[0] if enriched else None)
    show_activity = request.query_params.get("summary") == "1" or focus_entry is not None
    activity_summary = _recent_activity_summary(enriched)

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "enriched": enriched,
            "featured_entry": featured_entry,
            "focus_entry": focus_entry,
            "show_activity": show_activity,
            "activity_summary": activity_summary,
            "now": datetime.utcnow(),
            "mailbox": mailbox,
            "poll_state": current_poll_state,
        },
    )


@app.get("/messages", response_class=HTMLResponse)
async def list_messages(request: Request, db: Session = Depends(get_db)):
    """List all processed messages."""
    page = max(int(request.query_params.get("page", "1") or "1"), 1)
    offset = (page - 1) * MESSAGE_PAGE_SIZE
    total_messages = db.query(Message).count()
    messages = (
        db.query(Message)
        .order_by(Message.created_at.desc())
        .offset(offset)
        .limit(MESSAGE_PAGE_SIZE)
        .all()
    )
    enriched_messages = _message_context_bundles(db, messages)
    return templates.TemplateResponse(
        "messages.html",
        {
            "request": request,
            "messages": enriched_messages,
            "page": page,
            "page_size": MESSAGE_PAGE_SIZE,
            "total_messages": total_messages,
            "has_prev": page > 1,
            "has_next": offset + len(messages) < total_messages,
        },
    )


@app.get("/review", response_class=HTMLResponse)
async def review_queue(request: Request, db: Session = Depends(get_db)):
    """Manual review queue for low-confidence categorizations."""
    page = max(int(request.query_params.get("page", "1") or "1"), 1)
    offset = (page - 1) * REVIEW_PAGE_SIZE
    total_messages = db.query(Message).filter(Message.needs_review == True).count()
    messages = (
        db.query(Message)
        .filter(Message.needs_review == True)
        .order_by(Message.created_at.desc())
        .offset(offset)
        .limit(REVIEW_PAGE_SIZE)
        .all()
    )
    enriched_messages = _message_context_bundles(db, messages)
    return templates.TemplateResponse(
        "review.html",
        {
            "request": request,
            "messages": enriched_messages,
            "page": page,
            "page_size": REVIEW_PAGE_SIZE,
            "total_messages": total_messages,
            "has_prev": page > 1,
            "has_next": offset + len(messages) < total_messages,
        },
    )


@app.get("/insights", response_class=HTMLResponse)
async def insights_view(request: Request, db: Session = Depends(get_db)):
    """Show the lightweight local relationship/context data stored by the app."""
    top_contacts = (
        db.query(ContactInsight)
        .order_by(ContactInsight.message_count.desc(), ContactInsight.updated_at.desc())
        .limit(12)
        .all()
    )
    top_domains = (
        db.query(DomainInsight)
        .order_by(DomainInsight.message_count.desc(), DomainInsight.updated_at.desc())
        .limit(12)
        .all()
    )
    recent_conversations = (
        db.query(Conversation)
        .order_by(Conversation.updated_at.desc())
        .limit(12)
        .all()
    )
    return templates.TemplateResponse(
        "insights.html",
        {
            "request": request,
            "top_contacts": top_contacts,
            "top_domains": top_domains,
            "recent_conversations": recent_conversations,
        },
    )


@app.get("/messages/{message_id}", response_class=HTMLResponse)
async def message_detail(request: Request, message_id: int, db: Session = Depends(get_db)):
    """Detail page for a single processed message."""
    message = db.query(Message).filter(Message.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    draft = db.query(Draft).filter(Draft.message_id_fk == message.id).first()
    conversation = db.query(Conversation).filter(Conversation.id == message.conversation_id).first() if message.conversation_id else None
    contact = None
    domain = None
    if message.from_email:
        contact = db.query(ContactInsight).filter(
            ContactInsight.contact_key == f"{message.mailbox_id}:{message.from_email.lower()}"
        ).first()
    if message.participant_domain:
        domain = db.query(DomainInsight).filter(
            DomainInsight.domain_key == f"{message.mailbox_id}:{message.participant_domain}"
        ).first()
    feedback_entries = (
        db.query(DraftFeedback)
        .filter(DraftFeedback.message_id_fk == message.id)
        .order_by(DraftFeedback.created_at.desc())
        .limit(8)
        .all()
    )
    client_preferences = _load_client_preferences(contact)
    return templates.TemplateResponse(
        "message_detail.html",
        {
            "request": request,
            "message": message,
            "draft": draft,
            "conversation": conversation,
            "contact": contact,
            "domain": domain,
            "client_preferences": client_preferences,
            "feedback_entries": feedback_entries,
            "category_options": CATEGORY_OPTIONS,
        },
    )


@app.post("/poll")
async def manual_poll(request: Request, db: Session = Depends(get_db)):
    """
    Manually trigger a poll, then redirect back to the dashboard.
    """
    active_mailbox = db.query(Mailbox).filter(Mailbox.is_active == True).first()
    if not active_mailbox:
        return RedirectResponse(url="/?poll_disabled=1", status_code=303)

    started = _start_manual_poll()
    if started:
        return RedirectResponse(url="/?poll_started=1", status_code=303)
    return RedirectResponse(url="/?poll_running=1", status_code=303)


@app.get("/poll-status")
async def poll_status():
    with poll_state_lock:
        return {
            "running": poll_state["running"],
            "last_started_at": poll_state["last_started_at"].isoformat() if poll_state["last_started_at"] else None,
            "last_finished_at": poll_state["last_finished_at"].isoformat() if poll_state["last_finished_at"] else None,
            "last_result": poll_state["last_result"],
            "last_error": poll_state["last_error"],
            "mailboxes_processed": poll_state["mailboxes_processed"],
            "messages_processed": poll_state["messages_processed"],
            "backlog_detected": poll_state["backlog_detected"],
            "backlog_mailboxes": poll_state["backlog_mailboxes"],
            "last_run_summary": poll_state["last_run_summary"],
        }



@app.get("/configuration", response_class=HTMLResponse)
async def get_configuration(request: Request, db: Session = Depends(get_db)):
    """View and edit mailbox/OpenAI runtime configuration."""
    mailbox = db.query(Mailbox).first()
    cfg = _read_runtime_config()

    # Fall back to DB values when env is missing.
    if mailbox:
        cfg["provider"] = cfg["provider"] or (mailbox.provider or "custom")
        cfg["email"] = cfg["email"] or (mailbox.email_address or "")
        cfg["imap_host"] = cfg["imap_host"] or (mailbox.imap_host or "")
        cfg["imap_port"] = cfg["imap_port"] or str(mailbox.imap_port or 993)
        cfg["imap_username"] = cfg["imap_username"] or (mailbox.username or "")
        cfg["source_folder"] = cfg["source_folder"] or (mailbox.source_folder or "INBOX")
        cfg["drafts_folder"] = cfg["drafts_folder"] or (mailbox.drafts_folder or "Drafts")
        cfg["polling_enabled"] = "1" if mailbox.is_active else "0"

    settings = db.query(Settings).first()
    if settings:
        cfg["run_in_background"] = "1" if settings.background_mode_enabled else cfg["run_in_background"]

    return templates.TemplateResponse(
        "configuration.html",
        {
            "request": request,
            "config": cfg,
            "provider_presets": PROVIDER_PRESETS,
            "saved": False,
            "error": None,
        },
    )


@app.post("/configuration/folders")
async def configuration_folders(request: Request):
    """List IMAP folders for the credentials currently entered in the configuration form."""
    data = await request.json()
    host = (data.get("imap_host") or "").strip()
    username = (data.get("imap_username") or data.get("email") or "").strip()
    password = (data.get("mailbox_password") or "").strip()

    try:
        port = int(data.get("imap_port") or 993)
    except ValueError:
        return {"ok": False, "message": "IMAP port must be a positive number.", "folders": []}

    if not host or not username or not password:
        return {"ok": False, "message": "Enter host, username, and password first.", "folders": []}

    conn = None
    try:
        conn = get_imap_connection(host, port, username, password)
        folders = list_mailboxes(conn)
        return {"ok": True, "folders": folders}
    except Exception as exc:
        return {"ok": False, "message": str(exc), "folders": []}
    finally:
        if conn:
            close_connection(conn)


@app.post("/configuration", response_class=HTMLResponse)
async def save_configuration(request: Request, db: Session = Depends(get_db)):
    """Persist mailbox/OpenAI runtime configuration for installed app usage."""
    form = await request.form()

    config = {
        "provider": (form.get("provider") or "custom").strip(),
        "email": (form.get("email") or "").strip(),
        "imap_host": (form.get("imap_host") or "").strip(),
        "imap_port": (form.get("imap_port") or "993").strip(),
        "imap_username": (form.get("imap_username") or "").strip(),
        "mailbox_password": (form.get("mailbox_password") or "").strip(),
        "source_folder": (form.get("source_folder") or "INBOX").strip(),
        "drafts_folder": (form.get("drafts_folder") or "Drafts").strip(),
        "polling_enabled": "1" if form.get("polling_enabled") == "on" else "0",
        "run_in_background": "1" if form.get("run_in_background") == "on" else "0",
        "openai_api_key": (form.get("openai_api_key") or "").strip(),
        "openai_model": (form.get("openai_model") or "gpt-4o-mini").strip(),
    }

    required_keys = [
        "email",
        "imap_host",
        "imap_port",
        "imap_username",
        "mailbox_password",
        "source_folder",
        "drafts_folder",
        "openai_api_key",
        "openai_model",
    ]
    missing = [k for k in required_keys if not config[k]]
    if missing:
        return templates.TemplateResponse(
            "configuration.html",
            {
                "request": request,
                "config": config,
                "provider_presets": PROVIDER_PRESETS,
                "saved": False,
                "error": "Please fill in all configuration fields.",
            },
        )

    try:
        imap_port = int(config["imap_port"])
        if imap_port <= 0:
            raise ValueError
    except ValueError:
        return templates.TemplateResponse(
            "configuration.html",
            {
                "request": request,
                "config": config,
                "provider_presets": PROVIDER_PRESETS,
                "saved": False,
                "error": "IMAP port must be a positive number.",
            },
        )

    config["imap_port"] = str(imap_port)

    # Persist to runtime .env used by desktop app.
    _write_runtime_config(config)

    # Update process env so changes take effect immediately.
    os.environ["MAIL_PROVIDER"] = config["provider"]
    os.environ["MAILBOX_EMAIL"] = config["email"]
    os.environ["IMAP_HOST"] = config["imap_host"]
    os.environ["IMAP_PORT"] = config["imap_port"]
    os.environ["IMAP_USERNAME"] = config["imap_username"]
    os.environ["MAILBOX_PASSWORD"] = config["mailbox_password"]
    os.environ["SOURCE_FOLDER"] = config["source_folder"]
    os.environ["DRAFTS_FOLDER"] = config["drafts_folder"]
    os.environ["POLLING_ENABLED"] = config["polling_enabled"]
    os.environ["RUN_IN_BACKGROUND"] = config["run_in_background"]
    os.environ["OPENAI_API_KEY"] = config["openai_api_key"]
    os.environ["OPENAI_MODEL"] = config["openai_model"]

    mailbox = db.query(Mailbox).first()
    if not mailbox:
        mailbox = Mailbox(password_env_key="MAILBOX_PASSWORD", is_active=True)
        db.add(mailbox)

    mailbox.email_address = config["email"]
    mailbox.provider = config["provider"]
    mailbox.imap_host = config["imap_host"]
    mailbox.imap_port = imap_port
    mailbox.username = config["imap_username"]
    mailbox.password_env_key = "MAILBOX_PASSWORD"
    mailbox.source_folder = config["source_folder"]
    mailbox.drafts_folder = config["drafts_folder"]
    mailbox.is_active = config["polling_enabled"] == "1"

    settings = db.query(Settings).filter(Settings.mailbox_id == mailbox.id).first() if mailbox.id else None
    if not settings and mailbox.id:
        settings = Settings(mailbox_id=mailbox.id)
        db.add(settings)

    db.commit()

    if not settings:
        settings = db.query(Settings).filter(Settings.mailbox_id == mailbox.id).first()
        if not settings:
            settings = Settings(mailbox_id=mailbox.id)
            db.add(settings)

    settings.background_mode_enabled = config["run_in_background"] == "1"
    db.commit()

    return templates.TemplateResponse(
        "configuration.html",
        {
            "request": request,
            "config": config,
            "provider_presets": PROVIDER_PRESETS,
            "saved": True,
            "error": None,
        },
    )

@app.get("/settings", response_class=HTMLResponse)
async def get_settings(request: Request, db: Session = Depends(get_db)):
    """Settings page — configure AI prompt, signature, tone, etc."""
    mailbox = db.query(Mailbox).first()
    settings = db.query(Settings).first()
    if not settings and mailbox:
        settings = Settings(mailbox_id=mailbox.id)
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "mailbox": mailbox,
            "settings": settings,
            "category_options": CATEGORY_OPTIONS,
            "saved": False,
            "imported_signature": False,
        },
    )


@app.post("/settings", response_class=HTMLResponse)
async def save_settings(
    request: Request,
    db: Session = Depends(get_db),
):
    """Save settings from the form."""
    form = await request.form()
    mailbox = db.query(Mailbox).first()
    if not mailbox:
        raise HTTPException(status_code=400, detail="No mailbox configured.")

    settings = db.query(Settings).filter(Settings.mailbox_id == mailbox.id).first()
    if not settings:
        settings = Settings(mailbox_id=mailbox.id)
        db.add(settings)

    action = (form.get("settings_action") or "save").strip().lower()
    imported_signature = False
    imported_html, imported_text = _import_signature_payload(form)
    if imported_html and action == "import_signature":
        settings.signature_html = imported_html
        imported_signature = True

    settings.sender_name = form.get("sender_name", "").strip() or None
    settings.company_name = form.get("company_name", "").strip() or None
    settings.tone = form.get("tone", "professional")
    settings.reply_length = form.get("reply_length", "normal")
    settings.custom_instructions = form.get("custom_instructions", "").strip() or None
    settings.business_context = form.get("business_context", "").strip() or None
    settings.hard_rules = form.get("hard_rules", "").strip() or None
    settings.escalation_guidance = form.get("escalation_guidance", "").strip() or None
    settings.example_phrasing = form.get("example_phrasing", "").strip() or None
    settings.client_prompt = form.get("client_prompt", "").strip() or None
    settings.sales_prompt = form.get("sales_prompt", "").strip() or None
    settings.support_prompt = form.get("support_prompt", "").strip() or None
    settings.scheduling_prompt = form.get("scheduling_prompt", "").strip() or None
    settings.billing_prompt = form.get("billing_prompt", "").strip() or None
    settings.general_prompt = form.get("general_prompt", "").strip() or None
    settings.lightweight_context_enabled = form.get("lightweight_context_enabled") == "on"
    settings.strict_privacy_mode = form.get("strict_privacy_mode") == "on"
    manual_signature = form.get("signature", "").strip() or None
    settings.signature = imported_text if imported_signature else manual_signature
    if not imported_signature and manual_signature:
        settings.signature_html = None
    elif not imported_signature and not manual_signature:
        settings.signature_html = None
    settings.footer_link_label = form.get("footer_link_label", "").strip() or None
    settings.footer_link = form.get("footer_link", "").strip() or None

    db.commit()

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "mailbox": mailbox,
            "settings": settings,
            "category_options": CATEGORY_OPTIONS,
            "saved": True,
            "imported_signature": imported_signature,
        },
    )


@app.get("/health/view", response_class=HTMLResponse)
async def health_view(request: Request, db: Session = Depends(get_db)):
    """Human-friendly health page for the desktop UI."""
    message_count = db.query(Message).count()
    mailbox_count = db.query(Mailbox).filter(Mailbox.is_active == True).count()
    mailbox = db.query(Mailbox).first()
    settings = db.query(Settings).first()
    return templates.TemplateResponse(
        "health.html",
        {
            "request": request,
            "status": "healthy",
            "message_count": message_count,
            "mailbox_count": mailbox_count,
            "mailbox": mailbox,
            "background_mode_enabled": bool(settings and settings.background_mode_enabled),
            "timestamp": datetime.utcnow().isoformat(),
            "database_url": os.getenv("DATABASE_URL", "sqlite:///./email_drafts.db"),
        },
    )


@app.post("/messages/{message_id}/feedback")
async def save_draft_feedback(message_id: int, request: Request, db: Session = Depends(get_db)):
    """Store explicit thumbs up/down feedback and fold it into client preferences."""
    message = db.query(Message).filter(Message.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    form = await request.form()
    signal = (form.get("signal") or "up").strip().lower()
    reason = (form.get("reason") or "").strip() or None
    feedback_type = (form.get("feedback_type") or "draft").strip().lower()

    contact = None
    if message.from_email:
        contact = db.query(ContactInsight).filter(
            ContactInsight.contact_key == f"{message.mailbox_id}:{message.from_email.lower()}"
        ).first()

    feedback = DraftFeedback(
        mailbox_id=message.mailbox_id,
        message_id_fk=message.id,
        contact_insight_id=contact.id if contact else None,
        feedback_type=feedback_type,
        signal=signal,
        reason=reason,
        original_value=message.category if feedback_type == "category" else None,
    )
    db.add(feedback)
    _apply_feedback_to_preferences(contact, signal, reason)
    db.commit()
    return RedirectResponse(url=f"/messages/{message.id}", status_code=303)


@app.post("/messages/{message_id}/category")
async def update_message_category(message_id: int, request: Request, db: Session = Depends(get_db)):
    """Human-in-the-loop category correction and review resolution."""
    message = db.query(Message).filter(Message.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")

    form = await request.form()
    new_category = (form.get("category") or "general").strip().lower()
    if new_category not in CATEGORY_OPTIONS:
        new_category = "general"

    prior_category = message.category or "general"
    message.category = new_category
    message.manual_category = new_category
    message.category_confidence = 1.0
    message.needs_review = False

    if message.conversation_id:
        conversation = db.query(Conversation).filter(Conversation.id == message.conversation_id).first()
        if conversation:
            conversation.category = new_category

    contact = None
    if message.from_email:
        contact = db.query(ContactInsight).filter(
            ContactInsight.contact_key == f"{message.mailbox_id}:{message.from_email.lower()}"
        ).first()
    if contact:
        contact.category_hint = new_category
        preferences = _load_client_preferences(contact)
        examples = preferences.get("category_examples") or {}
        examples[_normalize_subject(message.subject)] = new_category
        preferences["category_examples"] = examples
        _save_client_preferences(contact, preferences)

    feedback = DraftFeedback(
        mailbox_id=message.mailbox_id,
        message_id_fk=message.id,
        contact_insight_id=contact.id if contact else None,
        feedback_type="category",
        signal="corrected",
        reason=f"Changed category from {prior_category} to {new_category}",
        original_value=prior_category,
        updated_value=new_category,
    )
    db.add(feedback)
    db.commit()
    return RedirectResponse(url=f"/messages/{message.id}", status_code=303)


@app.post("/reset-app-data", response_class=HTMLResponse)
async def reset_app_data(request: Request, db: Session = Depends(get_db)):
    """Clear saved runtime state so the next launch starts from setup again."""
    env_path = _runtime_env_path()
    db_path = _runtime_db_path()

    db.close()
    database.engine.dispose()

    if scheduler.running:
        scheduler.shutdown(wait=False)
        _append_startup_trace("scheduler shut down for app-data reset")

    if env_path.exists():
        env_path.unlink()

    if db_path and db_path.exists():
        db_path.unlink()

    for key in [
        "MAILBOX_EMAIL",
        "IMAP_HOST",
        "IMAP_PORT",
        "IMAP_USERNAME",
        "MAILBOX_PASSWORD",
        "DRAFTS_FOLDER",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
    ]:
        os.environ.pop(key, None)

    return templates.TemplateResponse(
        "reset_complete.html",
        {
            "request": request,
            "env_path": str(env_path),
            "db_path": str(db_path) if db_path else "Unavailable",
        },
    )


@app.get("/health")
async def health_check(db: Session = Depends(get_db)):
    """Basic health check endpoint."""
    try:
        message_count = db.query(Message).count()
        _append_startup_trace(f"health endpoint served successfully with {message_count} messages")
        return {
            "status": "healthy",
            "message_count": message_count,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as exc:
        _append_startup_trace(f"health endpoint failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Health check failed: {str(exc)}")

