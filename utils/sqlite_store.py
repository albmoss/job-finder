from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import threading
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable, Optional

from utils.safe_io import BACKUP_DIR_NAME, REGENERABLE_KEEP

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (key TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
"""

_ready: set[str] = set()
_ready_lock = threading.Lock()


def dumps(record) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class RecordStore:
    def __init__(self, path, legacy_json: Optional[Path] = None,
                 legacy_key: Optional[Callable[[dict], str]] = None):
        self.path = Path(path)
        self.legacy_json = Path(legacy_json) if legacy_json else None
        self.legacy_key = legacy_key
        self.rev: Optional[int] = None
        self._raw: Optional[dict[str, str]] = None

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists()
        conn = sqlite3.connect(self.path, timeout=60)
        conn.execute("PRAGMA busy_timeout = 60000")
        key = str(self.path.resolve())
        with _ready_lock:
            if key not in _ready or not existed:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.executescript(_SCHEMA)
                self._import_legacy(conn)
                _ready.add(key)
        conn.execute("PRAGMA synchronous = NORMAL")
        return conn

    def _import_legacy(self, conn: sqlite3.Connection) -> None:
        if conn.execute("SELECT 1 FROM meta WHERE name = 'rev'").fetchone():
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT 1 FROM meta WHERE name = 'rev'").fetchone():
                conn.rollback()
                return
            source = self.legacy_json if self.legacy_json and self.legacy_json.exists() else None
            imported = 0
            if source:
                with open(source, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    items = data.items()
                else:
                    items = ((self.legacy_key(r), r) for r in data if isinstance(r, dict))
                rows = [(k, dumps(r)) for k, r in items if k]
                conn.executemany(
                    "INSERT INTO records(key, data) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET data = excluded.data", rows)
                imported = len(rows)
            conn.execute("INSERT INTO meta(name, value) VALUES ('rev', 1)")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        if source:
            target_dir = source.parent / BACKUP_DIR_NAME
            target_dir.mkdir(exist_ok=True)
            target = target_dir / f"{source.name}.pre_sqlite.bak"
            shutil.move(str(source), str(target))
            logger.info(f"{source.name}: {imported} records imported into {self.path.name}, "
                        f"original moved to {target}")

    def revision(self) -> int:
        with closing(self._connect()) as conn:
            self.rev = conn.execute("SELECT value FROM meta WHERE name = 'rev'").fetchone()[0]
        return self.rev

    def load(self) -> dict[str, dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT key, data FROM records ORDER BY rowid").fetchall()
            self.rev = conn.execute("SELECT value FROM meta WHERE name = 'rev'").fetchone()[0]
        self._raw = dict(rows)
        return {key: json.loads(text) for key, text in rows}

    def keys(self) -> list[str]:
        with closing(self._connect()) as conn:
            return [k for (k,) in conn.execute("SELECT key FROM records ORDER BY rowid")]

    def count(self) -> int:
        with closing(self._connect()) as conn:
            return conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]

    def _write(self, upserts: list[tuple[str, str]], deletes: list[str]) -> None:
        if not upserts and not deletes:
            return
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if deletes:
                    conn.executemany("DELETE FROM records WHERE key = ?", ((k,) for k in deletes))
                if upserts:
                    conn.executemany(
                        "INSERT INTO records(key, data) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET data = excluded.data", upserts)
                conn.execute("UPDATE meta SET value = value + 1 WHERE name = 'rev'")
                self.rev = conn.execute("SELECT value FROM meta WHERE name = 'rev'").fetchone()[0]
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        if self._raw is not None:
            for key in deletes:
                self._raw.pop(key, None)
            self._raw.update(upserts)

    def put(self, records: dict[str, dict]) -> int:
        upserts = [(k, dumps(r)) for k, r in records.items()]
        self._write(upserts, [])
        return len(upserts)

    def delete(self, keys: Iterable[str]) -> int:
        keys = list(keys)
        self._write([], keys)
        return len(keys)

    def sync(self, records: dict[str, dict]) -> tuple[int, int]:
        if self._raw is None:
            with closing(self._connect()) as conn:
                self._raw = dict(conn.execute("SELECT key, data FROM records"))
        raw = self._raw
        upserts = []
        for key, record in records.items():
            text = dumps(record)
            if raw.get(key) != text:
                upserts.append((key, text))
        deletes = [k for k in raw if k not in records]
        self._write(upserts, deletes)
        return len(upserts), len(deletes)

    def backup(self, keep: int = REGENERABLE_KEEP) -> Optional[Path]:
        if not self.path.exists():
            return None
        bdir = self.path.parent / BACKUP_DIR_NAME
        bdir.mkdir(exist_ok=True)
        now = datetime.now()
        stamp = now.strftime("%Y%m%d_%H%M%S") + f"_{now.microsecond // 1000:03d}"
        target = bdir / f"{self.path.name}.{stamp}.bak"
        with closing(self._connect()) as conn, closing(sqlite3.connect(target)) as dest:
            conn.backup(dest)
        old_backups = sorted(bdir.glob(f"{self.path.name}.*.bak"))
        for old in old_backups[:max(len(old_backups) - keep, 0)]:
            try:
                old.unlink()
            except OSError as e:
                logger.debug(f"Backup rotation failed: {e}")
        return target
