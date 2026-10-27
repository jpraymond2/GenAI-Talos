"""Load/save ~/Library/Application Support/Recall/config.json (chmod 600)."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from recall.mac_agent.paths import config_path, ensure_support_dirs


class ConfigError(RuntimeError):
    """Missing or invalid Mac agent configuration."""


@dataclass
class AgentConfig:
    """Mac agent local config."""

    server_url: str = ""
    device_upload_token: str = ""
    backfill_days: int = 365
    max_group_size: int = 15
    device_id: str = ""
    excluded_contacts: list[str] = field(default_factory=list)
    excluded_chats: list[str] = field(default_factory=list)
    default_region: str = "CA"
    consent_at: str = ""

    def normalized_server_url(self) -> str:
        return self.server_url.rstrip("/")

    def require_server_url(self) -> str:
        """Return server URL or raise a clear error if unset."""
        url = (self.server_url or "").strip()
        if not url:
            raise ConfigError(
                "server_url is not set. Add it to "
                "~/Library/Application Support/Recall/config.json "
                "or export RECALL_SERVER_URL=https://…"
            )
        return url.rstrip("/")


def load_config(*, create_if_missing: bool = True) -> AgentConfig:
    """Load config.json; create a template with a fresh device_id if missing.

    ``RECALL_SERVER_URL`` overrides ``server_url`` from the file when set.
    """
    ensure_support_dirs()
    path = config_path()
    if not path.is_file():
        cfg = AgentConfig(device_id=str(uuid.uuid4()))
        if create_if_missing:
            save_config(cfg)
        return _apply_env_overrides(cfg)
    data = json.loads(path.read_text(encoding="utf-8"))
    return _apply_env_overrides(_from_dict(data))


def save_config(cfg: AgentConfig) -> Path:
    ensure_support_dirs()
    path = config_path()
    payload = asdict(cfg)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def _apply_env_overrides(cfg: AgentConfig) -> AgentConfig:
    env_url = (os.environ.get("RECALL_SERVER_URL") or "").strip()
    if env_url:
        cfg.server_url = env_url
    return cfg


def _from_dict(data: dict[str, Any]) -> AgentConfig:
    return AgentConfig(
        server_url=str(data.get("server_url") or ""),
        device_upload_token=str(data.get("device_upload_token") or ""),
        backfill_days=int(data.get("backfill_days") or 365),
        max_group_size=int(data.get("max_group_size") or 15),
        device_id=str(data.get("device_id") or str(uuid.uuid4())),
        excluded_contacts=[str(x) for x in (data.get("excluded_contacts") or [])],
        excluded_chats=[str(x) for x in (data.get("excluded_chats") or [])],
        default_region=str(data.get("default_region") or "CA"),
        consent_at=str(data.get("consent_at") or ""),
    )
