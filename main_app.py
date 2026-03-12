"""
draft.ai — Standalone desktop entry point.

Starts the local FastAPI server, then opens the app in the default browser.
When packaged with PyInstaller, user data (DB, .env) lives in %APPDATA%\draft.ai
so it survives app updates.
"""

import os
import sys
import socket
import threading
import time
import webbrowser


def _get_app_dir():
    """Return the directory containing app source files."""
    if getattr(sys, "frozen", False):
        # PyInstaller extracts files to sys._MEIPASS at runtime
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def _get_data_dir():
    """Return a writable directory for DB, .env, and logs."""
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        data_dir = os.path.join(base, "draft.ai")
    else:
        data_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(data_dir, exist_ok=True)
    return data_dir


def _find_free_port(preferred: int = 8765) -> int:
    """Return `preferred` if it is free, otherwise bind to any free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", preferred)) != 0:
            return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _open_browser(url: str, delay: float = 1.5):
    time.sleep(delay)
    webbrowser.open(url)


if __name__ == "__main__":
    import uvicorn

    app_dir = _get_app_dir()
    data_dir = _get_data_dir()

    # PyInstaller: switch working directory so relative paths (templates/, static/) resolve
    os.chdir(app_dir)

    # Store the SQLite DB in the writable data directory
    db_path = os.path.join(data_dir, "email_drafts.db")
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{db_path}")

    # Load .env from the data directory (user's config lives there after install)
    env_path = os.path.join(data_dir, ".env")
    if os.path.exists(env_path):
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)

    port = _find_free_port(8765)
    url = f"http://127.0.0.1:{port}"

    # Open browser after the server has had a moment to start
    threading.Thread(target=_open_browser, args=(url,), daemon=True).start()

    print(f"\n  draft.ai  —  {url}")
    print(f"  Data folder: {data_dir}")
    print("  Close this window to stop the app.\n")

    uvicorn.run("app:app", host="127.0.0.1", port=port, log_level="warning")
