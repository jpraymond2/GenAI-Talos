"""Mac agent support paths and binary identity for Full Disk Access prompts."""

from __future__ import annotations

import sys
from pathlib import Path


def support_dir() -> Path:
    """~/Library/Application Support/Recall."""
    return Path.home() / "Library" / "Application Support" / "Recall"


def config_path() -> Path:
    return support_dir() / "config.json"


def state_path() -> Path:
    return support_dir() / "state.json"


def logs_dir() -> Path:
    return Path.home() / "Library" / "Logs" / "Recall"


def dry_run_preview_path() -> Path:
    return support_dir() / "dry-run-preview.json"


def agent_binary_path() -> Path:
    """Python executable that needs Full Disk Access (venv or system)."""
    return Path(sys.executable).resolve()


def chat_db_path() -> Path:
    return Path.home() / "Library" / "Messages" / "chat.db"


def addressbook_sources() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "AddressBook"
        / "Sources"
    )


def ensure_support_dirs() -> None:
    support_dir().mkdir(parents=True, exist_ok=True)
    logs_dir().mkdir(parents=True, exist_ok=True)
