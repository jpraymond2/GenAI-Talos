"""Copy SQLite DB (+ wal/shm) to a temp dir for safe read-only opens."""

from __future__ import annotations

import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


@contextmanager
def copied_sqlite(db_path: Path, *, prefix: str = "recall-mac-") -> Iterator[Path]:
    """Yield a temp copy of ``db_path`` (and sidecars). Caller must not touch originals."""
    with tempfile.TemporaryDirectory(prefix=prefix) as tmp:
        tmp_dir = Path(tmp)
        dest = tmp_dir / db_path.name
        shutil.copy2(db_path, dest)
        for suffix in ("-wal", "-shm"):
            side = Path(str(db_path) + suffix)
            if side.is_file():
                shutil.copy2(side, tmp_dir / side.name)
        # Also try name.wal style used by some AddressBook copies
        for suffix in (".wal", ".shm"):
            side = db_path.with_suffix(db_path.suffix + suffix)
            if side.is_file():
                shutil.copy2(side, tmp_dir / side.name)
        yield dest
