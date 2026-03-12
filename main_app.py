"""
draft.ai — Standalone desktop entry point.
Uses pywebview to display the app in a native Windows window.
On first run, shows the setup wizard before launching the main app.
"""

import os
import sys
import socket
import threading
import time
from pathlib import Path


def _get_app_dir() -> Path:
    """Directory containing app source files (handles PyInstaller frozen state)."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).parent


def _get_data_dir() -> Path:
    """Writable directory for DB, .env, logs. Uses %APPDATA%\\draft.ai when frozen."""
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA") or str(Path.home())
        data_dir = Path(base) / "draft.ai"
    else:
        data_dir = Path(__file__).parent
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def _find_free_port(preferred: int = 8765) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", preferred)) != 0:
            return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = 12.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


def _run_server(app_import: str, port: int):
    import uvicorn
    uvicorn.run(app_import, host="127.0.0.1", port=port, log_level="warning")


class _SetupApi:
    """Exposed to JavaScript in the setup webview window."""
    _window = None  # Set after create_window

    def close_setup(self):
        if self._window:
            self._window.destroy()


if __name__ == "__main__":
    try:
        import webview
    except ImportError:
        # Fallback: open in browser if pywebview not installed
        import webbrowser
        webview = None

    app_dir  = _get_app_dir()
    data_dir = _get_data_dir()

    # PyInstaller: chdir to extracted bundle so relative imports (templates/, static/) work
    os.chdir(app_dir)

    # Tell setup_app where to write .env and DB
    os.environ["DRAFT_AI_DATA_DIR"] = str(data_dir)

    # Point DB to the writable data dir
    db_path = data_dir / "email_drafts.db"
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{db_path}")

    env_path = data_dir / ".env"
    needs_setup = not env_path.exists()

    # ------------------------------------------------------------------
    # First run: show setup wizard
    # ------------------------------------------------------------------
    if needs_setup:
        setup_port = _find_free_port(8764)

        t = threading.Thread(
            target=_run_server,
            args=("setup_app:setup_app", setup_port),
            daemon=True,
        )
        t.start()

        if not _wait_for_server(setup_port):
            print("ERROR: Setup server did not start in time.", file=sys.stderr)
            sys.exit(1)

        if webview:
            api = _SetupApi()
            win = webview.create_window(
                "draft.ai — Setup",
                f"http://127.0.0.1:{setup_port}/setup",
                width=600,
                height=760,
                resizable=False,
                js_api=api,
            )
            api._window = win
            webview.start()
        else:
            import webbrowser as wb
            wb.open(f"http://127.0.0.1:{setup_port}/setup")
            input("Complete setup in the browser, then press Enter to continue...")

        # If user closed without finishing, exit
        if not env_path.exists():
            sys.exit(0)

    # Load .env
    from dotenv import load_dotenv
    load_dotenv(str(env_path), override=False)

    # ------------------------------------------------------------------
    # Main app
    # ------------------------------------------------------------------
    main_port = _find_free_port(8765)

    t = threading.Thread(
        target=_run_server,
        args=("app:app", main_port),
        daemon=True,
    )
    t.start()

    if not _wait_for_server(main_port):
        print("ERROR: App server did not start in time.", file=sys.stderr)
        sys.exit(1)

    if webview:
        win = webview.create_window(
            "draft.ai",
            f"http://127.0.0.1:{main_port}",
            width=1280,
            height=820,
            min_size=(960, 600),
        )
        webview.start()
    else:
        import webbrowser as wb
        wb.open(f"http://127.0.0.1:{main_port}")
        print(f"\n  draft.ai running at http://127.0.0.1:{main_port}")
        print("  Press Ctrl+C to stop.\n")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
