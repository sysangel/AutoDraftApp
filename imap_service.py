"""
IMAP service: connect, read inbox, parse messages, append drafts, mark as read.

This module NEVER sends email. It only:
  1. Reads messages from INBOX
  2. Appends draft messages into the Drafts folder
  3. Marks processed emails as \\Seen

SMTP is intentionally absent.
"""

import imaplib
import email
import email.header
import email.utils
import logging
import os
import re
import html
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)
DEFAULT_MAX_UNREAD_FETCH = 75

# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------

def get_imap_connection(imap_host: str, imap_port: int, username: str, password: str) -> imaplib.IMAP4_SSL:
    """
    Open and authenticate an IMAP SSL connection.
    Raises on failure — callers should handle exceptions.
    """
    conn = imaplib.IMAP4_SSL(imap_host, imap_port)
    conn.login(username, password)
    logger.info("IMAP login successful for %s @ %s", username, imap_host)
    return conn


def close_connection(conn: imaplib.IMAP4_SSL):
    """Gracefully close an IMAP connection."""
    try:
        conn.logout()
    except Exception:
        pass


def list_mailboxes(conn: imaplib.IMAP4_SSL) -> list[str]:
    """Return a best-effort list of IMAP folder names available for this account."""
    status, data = conn.list()
    if status != "OK" or not data:
        return []

    folders = []
    for row in data:
        decoded = row.decode(errors="replace") if isinstance(row, bytes) else str(row)
        if ' "/" ' in decoded:
            folder = decoded.rsplit(' "/" ', 1)[-1].strip().strip('"')
        else:
            folder = decoded.split()[-1].strip().strip('"')
        if folder:
            folders.append(folder)

    ordered = []
    seen = set()
    for folder in folders:
        if folder in seen:
            continue
        seen.add(folder)
        ordered.append(folder)
    return ordered


# ---------------------------------------------------------------------------
# Fetch unread messages
# ---------------------------------------------------------------------------

def fetch_unread_uids(
    conn: imaplib.IMAP4_SSL,
    source_folder: str = "INBOX",
    max_unread_fetch: int = DEFAULT_MAX_UNREAD_FETCH,
) -> list[str]:
    """
    Select the configured source folder and return a list of unread (UNSEEN) message UIDs.
    Returns an empty list if none found.
    """
    status, _ = conn.select(source_folder)
    if status != "OK":
        raise RuntimeError(f"Could not open IMAP folder '{source_folder}'.")
    status, data = conn.uid("SEARCH", None, "UNSEEN")
    if status != "OK" or not data or not data[0]:
        return []
    uid_list = data[0].decode().split()
    if max_unread_fetch > 0 and len(uid_list) > max_unread_fetch:
        logger.info(
            "Unread UID count %d exceeds cap %d; processing newest subset only.",
            len(uid_list),
            max_unread_fetch,
        )
        uid_list = uid_list[-max_unread_fetch:]
    logger.info("Found %d unread UIDs selected for processing", len(uid_list))
    return uid_list


def fetch_raw_message(conn: imaplib.IMAP4_SSL, uid: str) -> Optional[bytes]:
    """
    Fetch the full RFC 2822 raw bytes of a message by UID.
    Returns None on failure.
    """
    status, data = conn.uid("FETCH", uid, "(RFC822)")
    if status != "OK" or not data or not data[0]:
        logger.warning("Failed to fetch UID %s", uid)
        return None
    # data[0] is a tuple: (b'UID RFC822 {size}', b'<raw bytes>')
    if isinstance(data[0], tuple):
        return data[0][1]
    return None


# ---------------------------------------------------------------------------
# Message parsing
# ---------------------------------------------------------------------------

