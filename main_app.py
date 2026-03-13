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
import traceback
import urllib.error
import urllib.request
from pathlib import Path

ACTIVATION_PORT = 43891
WINDOW_TITLE = "Draft"


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


def _log_launcher(data_dir: Path, message: str):
    """Write launcher diagnostics to the per-run startup trace log."""
    log_path = data_dir / "startup_trace.log"
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(f"[{timestamp}] {message}\n")


def _reset_log(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


def _schedule_log_cleanup(data_dir: Path, delay_seconds: float = 15.0):
    """Delete the startup trace after a successful launch to keep failures prominent."""

    def _cleanup():
        time.sleep(delay_seconds)
        log_path = data_dir / "startup_trace.log"
        try:
            if log_path.exists():
                log_path.unlink()
        except OSError:
            pass

    threading.Thread(target=_cleanup, daemon=True).start()


def _present_window(window, title: str, width: int, height: int):
    """Force the native window into a visible onscreen state."""
    try:
        window.set_title(title)
        window.resize(width, height)
        window.move(80, 60)
        window.restore()
        window.show()
    except Exception:
        pass


class _WindowManager:
    """Tracks the active desktop window so later launches can focus it."""

    def __init__(self, data_dir: Path):
        self._window = None
        self._title = WINDOW_TITLE
        self._size = (1280, 820)
        self._data_dir = data_dir
        self._lock = threading.Lock()

    def attach(self, window, title: str, width: int, height: int):
        with self._lock:
            self._window = window
            self._title = title
            self._size = (width, height)

    def show(self):
        with self._lock:
            window = self._window
            title = self._title
            width, height = self._size
        if window is None:
            return
        _log_launcher(self._data_dir, f"Presenting window '{title}'")
        _present_window(window, title, width, height)


def _signal_existing_instance() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", ACTIVATION_PORT), timeout=1.0) as conn:
            conn.sendall(b"ACTIVATE")
        return True
    except OSError:
        return False


def _set_windows_app_id():
    """Give Windows a stable app identity so the taskbar uses the packaged icon."""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Draft.Desktop")
    except Exception:
        pass


def _start_activation_listener(data_dir: Path, window_manager: _WindowManager):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(("127.0.0.1", ACTIVATION_PORT))
    except OSError:
        server.close()
        return None

    server.listen(5)
    _log_launcher(data_dir, f"Activation listener bound on port {ACTIVATION_PORT}")

    def _serve():
        while True:
            try:
                conn, addr = server.accept()
            except OSError:
                break
            with conn:
                try:
                    payload = conn.recv(64)
                except OSError:
                    payload = b""
                _log_launcher(data_dir, f"Activation request received from {addr[0]} with payload {payload!r}")
                window_manager.show()

    threading.Thread(target=_serve, daemon=True).start()
    return server


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


def _wait_for_http(url: str, timeout: float = 30.0) -> bool:
    """Wait until the app responds successfully over HTTP."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as response:
                if 200 <= response.status < 500:
                    return True
        except urllib.error.URLError:
            pass
        time.sleep(0.15)
    return False


def _resolve_app(app_import: str):
    """Resolve an app import string to the actual ASGI app object."""
    module_name, app_name = app_import.split(":", 1)
    module = __import__(module_name, fromlist=[app_name])
    return getattr(module, app_name)


def _run_server(app_import: str, port: int, data_dir: Path):
    import uvicorn
    try:
        _log_launcher(data_dir, f"Starting server {app_import} on port {port}")
        app_obj = _resolve_app(app_import)
        _log_launcher(data_dir, f"Resolved app import {app_import}")
        uvicorn.run(
            app_obj,
            host="127.0.0.1",
            port=port,
            log_level="info",
            access_log=False,
        )
        _log_launcher(data_dir, f"Server {app_import} on port {port} exited normally")
    except BaseException:
        _log_launcher(data_dir, f"Server {app_import} on port {port} crashed:\n{traceback.format_exc()}")
        raise


def _start_main_server(env_path: Path, data_dir: Path) -> int:
    """Load configuration and start the main FastAPI app."""
    from dotenv import load_dotenv

    _log_launcher(data_dir, f"Loading env from {env_path}")
    load_dotenv(str(env_path), override=True)

    main_port = _find_free_port(8765)
    _log_launcher(data_dir, f"Selected main app port {main_port}")
    t = threading.Thread(
        target=_run_server,
        args=("app:app", main_port, data_dir),
        daemon=True,
    )
    t.start()

    if not _wait_for_http(f"http://127.0.0.1:{main_port}/health"):
        _log_launcher(data_dir, "Main app health check did not succeed before timeout")
        raise RuntimeError("Main app server did not start in time.")

    _log_launcher(data_dir, f"Main app health check succeeded on port {main_port}")
    return main_port


def _start_setup_server(data_dir: Path) -> int:
    """Start the setup/recovery FastAPI app."""
    setup_port = _find_free_port(8764)
    t = threading.Thread(
        target=_run_server,
        args=("setup_app:setup_app", setup_port, data_dir),
        daemon=True,
    )
    t.start()

    if not _wait_for_server(setup_port):
        raise RuntimeError("Setup server did not start in time.")

    _log_launcher(data_dir, f"Setup server ready on port {setup_port}")
    return setup_port


class _DesktopApi:
    """Bridge used by the setup UI to transition into the main app."""

    def __init__(self, env_path: Path, data_dir: Path, window_manager: _WindowManager):
        self._window = None
        self._env_path = env_path
        self._data_dir = data_dir
        self._window_manager = window_manager
        self._launch_lock = threading.Lock()
        self._launched = False

    def attach_window(self, window):
        self._window = window

    def launch_main_app(self):
        with self._launch_lock:
            if self._launched:
                return {"ok": True}

            if not self._env_path.exists():
                _log_launcher(self._data_dir, "launch_main_app called but env file is missing")
                return {"ok": False, "message": "Configuration file was not created."}

            try:
                _log_launcher(self._data_dir, "launch_main_app invoked from setup UI")
                main_port = _start_main_server(self._env_path, self._data_dir)
            except Exception as exc:
                _log_launcher(self._data_dir, f"launch_main_app failed: {exc}")
                return {"ok": False, "message": str(exc)}

            if self._window:
                _log_launcher(self._data_dir, f"Switching setup window to main app on port {main_port}")
                self._window.set_title(WINDOW_TITLE)
                self._window.resize(1280, 820)
                self._window.min_size = (960, 600)
                self._window.load_url(f"http://127.0.0.1:{main_port}")
                self._window.move(80, 60)
                self._window.restore()
                self._window.show()
                self._window_manager.attach(self._window, WINDOW_TITLE, 1280, 820)
                _log_launcher(self._data_dir, "Main app URL loaded into existing webview window")

            self._launched = True
            _schedule_log_cleanup(self._data_dir)
            return {"ok": True}

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
    window_manager = _WindowManager(data_dir)
    _set_windows_app_id()

    if _signal_existing_instance():
        sys.exit(0)

    _reset_log(data_dir / "startup_trace.log")
    _reset_log(data_dir / "app_runtime.log")
    _reset_log(data_dir / "setup_runtime.log")
    _log_launcher(data_dir, "Draft launcher started")

    activation_server = _start_activation_listener(data_dir, window_manager)
    if activation_server is None:
        _log_launcher(data_dir, "Another Draft instance is already running; activation signal sent")
        sys.exit(0)

    # PyInstaller: chdir to extracted bundle so relative imports (templates/, static/) work
    os.chdir(app_dir)
    _log_launcher(data_dir, f"Working directory set to {app_dir}")

    # Tell setup_app where to write .env and DB
    os.environ["DRAFT_AI_DATA_DIR"] = str(data_dir)
    os.environ["DRAFT_AI_STARTUP_LOG"] = str(data_dir / "startup_trace.log")
    os.environ["DRAFT_AI_APP_LOG"] = str(data_dir / "app_runtime.log")
    os.environ["DRAFT_AI_SETUP_LOG"] = str(data_dir / "setup_runtime.log")
    _log_launcher(data_dir, f"Data directory set to {data_dir}")

    # Point DB to the writable data dir
    db_path = data_dir / "email_drafts.db"
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{db_path}")
    _log_launcher(data_dir, f"Database path set to {db_path}")

    env_path = data_dir / ".env"
    needs_setup = not env_path.exists()
    _log_launcher(data_dir, f"Environment file present: {env_path.exists()}")

    # ------------------------------------------------------------------
    # First run: show setup wizard
    # ------------------------------------------------------------------
    if needs_setup:
        try:
            setup_port = _start_setup_server(data_dir)
        except Exception as exc:
            _log_launcher(data_dir, f"Setup startup failed: {exc}")
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)

        if webview:
            api = _DesktopApi(env_path, data_dir, window_manager)
            _log_launcher(data_dir, "Creating setup window")
            win = webview.create_window(
                f"{WINDOW_TITLE} - Setup",
                f"http://127.0.0.1:{setup_port}/setup",
                width=600,
                height=760,
                resizable=False,
                x=80,
                y=60,
                js_api=api,
            )
            api.attach_window(win)
            window_manager.attach(win, f"{WINDOW_TITLE} - Setup", 600, 760)
            _log_launcher(data_dir, "Starting webview event loop for setup window")
            webview.start(_present_window, (win, f"{WINDOW_TITLE} - Setup", 600, 760))
        else:
            import webbrowser as wb
            wb.open(f"http://127.0.0.1:{setup_port}/setup")
            input("Complete setup in the browser, then press Enter to continue...")

        # If user closed without finishing, exit
        if not env_path.exists():
            sys.exit(0)
        sys.exit(0)

    # ------------------------------------------------------------------
    # Main app
    # ------------------------------------------------------------------
    try:
        main_port = _start_main_server(env_path, data_dir)
    except Exception as exc:
        _log_launcher(data_dir, f"Direct main app startup failed: {exc}")
        if webview:
            try:
                setup_port = _start_setup_server(data_dir)
                api = _DesktopApi(env_path, data_dir, window_manager)
                win = webview.create_window(
                    f"{WINDOW_TITLE} - Recovery",
                    f"http://127.0.0.1:{setup_port}/setup?recovery=1&error=main_startup_failed",
                    width=640,
                    height=820,
                    resizable=False,
                    js_api=api,
                )
                api.attach_window(win)
                window_manager.attach(win, f"{WINDOW_TITLE} - Recovery", 640, 820)
                webview.start(_present_window, (win, f"{WINDOW_TITLE} - Recovery", 640, 820))
                sys.exit(0)
            except Exception as recovery_exc:
                _log_launcher(data_dir, f"Recovery UI startup failed: {recovery_exc}")
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if webview:
        _log_launcher(data_dir, f"Creating main app window on port {main_port}")
        win = webview.create_window(
            WINDOW_TITLE,
            f"http://127.0.0.1:{main_port}",
            width=1280,
            height=820,
            min_size=(960, 600),
            x=80,
            y=60,
        )
        window_manager.attach(win, WINDOW_TITLE, 1280, 820)
        _schedule_log_cleanup(data_dir)
        _log_launcher(data_dir, "Starting webview event loop for main app window")
        webview.start(_present_window, (win, WINDOW_TITLE, 1280, 820))
    else:
        import webbrowser as wb
        wb.open(f"http://127.0.0.1:{main_port}")
        print(f"\n  Draft running at http://127.0.0.1:{main_port}")
        print("  Press Ctrl+C to stop.\n")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
