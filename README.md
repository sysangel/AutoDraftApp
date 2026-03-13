# Draft

Draft is a Windows desktop app that monitors an IMAP mailbox, generates reply drafts with OpenAI, and saves those drafts back into your mailbox Drafts folder for manual review.

It is designed to reduce inbox admin work without taking away control.

**Draft never sends email.** There is no SMTP auto-send path in the app.

---

## What Draft Does

Draft can:

1. Poll a selected IMAP folder on a schedule or on demand
2. Detect unread emails that have not already been processed
3. Clean message bodies before drafting
4. Categorize emails with AI
5. Generate context-aware reply drafts
6. Append those drafts to your IMAP Drafts folder
7. Mark the original message as read
8. Store lightweight local context in SQLite to improve future drafts
9. Let you review messages, feedback, insights, and configuration in a desktop UI

---

## Desktop App

Draft now runs as a packaged Windows desktop application rather than requiring you to manually use a browser.

Desktop behavior:

- Native Windows installer
- First-run setup flow
- Main app opens in its own window
- Single-app behavior for normal launches
- Optional launch on login via installer task
- Local FastAPI server still powers the UI under the hood

While the app is running, polling is active. If the app is fully closed, polling stops.

---

## Core Guardrails

- Draft only creates drafts
- Draft never sends mail
- Sensitive context is kept tightly bounded
- Local summaries stay on your machine
- Strict privacy mode can further reduce what is included in AI prompts

Important note:

- OpenAI API usage is still subject to your account or organization data controls
- The app minimizes sent content, but app code alone cannot guarantee a universal zero-retention policy

---

## Main Features

### Inbox Processing

- Scheduled polling
- Background-safe manual polling
- Duplicate prevention using `Message-ID`
- Configurable source folder beyond just `INBOX`

### Draft Generation

- Reply length controls
- Tone controls
- Hard rule guidance
- Business context guidance
- Category-specific prompting
- Thread-aware context using bounded local summaries

### Categorization

- AI-based categorization instead of simple keyword rules
- Review Queue for lower-confidence categorizations
- Manual category correction flow

### Feedback Loop

- Thumbs up feedback
- Refine-note feedback
- Client preference metadata saved locally
- Future drafts can incorporate learned client-specific guidance

### Insights

- Top contacts
- Top domains
- Lightweight sender and thread summaries
- Transparency into what context has been stored locally

### Recovery / Troubleshooting

- Health page
- Reset App Data button in-app
- First-run recovery flow
- Startup logging and installer flow improvements

---

## Setup Options

## Option 1: Install the Windows App

Use the generated installer:

- `installer_output\Draft_Setup.exe`

After installing:

1. Launch `Draft`
2. Complete setup if no saved app data exists
3. Configure mailbox and OpenAI settings
4. Poll your mailbox from the desktop app

## Option 2: Run From Source

### 1. Create a virtual environment

```bash
python -m venv venv
venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

Copy `.env.example` to `.env` and provide values for your mailbox and OpenAI credentials.

### 4. Run the app

```bash
python -m dotenv run uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

The web app will be available at `http://localhost:8000`.

---

## Configuration

Draft supports:

- IMAP host
- IMAP port
- IMAP username
- mailbox password
- source folder
- drafts folder
- OpenAI API key
- OpenAI model
- polling enabled toggle
- background mode preference

The installed desktop app stores runtime state in:

