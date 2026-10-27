"""Install / uninstall LaunchAgent (every 15 minutes)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

from recall.mac_agent.paths import agent_binary_path, logs_dir

_LABEL = "io.recall.mac-agent"
_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"


def plist_path() -> Path:
    return _PLIST


def install() -> Path:
    logs_dir().mkdir(parents=True, exist_ok=True)
    log_path = logs_dir() / "agent.log"
    repo = Path(__file__).resolve().parents[2]
    venv_python = repo / ".venv" / "bin" / "python"
    python = venv_python if venv_python.is_file() else agent_binary_path()
    program = [
        str(python),
        "-m",
        "recall.mac_agent",
        "run",
    ]
    # WorkingDirectory so imports resolve when installed editable via uv.
    plist = dedent(
        f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
          "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">
        <dict>
          <key>Label</key>
          <string>{_LABEL}</string>
          <key>ProgramArguments</key>
          <array>
            <string>{program[0]}</string>
            <string>{program[1]}</string>
            <string>{program[2]}</string>
            <string>{program[3]}</string>
          </array>
          <key>WorkingDirectory</key>
          <string>{repo}</string>
          <key>StartInterval</key>
          <integer>900</integer>
          <key>RunAtLoad</key>
          <true/>
          <key>StandardOutPath</key>
          <string>{log_path}</string>
          <key>StandardErrorPath</key>
          <string>{log_path}</string>
          <key>EnvironmentVariables</key>
          <dict>
            <key>PATH</key>
            <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
          </dict>
        </dict>
        </plist>
        """
    )
    _PLIST.parent.mkdir(parents=True, exist_ok=True)
    _PLIST.write_text(plist, encoding="utf-8")
    subprocess.run(["launchctl", "unload", str(_PLIST)], check=False, capture_output=True)
    subprocess.run(["launchctl", "load", str(_PLIST)], check=True)
    return _PLIST


def uninstall() -> None:
    if _PLIST.is_file():
        subprocess.run(["launchctl", "unload", str(_PLIST)], check=False, capture_output=True)
        _PLIST.unlink(missing_ok=True)


def status_lines() -> list[str]:
    lines = [f"plist: {_PLIST} ({'present' if _PLIST.is_file() else 'missing'})"]
    result = subprocess.run(
        ["launchctl", "list"],
        check=False,
        capture_output=True,
        text=True,
    )
    loaded = _LABEL in (result.stdout or "")
    lines.append(f"launchd: {'loaded' if loaded else 'not loaded'}")
    lines.append(f"python: {agent_binary_path()}")
    lines.append(f"log: {logs_dir() / 'agent.log'}")
    lines.append(f"uid: {os.getuid()}")
    return lines