def decode_header_value(raw_value: str) -> str:
    """Decode a possibly encoded email header into a plain string."""
    if not raw_value:
        return ""
    parts = email.header.decode_header(raw_value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def strip_html_tags(html_content: str) -> str:
    """
    Very basic HTML-to-text: unescape entities and strip tags.
    Good enough for MVP; consider html2text for production.
    """
    text = html.unescape(html_content)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<p\s*/?>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


def extract_body(msg: email.message.Message) -> str:
    """
    Extract plain text from a parsed email.Message.
    Prefers text/plain; falls back to stripping text/html.
    """
    plain_parts = []
    html_parts = []

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            # Skip attachments
            if "attachment" in disposition:
                continue
            if ctype == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                plain_parts.append(part.get_payload(decode=True).decode(charset, errors="replace"))
            elif ctype == "text/html":
                charset = part.get_content_charset() or "utf-8"
                html_parts.append(part.get_payload(decode=True).decode(charset, errors="replace"))
    else:
        ctype = msg.get_content_type()
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True)
        if payload:
            raw = payload.decode(charset, errors="replace")
            if ctype == "text/plain":
                plain_parts.append(raw)
            elif ctype == "text/html":
                html_parts.append(raw)

    if plain_parts:
        return "\n".join(plain_parts)
    if html_parts:
        return strip_html_tags("\n".join(html_parts))
    return ""


def parse_message(raw_bytes: bytes, uid: str) -> dict:
    """
    Parse raw email bytes into a dict with the fields we care about.
    Returns a dict with keys: message_id, from_email, from_name,
    subject, received_at, body_text, in_reply_to, references_header.
    """
    msg = email.message_from_bytes(raw_bytes)

    message_id = (msg.get("Message-ID") or "").strip()
    subject = decode_header_value(msg.get("Subject", ""))
    from_raw = decode_header_value(msg.get("From", ""))
    from_name, from_email = email.utils.parseaddr(from_raw)

    # Parse date
    date_str = msg.get("Date", "")
    received_at = None
    if date_str:
        try:
            date_tuple = email.utils.parsedate_to_datetime(date_str)
            received_at = date_tuple
        except Exception:
            received_at = datetime.utcnow()

    body_text = extract_body(msg)
    in_reply_to = decode_header_value(msg.get("In-Reply-To", "")).strip()
    references_header = decode_header_value(msg.get("References", "")).strip()

    return {
        "uid": uid,
        "message_id": message_id,
        "in_reply_to": in_reply_to,
        "references_header": references_header,
        "from_email": from_email,
        "from_name": from_name,
        "subject": subject,
        "received_at": received_at,
        "body_text": body_text,
    }


# ---------------------------------------------------------------------------
# Text cleaning for AI prompt
# ---------------------------------------------------------------------------

# Simple heuristics for common signature separators
_SIGNATURE_MARKERS = [
    r"^--\s*$",
    r"^_{3,}",
    r"^-{3,}",
    r"^Sent from my",
    r"^Get Outlook",
    r"^Regards,",
    r"^Best regards,",
    r"^Thanks,",
    r"^Cheers,",
    r"^Sincerely,",
]
_SIG_RE = re.compile("|".join(_SIGNATURE_MARKERS), re.IGNORECASE | re.MULTILINE)

# Quoted reply markers
_QUOTE_RE = re.compile(r"^(>|On .+ wrote:)", re.MULTILINE)


def clean_body(body: str) -> str:
    """
    Remove quoted history and signatures from an email body.
    Keeps only the top-most (freshest) text block for the AI.
    """
    if not body:
        return ""

    # Cut at first quote block
    match = _QUOTE_RE.search(body)
    if match:
        body = body[:match.start()]

    # Cut at first signature marker
    match = _SIG_RE.search(body)
    if match:
        body = body[:match.start()]

    # Collapse excessive blank lines
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


# ---------------------------------------------------------------------------
# Append draft to Drafts folder
# ---------------------------------------------------------------------------

