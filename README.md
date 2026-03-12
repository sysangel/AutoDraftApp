# Email Draft App

A simple internal MVP that reads incoming emails via IMAP, generates professional reply drafts using OpenAI, and saves those drafts back into your mailbox Drafts folder — so you can review and send them manually from your own email client.

**This app never sends email.** There is no SMTP, no auto-send, and no sending of any kind.

---

## What it does

1. Polls your INBOX via IMAP every 2 minutes
2. Finds unread messages it hasn't seen before
3. Cleans the email body (removes quoted history, signatures)
4. Sends the cleaned body to OpenAI to generate a draft reply
5. Appends the draft to your mailbox Drafts folder via IMAP
6. Marks the original email as read
7. Stores everything in a local SQLite database
8. Shows a simple admin UI at `http://localhost:8000`

---

## Setup

### 1. Clone and create a virtual environment

```bash
git clone <repo>
cd email-draft-app
python3 -m venv venv
source venv/bin/activate       # Linux / macOS
venv\Scripts\activate          # Windows
```

### 2. Install requirements

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` with your values:

| Variable | Description |
|---|---|
| `MAILBOX_EMAIL` | Your full email address |
| `IMAP_HOST` | Your IMAP server hostname (e.g. `mail.yourdomain.com`) |
| `IMAP_PORT` | IMAP SSL port — almost always `993` |
| `IMAP_USERNAME` | Usually the same as your email address |
| `MAILBOX_PASSWORD` | Your email password |
| `DRAFTS_FOLDER` | Mailbox folder name for drafts (see below) |
| `OPENAI_API_KEY` | Your OpenAI API key |
| `OPENAI_MODEL` | OpenAI model to use (default: `gpt-4o-mini`) |

### 4. Bluehost IMAP settings

For Bluehost-hosted accounts:

- **IMAP Host:** `mail.yourdomain.com`
- **IMAP Port:** `993` (SSL)
- **Username:** your full email address
- **Password:** your email password

You can confirm these in Bluehost cPanel → Email Accounts → Connect Devices.

### 5. Run the app

```bash
python -m dotenv run uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

Or if you load .env manually:

```bash
export $(grep -v '^#' .env | xargs)
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` in your browser.

---

## Drafts folder name

Different mail servers use different folder names for Drafts. Common ones:

| Server type | Folder name |
|---|---|
| Bluehost / cPanel default | `Drafts` |
| Some Dovecot configs | `INBOX.Drafts` |
| Some older servers | `Draft` |

**How to find yours:** Open your email client (e.g. Thunderbird, Apple Mail) or webmail (Bluehost Webmail / Roundcube), and check the exact folder name shown for your Drafts folder.

Set it in `.env`:

```
DRAFTS_FOLDER=Drafts
```

If drafts aren't appearing, try `INBOX.Drafts` as the next option.

---

## Duplicate prevention

Every processed email is stored in the SQLite database using its RFC 2822 `Message-ID` header as the unique key. Before processing any email, the app checks whether that `Message-ID` already exists in the database.

- This means even if an email is accidentally marked unread again, it will **never be processed twice**.
- The app does not rely solely on the read/unread flag.
- If you want to reprocess an email, you would need to delete its record from the `messages` table.

---

## Admin UI

| URL | Description |
|---|---|
| `http://localhost:8000/` | Dashboard — recent messages with drafts |
| `http://localhost:8000/messages` | Full message list |
| `http://localhost:8000/messages/{id}` | Detail view for a single message |
| `http://localhost:8000/poll` (POST) | Manually trigger a poll |
| `http://localhost:8000/health` | Health check |

Use the **Poll Now** button on the dashboard to trigger an immediate check instead of waiting for the 2-minute interval.

---

## Why no SMTP / sending?

This is intentional. The app is designed for the case where:

- You want AI-assisted draft generation but need to review before sending
- You don't want to risk accidental sends
- You want to manage sending entirely from your own email client

The app uses IMAP APPEND to place drafts in your mailbox. Your normal email client (Roundcube, Thunderbird, Outlook, Apple Mail, etc.) will display those drafts in your Drafts folder, ready for you to edit and send manually.

---

## Project structure

```
email-draft-app/
├── app.py              # FastAPI routes, scheduler, poll logic
├── db.py               # SQLite engine and session factory
├── models.py           # SQLAlchemy ORM models
├── imap_service.py     # All IMAP operations (read, append, mark read)
├── ai_service.py       # OpenAI draft generation
├── templates/
│   ├── base.html
│   ├── dashboard.html
│   ├── messages.html
│   └── message_detail.html
├── static/
│   └── style.css
├── .env.example        # Copy to .env and fill in
├── requirements.txt
└── README.md
```

---

## Security notes

- Passwords are stored only in environment variables, never in the database
- The database stores a reference key name (e.g. `MAILBOX_PASSWORD`) not the actual password
- **For production:** Replace `.env` password storage with a proper secrets manager (AWS Secrets Manager, HashiCorp Vault, etc.)
- **For production:** Add authentication to the admin UI (currently open to anyone on the network)
- **For production:** Use HTTPS

---

## Troubleshooting

**Drafts not appearing in my email client**
- Check `DRAFTS_FOLDER` in `.env` — try `Drafts`, `INBOX.Drafts`, or `Draft`
- Some clients cache folder lists; try refreshing or restarting your email client
- Check app logs for `append_draft_to_folder` errors

**IMAP login failing**
- Confirm host, port, username, and password in `.env`
- Bluehost may require you to enable IMAP in cPanel → Email Accounts
- Check if your host requires app-specific passwords

**"No active mailboxes configured"**
- Make sure `MAILBOX_EMAIL` and `IMAP_HOST` are set in `.env` before starting the app
- The mailbox is seeded from env vars on first startup

**OpenAI errors**
- Check `OPENAI_API_KEY` is valid and has credits
- Try switching `OPENAI_MODEL` to `gpt-3.5-turbo` for lower cost testing
