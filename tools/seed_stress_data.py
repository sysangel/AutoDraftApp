"""Seed a synthetic Draft database for local stress testing.

This script creates a separate SQLite database with realistic-looking
mailboxes, messages, drafts, conversations, and lightweight insights so we
can test Draft's UI and query behavior without touching real email data.
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


SUBJECT_PATTERNS = {
    "client": [
        "Project update request",
        "Revision round follow-up",
        "Client launch checklist",
        "Status update for deliverables",
    ],
    "support": [
        "Issue with dashboard access",
        "Bug report for mailbox polling",
        "Need help with configuration",
        "Support follow-up on failed drafts",
    ],
    "sales": [
        "Pricing questions for next quarter",
        "Can you send a proposal",
        "Follow-up after intro meeting",
        "Drafting assistant demo request",
    ],
    "scheduling": [
        "Meeting availability next week",
        "Can we reschedule Friday",
        "Calendar hold for onboarding",
        "Time options for client review",
    ],
    "billing": [
        "Invoice follow-up",
        "Payment confirmation needed",
        "Quote revision request",
        "Billing question for March",
    ],
    "general": [
        "Quick follow-up",
        "Checking in on next steps",
        "Question about your process",
        "Need a little clarification",
    ],
}


BODY_PATTERNS = {
    "client": "Hello team, we wanted to check on the latest deliverable status, timing, and any revision needs before the client review.",
    "support": "We are running into an issue and need help understanding why the current workflow is failing for our inbox processing.",
    "sales": "We are evaluating options and would like a concise breakdown of pricing, onboarding, and what the product includes.",
    "scheduling": "Could you share a few times that work next week so we can lock in the meeting and keep momentum moving.",
    "billing": "Can you confirm the invoice details, due date, and whether there are any updates needed before payment is processed.",
    "general": "Just reaching out with a quick follow-up and a couple of light questions so we can understand the next step clearly.",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed a synthetic Draft SQLite database.")
    parser.add_argument(
        "--db-path",
        default=str(REPO_ROOT / "stress_test.db"),
        help="Path to the SQLite file to create or reuse.",
    )
    parser.add_argument("--messages", type=int, default=1500, help="Total synthetic messages to create.")
    parser.add_argument("--mailboxes", type=int, default=2, help="Number of synthetic mailboxes.")
    parser.add_argument("--domains", type=int, default=8, help="Number of synthetic sender domains.")
    parser.add_argument("--reset", action="store_true", help="Delete the target SQLite file first.")
    parser.add_argument("--review-rate", type=float, default=0.14, help="Fraction of messages flagged for review.")
    return parser.parse_args()


def configure_database(db_path: Path):
    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    import db

    db.init_db()
    return db


def weighted_category(index: int) -> str:
    ordered = ["client", "support", "sales", "scheduling", "billing", "general"]
    return ordered[index % len(ordered)]


def compact(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def seed_database(args: argparse.Namespace) -> Path:
    db_path = Path(args.db_path).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if args.reset and db_path.exists():
        db_path.unlink()

    db = configure_database(db_path)
    from models import ContactInsight, Conversation, DomainInsight, Draft, Mailbox, Message

    session = db.SessionLocal()
    now = datetime.now()

    try:
        mailboxes = []
        for mailbox_index in range(args.mailboxes):
            mailbox = Mailbox(
                email_address=f"stress{mailbox_index + 1}@draft.local",
                imap_host="imap.example.test",
                imap_port=993,
                username=f"stress{mailbox_index + 1}@draft.local",
                password_env_key="MAILBOX_PASSWORD",
                provider="custom",
                source_folder="INBOX",
                drafts_folder="Drafts",
                is_active=True,
            )
            session.add(mailbox)
            mailboxes.append(mailbox)
        session.commit()
        for mailbox in mailboxes:
            session.refresh(mailbox)

        domains = [f"domain{idx + 1}.example" for idx in range(args.domains)]
        for idx in range(args.messages):
            mailbox = mailboxes[idx % len(mailboxes)]
            category = weighted_category(idx)
            domain = domains[idx % len(domains)]
            contact_email = f"client{idx % max(120, args.messages // 8)}@{domain}"
            subject = SUBJECT_PATTERNS[category][idx % len(SUBJECT_PATTERNS[category])]
            body = BODY_PATTERNS[category]
            received_at = now - timedelta(minutes=idx * 3)
            thread_key = f"{mailbox.id}|subj|{domain}|thread-{idx % max(90, args.messages // 10)}"

            conversation = (
                session.query(Conversation)
                .filter(Conversation.thread_key == thread_key)
                .first()
            )
            if not conversation:
                conversation = Conversation(
                    mailbox_id=mailbox.id,
                    thread_key=thread_key,
                    participant_email=contact_email,
                    participant_domain=domain,
                    subject_root=subject,
                    category=category,
                    summary=compact(f"Recent conversation about {category}, planning, and next actions for {domain}.", 320),
                    latest_inbound=compact(body, 220),
                    turn_count=0,
                    last_message_at=received_at,
                )
                session.add(conversation)
                session.flush()

            conversation.turn_count = (conversation.turn_count or 0) + 1
            conversation.last_message_at = received_at
            conversation.latest_inbound = compact(body, 220)

            msg = Message(
                mailbox_id=mailbox.id,
                message_id=f"<stress-{mailbox.id}-{idx}@draft.local>",
                uid=str(idx + 1),
                thread_key=thread_key,
                conversation_id=conversation.id,
                in_reply_to=None,
                references_header=None,
                category=category,
                category_confidence=0.74 if idx % max(3, round(1 / max(args.review_rate, 0.01))) == 0 else 0.92,
                needs_review=idx % max(3, round(1 / max(args.review_rate, 0.01))) == 0,
                participant_domain=domain,
                from_email=contact_email,
                from_name=f"Client {idx % 250}",
                subject=subject,
                received_at=received_at,
                body_text=body * 3,
                cleaned_text=body,
                is_read_marked=True,
                draft_appended=True,
                status="complete",
            )
            session.add(msg)
            session.flush()

            session.add(
                Draft(
                    message_id_fk=msg.id,
                    draft_text=compact(
                        f"Hi {msg.from_name}, thanks for reaching out about {category}. "
                        f"Here is a calm, concise response with the next steps and a clear follow-up question.",
                        500,
                    ),
                    model_name="gpt-test",
                    prompt_version="stress-seed",
                )
            )

            contact_key = f"{mailbox.id}:{contact_email.lower()}"
            contact = session.query(ContactInsight).filter(ContactInsight.contact_key == contact_key).first()
            if not contact:
                contact = ContactInsight(
                    mailbox_id=mailbox.id,
                    contact_key=contact_key,
                    contact_email=contact_email.lower(),
                    contact_name=msg.from_name,
                    domain=domain,
                    category_hint=category,
                    summary=compact(
                        f"{msg.from_name} usually writes about {category}, planning, and response timing.",
                        260,
                    ),
                    common_topics="timing, follow-up, planning",
                    message_count=0,
                    last_seen_at=received_at,
                )
                session.add(contact)
            contact.contact_name = msg.from_name
            contact.category_hint = category
            contact.summary = compact(
                f"{msg.from_name} usually writes about {category}, planning, and response timing.",
                260,
            )
            contact.message_count = (contact.message_count or 0) + 1
            contact.last_seen_at = received_at

            domain_key = f"{mailbox.id}:{domain}"
            domain_insight = session.query(DomainInsight).filter(DomainInsight.domain_key == domain_key).first()
            if not domain_insight:
                domain_insight = DomainInsight(
                    mailbox_id=mailbox.id,
                    domain_key=domain_key,
                    domain=domain,
                    summary=compact(
                        f"Messages from {domain} commonly revolve around {category}, coordination, and deadlines.",
                        260,
                    ),
                    common_topics="coordination, updates, next steps",
                    top_category=category,
                    message_count=0,
                    last_seen_at=received_at,
                )
                session.add(domain_insight)
            domain_insight.top_category = category
            domain_insight.summary = compact(
                f"Messages from {domain} commonly revolve around {category}, coordination, and deadlines.",
                260,
            )
            domain_insight.message_count = (domain_insight.message_count or 0) + 1
            domain_insight.last_seen_at = received_at

            if idx % 100 == 0:
                session.commit()

        session.commit()
    finally:
        session.close()

    return db_path


def main() -> int:
    args = parse_args()
    random.seed(42)
    db_path = seed_database(args)
    print(f"Seeded synthetic stress database at: {db_path}")
    print(f"Messages: {args.messages}")
    print(f"Mailboxes: {args.mailboxes}")
    print("")
    print("To test Draft against this database in a source run, use:")
    print(f"  set DATABASE_URL=sqlite:///{db_path.as_posix()}")
    print("  python -m dotenv run uvicorn app:app --host 127.0.0.1 --port 8000")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
