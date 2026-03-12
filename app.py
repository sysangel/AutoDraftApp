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

from dotenv import load_dotenv
load_dotenv()
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

import db as database
from db import get_db, init_db
from imap_service import (
    append_draft_to_folder,
    clean_body,
    close_connection,
    mark_as_read,
    poll_mailbox,
)
from ai_service import generate_draft_reply, PROMPT_VERSION
from models import Draft, Mailbox, Message, Settings

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------
scheduler = BackgroundScheduler()

# ---------------------------------------------------------------------------
# App lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB and start background scheduler on startup."""
    init_db()
    logger.info("Database initialized.")

    # Seed a mailbox from environment variables if none exists yet
    _seed_mailbox_from_env()

    # Schedule polling every 2 minutes
    scheduler.add_job(
        run_poll_job,
        "interval",
        minutes=2,
        id="email_poll",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Background scheduler started (polling every 2 minutes).")

    yield  # App is running

    scheduler.shutdown(wait=False)
    logger.info("Scheduler shut down.")


app = FastAPI(
    title="Email Draft App",
    description="Reads emails, drafts replies, never sends.",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


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
        existing = db.query(Mailbox).first()
        if existing:
            return

        email_addr = os.getenv("MAILBOX_EMAIL")
        imap_host = os.getenv("IMAP_HOST")
        imap_port = int(os.getenv("IMAP_PORT", "993"))
        username = os.getenv("IMAP_USERNAME") or email_addr
        drafts_folder = os.getenv("DRAFTS_FOLDER", "Drafts")

        if not email_addr or not imap_host:
            logger.warning(
                "MAILBOX_EMAIL or IMAP_HOST not set — no mailbox seeded. "
                "Set these in your .env file."
            )
            return

        mailbox = Mailbox(
            email_address=email_addr,
            imap_host=imap_host,
            imap_port=imap_port,
            username=username,
            password_env_key="MAILBOX_PASSWORD",  # The actual password lives in this env var
            drafts_folder=drafts_folder,
            is_active=True,
        )
        db.add(mailbox)
        db.commit()
        logger.info("Seeded mailbox for %s from environment variables.", email_addr)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Core poll logic
# ---------------------------------------------------------------------------

def run_poll_job():
    """
    Background job: poll all active mailboxes, generate drafts, append to Drafts folder.
    Called by the scheduler and also by the manual /poll endpoint.
    """
    db = database.SessionLocal()
    try:
        mailboxes = db.query(Mailbox).filter(Mailbox.is_active == True).all()
        if not mailboxes:
            logger.info("No active mailboxes configured.")
            return

        for mailbox in mailboxes:
            _process_mailbox(mailbox, db)
    finally:
        db.close()


def _process_mailbox(mailbox: Mailbox, db: Session):
    """Process a single mailbox: fetch, draft, append, mark read."""
    password = os.getenv(mailbox.password_env_key)
    if not password:
        logger.error(
            "Password env var '%s' is not set for mailbox %s",
            mailbox.password_env_key,
            mailbox.email_address,
        )
        return

    # Build set of already-seen Message-IDs to skip
    known_ids = {
        row.message_id
        for row in db.query(Message.message_id).filter(
            Message.mailbox_id == mailbox.id
        ).all()
    }

    logger.info("Polling mailbox %s (%d known IDs)", mailbox.email_address, len(known_ids))

    try:
        new_messages, conn = poll_mailbox(
            imap_host=mailbox.imap_host,
            imap_port=mailbox.imap_port,
            username=mailbox.username,
            password=password,
            drafts_folder=mailbox.drafts_folder,
            known_message_ids=known_ids,
        )
    except Exception as exc:
        logger.error("IMAP connection failed for %s: %s", mailbox.email_address, exc)
        return

    if not new_messages:
        logger.info("No new messages for %s", mailbox.email_address)
        close_connection(conn)
        return

    logger.info("%d new message(s) to process for %s", len(new_messages), mailbox.email_address)

    for parsed in new_messages:
        _process_single_message(parsed, mailbox, conn, db)

    close_connection(conn)


def _process_single_message(parsed: dict, mailbox: Mailbox, conn, db: Session):
    """
    For one parsed email: clean body, generate draft, append to Drafts, mark read, save to DB.
    """
    msg_id = parsed["message_id"]

    # Create DB record immediately so we don't double-process on crash
    message = Message(
        mailbox_id=mailbox.id,
        message_id=msg_id,
        uid=parsed.get("uid"),
        from_email=parsed.get("from_email"),
        from_name=parsed.get("from_name"),
        subject=parsed.get("subject"),
        received_at=parsed.get("received_at"),
        body_text=parsed.get("body_text"),
        status="new",
    )
    db.add(message)
    db.commit()
    db.refresh(message)

    try:
        # Step 1: Clean the body text
        cleaned = clean_body(parsed.get("body_text") or "")
        message.cleaned_text = cleaned
        message.status = "new"
        db.commit()

        # Step 2: Generate AI draft
        user_settings = db.query(Settings).filter(
            Settings.mailbox_id == mailbox.id
        ).first()
        draft_text, model_name = generate_draft_reply(
            sender=parsed.get("from_email", ""),
            subject=parsed.get("subject", ""),
            cleaned_body=cleaned,
            settings=user_settings,
        )
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
    # Attach draft text to each message for display
    enriched = []
    for msg in messages:
        draft = db.query(Draft).filter(Draft.message_id_fk == msg.id).first()
        enriched.append({"message": msg, "draft": draft})

    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "enriched": enriched, "now": datetime.utcnow()},
    )


