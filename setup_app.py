"""
draft.ai — First-run setup wizard backend.
Serves the setup UI and handles connection testing + config saving.
"""

import imaplib
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from provider_presets import PROVIDER_PRESETS
from secret_store import set_secret

_HERE = Path(__file__).parent

setup_app = FastAPI()
setup_app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")
_templates = Jinja2Templates(directory=str(_HERE / "templates"))


def _append_startup_trace(message: str):
    trace_path = os.environ.get("DRAFT_AI_STARTUP_LOG")
    if not trace_path:
        return
    with Path(trace_path).open("a", encoding="utf-8") as fh:
        fh.write(f"[setup] {message}\n")


def _configure_logging():
    handlers = [logging.StreamHandler()]
    setup_log_path = os.environ.get("DRAFT_AI_SETUP_LOG")
    if setup_log_path:
        Path(setup_log_path).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(setup_log_path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


_configure_logging()
logger = logging.getLogger(__name__)
_append_startup_trace("setup_app.py imported")


def _data_dir() -> Path:
    env_val = os.environ.get("DRAFT_AI_DATA_DIR")
    if env_val:
        return Path(env_val)
    return _HERE


@setup_app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request):
    logger.info("Setup page requested.")
    _append_startup_trace("setup page requested")
    return _templates.TemplateResponse("setup.html", {"request": request, "provider_presets": PROVIDER_PRESETS})


@setup_app.post("/setup/test-imap")
async def test_imap(request: Request):
    data = await request.json()
    host = data.get("imap_host", "").strip()
    port = int(data.get("imap_port", 993))
    username = data.get("email", "").strip()
    password = data.get("password", "")
    if not host or not username or not password:
        return {"ok": False, "message": "Please fill in all fields first."}
    try:
        logger.info("Testing IMAP connection to %s:%s for %s", host, port, username)
        conn = imaplib.IMAP4_SSL(host, port)
        conn.login(username, password)
        conn.logout()
        _append_startup_trace(f"imap test succeeded for {username}")
        return {"ok": True, "message": "Connection successful!"}
    except imaplib.IMAP4.error as e:
        return {"ok": False, "message": f"Login failed: {e}"}
    except Exception as e:
        _append_startup_trace(f"imap test failed: {e}")
        return {"ok": False, "message": "Could not connect to the IMAP server. Check the host, port, and credentials."}


@setup_app.post("/setup/test-openai")
async def test_openai_key(request: Request):
    data = await request.json()
    api_key = data.get("api_key", "").strip()
    if not api_key:
        return {"ok": False, "message": "Please enter your API key first."}
    try:
        import openai
        import httpx
        from openai import OpenAI, AuthenticationError
        logger.info("Testing OpenAI key with openai=%s httpx=%s", getattr(openai, "__version__", "unknown"), getattr(httpx, "__version__", "unknown"))
        _append_startup_trace(
            f"openai test using openai={getattr(openai, '__version__', 'unknown')} httpx={getattr(httpx, '__version__', 'unknown')}"
        )
        client = OpenAI(api_key=api_key)
        client.models.list()
        logger.info("OpenAI API key verified.")
        _append_startup_trace("openai key test succeeded")
        return {"ok": True, "message": "API key verified!"}
    except Exception as e:
        msg = str(e)
        try:
            import openai
            import httpx
            _append_startup_trace(
                f"openai key test env openai={getattr(openai, '__version__', 'unknown')} httpx={getattr(httpx, '__version__', 'unknown')}"
            )
        except Exception:
            pass
        _append_startup_trace(f"openai key test failed: {msg}")
        if "401" in msg or "invalid_api_key" in msg or "Incorrect API key" in msg:
            return {"ok": False, "message": "Invalid API key — check platform.openai.com/api-keys."}
        return {"ok": False, "message": "Could not verify the OpenAI API key right now. Check the key and your connection, then try again."}


@setup_app.post("/setup/save")
async def save_config(request: Request):
    data = await request.json()
    dd = _data_dir()
    dd.mkdir(parents=True, exist_ok=True)
    db_path = dd / "email_drafts.db"
    env_path = dd / ".env"
    os.environ["DRAFT_AI_DATA_DIR"] = str(dd)

    set_secret("mailbox_password", data.get("password", ""))
    set_secret("openai_api_key", data.get("openai_key", ""))

    lines = [
        "# draft.ai Configuration — generated by setup wizard",
        "",
        "# Mailbox",
        f"MAIL_PROVIDER={data.get('provider', 'custom')}",
        f"MAILBOX_EMAIL={data.get('email', '')}",
        f"IMAP_HOST={data.get('imap_host', '')}",
        f"IMAP_PORT={data.get('imap_port', '993')}",
        f"IMAP_USERNAME={data.get('email', '')}",
        "SOURCE_FOLDER=INBOX",
        f"DRAFTS_FOLDER={data.get('drafts_folder', 'Drafts')}",
        "POLLING_ENABLED=1",
        "RUN_IN_BACKGROUND=0",
        "",
        "# OpenAI",
        f"OPENAI_MODEL={data.get('openai_model', 'gpt-4o-mini')}",
        "",
        "# Database",
        f"DATABASE_URL=sqlite:///{db_path}",
    ]
    env_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Configuration saved to %s", env_path)
    _append_startup_trace(f"configuration saved to {env_path}")
    return {"ok": True}


@setup_app.post("/setup/reset")
async def reset_runtime_state():
    dd = _data_dir()
    env_path = dd / ".env"
    db_path = dd / "email_drafts.db"

    if env_path.exists():
        env_path.unlink()
    if db_path.exists():
        db_path.unlink()
    set_secret("mailbox_password", None)
    set_secret("openai_api_key", None)

    logger.info("Runtime state reset in %s", dd)
    _append_startup_trace(f"runtime state reset in {dd}")
    return {"ok": True}
