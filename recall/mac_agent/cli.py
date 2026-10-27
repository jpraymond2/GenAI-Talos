"""CLI for the Mac companion agent."""

from __future__ import annotations

import json
import sys
from typing import Annotated, Optional

import typer

from recall.mac_agent import __version__
from recall.mac_agent.config import load_config, save_config
from recall.mac_agent.fda import check_full_disk_access, require_darwin
from recall.mac_agent.paths import dry_run_preview_path, ensure_support_dirs
from recall.mac_agent.pipeline import payload_dict, run_extract
from recall.mac_agent.state import load_state, save_state

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="Recall Mac agent — extract iMessage/Contacts/WhatsApp and upload.",
)


@app.callback()
def _root() -> None:
    """Mac-only gate for every subcommand."""
    require_darwin()


@app.command("run")
def cmd_run(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Extract and preview locally; do not upload."),
    ] = False,
) -> None:
    """Extract (and upload unless --dry-run)."""
    ensure_support_dirs()
    check_full_disk_access(open_settings=True)
    cfg = load_config()
    state = load_state()

    # First-ever run: cursors are 0 → backfill_days window applies inside extractors.
    result = run_extract(cfg, state)
    for skip in result.skips:
        print(f"skip: {skip}")

    counts = result.counts()
    print("counts:")
    for key, value in counts.items():
        print(f"  {key}: {value}")
    print(
        f"  max_group_size: {cfg.max_group_size} "
        f"(skipped large groups imessage="
        f"{result.skipped_large_groups.get('imessage', 0)} "
        f"whatsapp={result.skipped_large_groups.get('whatsapp', 0)})"
    )
    print(f"  cursors_next: {result.cursors}")

    if dry_run:
        _print_samples(result.imessage, result.whatsapp, result.contacts)
        preview = {
            "agent_version": __version__,
            "device_id": cfg.device_id,
            "counts": counts,
            "cursors": result.cursors,
            "skips": result.skips,
            "sample_imessage": _sample_with_names(result.imessage),
            "sample_whatsapp": _sample_with_names(result.whatsapp),
            "sample_contacts": result.contacts[:5],
            "note": "dry-run: cursors NOT advanced; nothing uploaded",
        }
        path = dry_run_preview_path()
        path.write_text(json.dumps(preview, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"preview written: {path}")
        print("dry-run complete (no upload, cursors unchanged).")
        return

    from recall.mac_agent.upload import UploadError, upload_batches

    if not cfg.device_upload_token:
        print(
            "Missing device_upload_token in "
            "~/Library/Application Support/Recall/config.json"
        )
        raise SystemExit(1)

    payload = payload_dict(
        result, device_id=cfg.device_id, agent_version=__version__
    )
    try:
        response = upload_batches(cfg, payload)
    except UploadError as exc:
        print(f"upload failed: {exc}")
        raise SystemExit(1) from exc

    # Advance cursors only after server confirms.
    state.imessage_last_rowid = int(result.cursors.get("imessage_last_rowid") or 0)
    state.whatsapp_last_zpk = int(result.cursors.get("whatsapp_last_zpk") or 0)
    state.contacts_last_zpk = int(result.cursors.get("contacts_last_zpk") or 0)
    from datetime import datetime, timezone

    state.last_upload_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_state(state)
    print(f"upload ok: {json.dumps(response, ensure_ascii=False)[:500]}")


@app.command("install")
def cmd_install() -> None:
    """Install LaunchAgent (every 15 minutes)."""
    from recall.mac_agent.launchd import install

    check_full_disk_access(open_settings=True)
    path = install()
    print(f"installed: {path}")
    print("Grant Full Disk Access to the Python binary printed above if needed.")


@app.command("uninstall")
def cmd_uninstall() -> None:
    """Remove LaunchAgent."""
    from recall.mac_agent.launchd import uninstall

    uninstall()
    print("uninstalled LaunchAgent")


@app.command("status")
def cmd_status() -> None:
    """Show launchd + config paths."""
    from recall.mac_agent.launchd import status_lines
    from recall.mac_agent.paths import config_path, state_path

    cfg = load_config(create_if_missing=True)
    print(f"version: {__version__}")
    print(f"config: {config_path()}")
    print(f"state: {state_path()}")
    print(f"server_url: {cfg.server_url}")
    print(f"device_id: {cfg.device_id}")
    print(f"token_set: {bool(cfg.device_upload_token)}")
    print(f"backfill_days: {cfg.backfill_days}")
    for line in status_lines():
        print(line)


@app.command("init-config")
def cmd_init_config(
    token: Annotated[Optional[str], typer.Option("--token")] = None,
    server_url: Annotated[
        Optional[str], typer.Option("--server-url")
    ] = None,
) -> None:
    """Create or update config.json (chmod 600)."""
    cfg = load_config()
    if token:
        cfg.device_upload_token = token
    if server_url:
        cfg.server_url = server_url
    path = save_config(cfg)
    print(f"wrote {path}")


def _looks_garbled(text: str) -> bool:
    """Skip attributedBody decode failures that still contain typedstream junk."""
    lower = (text or "").lower()
    return any(
        marker in lower
        for marker in (
            "nsdictionary",
            "nsnumber",
            "nsvalue",
            "__kimmessagepartattributename",
            "nsmutable",
        )
    )


def _sample_with_names(messages: list, *, n: int = 5) -> list:
    """Prefer messages whose handle resolved to a contact card name."""
    clean = [m for m in messages if not _looks_garbled(str(m.get("text") or ""))]
    named = [m for m in clean if m.get("contact_name")]
    if len(named) >= n:
        return named[:n]
    seen = {id(m) for m in named}
    rest = [m for m in clean if id(m) not in seen]
    return (named + rest)[:n]


def _print_samples(
    imessage: list,
    whatsapp: list,
    contacts: list,
) -> None:
    print()
    print("sample iMessage (up to 5) with contact names:")
    sample_im = _sample_with_names(imessage)
    for msg in sample_im:
        name = msg.get("contact_name") or "?"
        handle = msg.get("handle") or ""
        text = (msg.get("text") or "").replace("\n", " ")[:80]
        print(f"  [{msg.get('ts_utc')}] {name} <{handle}>: {text}")
    if not imessage:
        print("  (none)")

    print()
    print("sample WhatsApp (up to 5):")
    sample_wa = _sample_with_names(whatsapp)
    for msg in sample_wa:
        name = msg.get("contact_name") or "?"
        handle = msg.get("handle") or ""
        text = (msg.get("text") or "").replace("\n", " ")[:80]
        print(f"  [{msg.get('ts_utc')}] {name} <{handle}>: {text}")
    if not whatsapp:
        print("  (none)")

    print()
    print("sample contacts (up to 5):")
    for c in contacts[:5]:
        print(
            f"  {c.get('name')} | org={c.get('organization') or '—'} | "
            f"phones={','.join(c.get('phones') or []) or '—'} | "
            f"emails={','.join(c.get('emails') or []) or '—'}"
        )
    if not contacts:
        print("  (none)")


def main(argv: list[str] | None = None) -> None:
    """Entry: ``python -m recall.mac_agent``."""
    # Allow `python -m recall.mac_agent --dry-run` as alias for `run --dry-run`.
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--dry-run"] or (len(args) == 1 and args[0] == "--dry-run"):
        args = ["run", "--dry-run"]
    app(args=args, prog_name="recall.mac_agent")
