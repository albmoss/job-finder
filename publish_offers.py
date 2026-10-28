import json
import os
import shlex
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

import config
from matching.quick import build_index
from utils.data_models import ScraperStatusManager

KEEP = 2


def _write_meta(target: Path) -> None:
    with closing(sqlite3.connect(target)) as conn, conn:
        sources = [
            {"source": source or "", "offers": count, "last_seen": last_seen or ""}
            for source, count, last_seen in conn.execute(
                "SELECT json_extract(data, '$.source') AS s, COUNT(*), "
                "MAX(COALESCE(json_extract(data, '$.last_seen'), json_extract(data, '$.scraped_at'))) "
                "FROM records GROUP BY s ORDER BY COUNT(*) DESC")
        ]
        meta = {
            "published_at": datetime.now().isoformat(timespec="seconds"),
            "sources": sources,
            "scrapers": ScraperStatusManager().load_status(),
        }
        conn.execute("DROP TABLE IF EXISTS publish_meta")
        conn.execute("CREATE TABLE publish_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.executemany("INSERT INTO publish_meta VALUES (?, ?)",
                         [(k, json.dumps(v, ensure_ascii=False)) for k, v in meta.items()])


def snapshot(target: Path) -> int:
    with closing(sqlite3.connect(config.JOBS_DATABASE_PATH, timeout=60)) as conn:
        conn.execute("VACUUM INTO ?", (str(target),))
    with closing(sqlite3.connect(target)) as conn:
        conn.execute("PRAGMA journal_mode = DELETE")
    count = build_index(target)
    _write_meta(target)
    return count


def publish_local(directory: Path) -> int:
    name = f"offers-{datetime.now():%Y%m%d-%H%M%S}.db"
    part = directory / f"{name}.part"
    count = snapshot(part)
    part.chmod(0o660)
    part.rename(directory / name)
    for old in sorted(directory.glob("offers-*.db"))[:-KEEP]:
        for path in (old, old.with_name(old.name + "-wal"), old.with_name(old.name + "-shm")):
            path.unlink(missing_ok=True)
    print(f"Opublikowano {name}: {count} ofert")
    return 0


def main(argv: list[str] | None = None) -> int:
    from utils.console import force_utf8
    force_utf8()
    args = sys.argv[1:] if argv is None else argv
    destination = (args[0] if args else os.environ.get("PUBLIC_DEPLOY_TARGET", "")).strip()
    if destination and Path(destination).is_dir():
        return publish_local(Path(destination))
    if ":" not in destination:
        print("Podaj cel: katalog ofert albo user@host:/katalog/ofert (albo PUBLIC_DEPLOY_TARGET w .env)")
        return 2
    host, remote_dir = destination.split(":", 1)
    name = f"offers-{datetime.now():%Y%m%d-%H%M%S}.db"
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / name
        count = snapshot(local)
        print(f"Snapshot: {count} ofert, {local.stat().st_size / 1e6:.1f} MB")
        subprocess.run(["ssh", host, f"mkdir -p {shlex.quote(remote_dir)}"], check=True)
        subprocess.run(["scp", "-q", str(local), f"{host}:{remote_dir}/{name}.part"], check=True)
    quoted = shlex.quote(remote_dir)
    subprocess.run(["ssh", host, (
        f"cd {quoted} && mv {name}.part {name} && "
        f"ls -1 offers-*.db | sort | head -n -{KEEP} | sed 'p;s/$/-wal/p;s/-wal$/-shm/' | xargs -r rm -f"
    )], check=True)
    print(f"Opublikowano {name} na {host}:{remote_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
