"""Full Disk Access check for Messages chat.db."""

from __future__ import annotations

import subprocess
import sys

from recall.mac_agent.paths import agent_binary_path, chat_db_path


def require_darwin() -> None:
    if sys.platform != "darwin":
        print(
            "Recall Mac agent only runs on macOS (darwin). "
            f"Current platform: {sys.platform!r}."
        )
        raise SystemExit(2)


def check_full_disk_access(*, open_settings: bool = True) -> None:
    """Try reading chat.db; on PermissionError print instructions and exit."""
    path = chat_db_path()
    binary = agent_binary_path()
    try:
        with path.open("rb") as handle:
            handle.read(1)
    except FileNotFoundError:
        print(f"Messages database not found at {path}")
        print("Sign in to iMessage on this Mac, or skip iMessage extraction.")
        return
    except PermissionError:
        _print_fda_help(binary=str(binary), db=str(path))
        if open_settings:
            try:
                subprocess.run(
                    [
                        "open",
                        "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
                    ],
                    check=False,
                )
            except OSError:
                pass
        raise SystemExit(1) from None


def _print_fda_help(*, binary: str, db: str) -> None:
    print("Full Disk Access required to read iMessage.")
    print()
    print("Steps:")
    print("  1. System Settings → Privacy & Security → Full Disk Access")
    print("  2. Unlock the padlock if needed")
    print("  3. Click +, then grant access to THIS binary:")
    print(f"       {binary}")
    print("     (If you run via Terminal.app, also enable Terminal.)")
    print("  4. Quit and reopen Terminal / Cursor, then re-run the agent")
    print()
    print(f"Blocked path: {db}")