- `%APPDATA%\draft.ai\`

That folder is used for:

- `.env`
- SQLite database
- runtime logs

---

## Provider Presets

Draft includes preset helpers for:

- Bluehost
- Gmail
- Outlook / Microsoft 365
- Yahoo
- Zoho
- Custom

These presets can auto-fill common IMAP defaults, but you can still fully customize the configuration.

---

## Important Environment Variables

| Variable | Description |
|---|---|
| `MAILBOX_EMAIL` | Mailbox email address |
| `IMAP_HOST` | IMAP server hostname |
| `IMAP_PORT` | IMAP SSL port, usually `993` |
| `IMAP_USERNAME` | IMAP username |
| `MAILBOX_PASSWORD` | Mailbox password |
| `DRAFTS_FOLDER` | IMAP drafts folder name |
| `SOURCE_FOLDER` | IMAP folder Draft should poll |
| `OPENAI_API_KEY` | OpenAI API key |
| `OPENAI_MODEL` | OpenAI model name |

---

## Drafts Folder Notes

Common Drafts folder names:

| Server type | Folder name |
|---|---|
| Bluehost / cPanel default | `Drafts` |
| Some Dovecot configs | `INBOX.Drafts` |
| Some older servers | `Draft` |
| Gmail | `[Gmail]/Drafts` |

If drafts are not appearing, verify the exact folder name used by your email provider.

---

## Duplicate Prevention

Draft stores processed messages locally using the RFC 2822 `Message-ID` as a unique key.

That means:

- messages are not processed twice just because they become unread again
- reprocessing requires clearing the stored record

---

## Data Stored Locally

Draft uses a local SQLite database to store useful but bounded metadata, including:

- processed message records
- generated drafts
- categorization status
- thread summaries
- sender insights
- domain insights
- client preference metadata
- feedback and refinement notes

Draft is intentionally not a full CRM sync engine. It stores a constrained local knowledge layer to improve drafting quality without becoming overly invasive.

---

## Privacy Model

Draft is built to keep data collection tightly controlled:

- no attachments are broadly ingested
- prompts are bounded
- local summaries are compact
- strict privacy mode can minimize AI prompt context further
- insights are intentionally lightweight, not deep profile records

You can also reset local app state from inside the app.

---

## Main UI Areas

| Route | Purpose |
|---|---|
| `/` | Dashboard |
| `/messages` | All processed messages |
| `/messages/{id}` | Message detail |
| `/review` | Low-confidence categorization review queue |
| `/insights` | Local contact/domain insights |
| `/settings` | Prompt and privacy settings |
| `/configuration` | Mailbox and runtime configuration |
| `/health/view` | Human-friendly health page |
| `/health` | Machine-readable health endpoint |

---

## Build and Packaging

Windows packaging uses:

- PyInstaller
- Inno Setup

Build script:

```bat
build_installer.bat
```

Output:

- `dist\Draft\Draft.exe`
- `installer_output\Draft_Setup.exe`

For distribution, share the installer, not just the raw EXE.

---

## Project Structure

```text
EmailDraftApp/
├── app.py
├── main_app.py
├── setup_app.py
├── ai_service.py
├── imap_service.py
├── db.py
├── models.py
├── provider_presets.py
├── build_installer.bat
├── draft_ai_installer.iss
├── templates/
├── static/
│   ├── style.css
│   └── brand/
├── tools/
└── README.md
```

---

## Troubleshooting

### The setup opens but the main app does not

- Use the in-app reset flow if stale app data is present
- Check `%APPDATA%\draft.ai\`
- Relaunch after clearing runtime state if needed

### The app skips setup on a fresh reinstall

Uninstalling the app does not always remove `%APPDATA%\draft.ai\`.

If `.env` still exists there, Draft treats the install as an existing configured user.

### Polling only works while the app is open

This is expected in the current desktop model. The app must be running for polling to continue.

### The taskbar icon does not update

Windows caches icons aggressively.

Try:

1. Uninstall or reinstall the latest build
2. Launch the updated app
3. Re-pin it if necessary

### Drafts are not appearing

- Verify `DRAFTS_FOLDER`
- Confirm the selected IMAP Drafts folder name
- Test alternate names such as `Drafts`, `INBOX.Drafts`, or `[Gmail]/Drafts`

### IMAP login fails

- Verify host, port, username, and password
- Confirm IMAP access is enabled with your provider
- Some providers require app passwords

---

## Security Notes

- Mailbox passwords are not stored in the SQLite database
- Runtime configuration is stored locally for the desktop app
- For stronger deployments, replace plain local secret storage with a proper secret manager
- If exposed beyond a local trusted machine, add authentication and HTTPS