def append_draft_to_folder(
    conn: imaplib.IMAP4_SSL,
    drafts_folder: str,
    original_from: str,
    original_subject: str,
    draft_body: str,
    to_address: str,
) -> bool:
    """
    Append a plain-text draft reply into the mailbox Drafts folder via IMAP APPEND.

    NOTE: This does NOT send the email. It only places the message
    in the Drafts folder so the user can review and send it manually.

    Common Drafts folder names on different providers:
      - Drafts         (standard / Bluehost default)
      - INBOX.Drafts   (some cPanel/Dovecot configs)
      - Draft          (some older servers)
    Adjust DRAFTS_FOLDER in your .env to match your provider.

    Returns True on success, False on failure.
    """
    subject = f"Re: {original_subject}" if original_subject else "Re: (no subject)"

    # Build a minimal RFC 2822 message for the draft
    draft_msg = email.message.Message()
    draft_msg["From"] = to_address  # The mailbox owner is replying
    draft_msg["To"] = original_from
    draft_msg["Subject"] = subject
    draft_msg["Date"] = email.utils.formatdate(localtime=True)
    draft_msg["Content-Type"] = "text/plain; charset=utf-8"
    draft_msg.set_payload(draft_body, charset="utf-8")

    raw_draft = draft_msg.as_bytes()

    # IMAP APPEND: appends message to the folder with \Draft flag
    status, data = conn.append(
        drafts_folder,
        r"(\Draft)",            # set the \Draft flag
        imaplib.Time2Internaldate(datetime.now(timezone.utc)),
        raw_draft,
    )

    if status == "OK":
        logger.info("Draft appended to folder '%s' for subject '%s'", drafts_folder, subject)
        return True
    else:
        logger.error("Failed to append draft to '%s': %s %s", drafts_folder, status, data)
        return False


# ---------------------------------------------------------------------------
# Mark original email as read
# ---------------------------------------------------------------------------

def mark_as_read(conn: imaplib.IMAP4_SSL, uid: str) -> bool:
    """
    Mark the email with the given UID as \\Seen (read) in the currently
    selected folder (INBOX).

    Only called AFTER a draft has been successfully appended.
    """
    status, data = conn.uid("STORE", uid, "+FLAGS", r"(\Seen)")
    if status == "OK":
        logger.info("Marked UID %s as \\Seen", uid)
        return True
    else:
        logger.error("Failed to mark UID %s as read: %s", uid, data)
        return False


# ---------------------------------------------------------------------------
# High-level poll function
# ---------------------------------------------------------------------------

def poll_mailbox(
    imap_host: str,
    imap_port: int,
    username: str,
    password: str,
    source_folder: str,
    drafts_folder: str,
    known_message_ids: set | None = None,
    max_unread_fetch: int = DEFAULT_MAX_UNREAD_FETCH,
) -> tuple[list[dict], imaplib.IMAP4_SSL, dict]:
    """
    Connect, find unread messages not in known_message_ids,
    parse and return them as dicts. Does NOT do AI generation or DB writes.

    Returns a list of parsed message dicts (plus 'conn' for later use).
    The caller is responsible for AI generation, DB saves, and closing conn.

    Each returned dict has keys:
      uid, message_id, from_email, from_name, subject,
      received_at, body_text, _conn (IMAP connection — keep open for appending)
    """
    conn = get_imap_connection(imap_host, imap_port, username, password)
    uids = fetch_unread_uids(
        conn,
        source_folder=source_folder or "INBOX",
        max_unread_fetch=max_unread_fetch,
    )
    results = []
    skipped_known_ids = 0
    skipped_missing_ids = 0

    for uid in reversed(uids):
        raw = fetch_raw_message(conn, uid)
        if not raw:
            continue
        parsed = parse_message(raw, uid)
        msg_id = parsed.get("message_id", "")

        if not msg_id:
            logger.warning("Email with UID %s has no Message-ID, skipping", uid)
            skipped_missing_ids += 1
            continue

        if known_message_ids and msg_id in known_message_ids:
            logger.info("Skipping already-processed Message-ID %s", msg_id)
            skipped_known_ids += 1
            continue

        parsed["_conn"] = conn
        parsed["_drafts_folder"] = drafts_folder
        results.append(parsed)

    if not results:
        close_connection(conn)

    metadata = {
        "selected_unread_count": len(uids),
        "backlog_detected": max_unread_fetch > 0 and len(uids) >= max_unread_fetch,
        "max_unread_fetch": max_unread_fetch,
        "skipped_known_ids": skipped_known_ids,
        "skipped_missing_ids": skipped_missing_ids,
    }
    return results, conn, metadata