@app.get("/messages", response_class=HTMLResponse)
async def list_messages(request: Request, db: Session = Depends(get_db)):
    """List all processed messages."""
    messages = (
        db.query(Message)
        .order_by(Message.created_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "messages.html",
        {"request": request, "messages": messages},
    )


@app.get("/messages/{message_id}", response_class=HTMLResponse)
async def message_detail(request: Request, message_id: int, db: Session = Depends(get_db)):
    """Detail page for a single processed message."""
    message = db.query(Message).filter(Message.id == message_id).first()
    if not message:
        raise HTTPException(status_code=404, detail="Message not found")
    draft = db.query(Draft).filter(Draft.message_id_fk == message.id).first()
    return templates.TemplateResponse(
        "message_detail.html",
        {"request": request, "message": message, "draft": draft},
    )


@app.post("/poll")
async def manual_poll(request: Request):
    """
    Manually trigger a poll. Useful for testing or on-demand runs.
    Returns a simple status message.
    """
    try:
        run_poll_job()
        return {"status": "ok", "message": "Poll completed successfully."}
    except Exception as exc:
        logger.error("Manual poll failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Poll failed: {str(exc)}")


@app.get("/settings", response_class=HTMLResponse)
async def get_settings(request: Request, db: Session = Depends(get_db)):
    """Settings page — configure AI prompt, signature, tone, etc."""
    mailbox = db.query(Mailbox).first()
    settings = db.query(Settings).first()
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "mailbox": mailbox, "settings": settings},
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

    settings.sender_name = form.get("sender_name", "").strip() or None
    settings.company_name = form.get("company_name", "").strip() or None
    settings.tone = form.get("tone", "professional")
    settings.custom_instructions = form.get("custom_instructions", "").strip() or None
    settings.signature = form.get("signature", "").strip() or None
    settings.footer_link_label = form.get("footer_link_label", "").strip() or None
    settings.footer_link = form.get("footer_link", "").strip() or None

    db.commit()

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "mailbox": mailbox,
            "settings": settings,
            "saved": True,
        },
    )


@app.get("/health")
async def health_check(db: Session = Depends(get_db)):
    """Basic health check endpoint."""
    try:
        message_count = db.query(Message).count()
        return {
            "status": "healthy",
            "message_count": message_count,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Health check failed: {str(exc)}")
