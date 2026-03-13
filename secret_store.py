"""Local secret storage for Draft.

Stores sensitive values outside the plaintext runtime .env file and protects
them with Windows DPAPI when available.
"""

from __future__ import annotations

import base64
import os
from pathlib import Path


_SECRETS_DIRNAME = "secrets"


def _data_dir() -> Path:
    data_dir = os.environ.get("DRAFT_AI_DATA_DIR")
    if data_dir:
        root = Path(data_dir)
    else:
        root = Path(__file__).parent
    root.mkdir(parents=True, exist_ok=True)
    return root


def _secret_path(name: str) -> Path:
    secrets_dir = _data_dir() / _SECRETS_DIRNAME
    secrets_dir.mkdir(parents=True, exist_ok=True)
    return secrets_dir / f"{name}.bin"


def _protect_bytes(value: bytes) -> bytes:
    if os.name == "nt":
        import win32crypt

        return win32crypt.CryptProtectData(value, "Draft secret", None, None, None, 0)
    return base64.b64encode(value)


def _unprotect_bytes(value: bytes) -> bytes:
    if os.name == "nt":
        import win32crypt

        return win32crypt.CryptUnprotectData(value, None, None, None, 0)[1]
    return base64.b64decode(value)


def set_secret(name: str, value: str | None):
    path = _secret_path(name)
    if not value:
        clear_secret(name)
        return
    protected = _protect_bytes(value.encode("utf-8"))
    path.write_bytes(protected)


def get_secret(name: str) -> str | None:
    path = _secret_path(name)
    if not path.exists():
        return None
    try:
        return _unprotect_bytes(path.read_bytes()).decode("utf-8")
    except Exception:
        return None


def clear_secret(name: str):
    path = _secret_path(name)
    if path.exists():
        path.unlink()


def has_secret(name: str) -> bool:
    return _secret_path(name).exists()
