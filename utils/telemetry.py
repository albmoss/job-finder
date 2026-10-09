"""
Zdarzenia przebiegu dla menedżera procesu (pipeline_manager): jedna linia JSON na stdout
z prefiksem PREFIX. Logi dla człowieka idą osobno i mogą zmieniać treść bez ryzyka.
"""

import json
import sys
import threading
import time

PREFIX = "@@jf "

_write_lock = threading.Lock()


def emit(event: str, **data) -> None:
    line = PREFIX + json.dumps({"event": event, "at": round(time.time(), 1), **data}, ensure_ascii=False)
    with _write_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def parse(line: str):
    if not line.startswith(PREFIX):
        return None
    try:
        payload = json.loads(line[len(PREFIX):])
    except ValueError:
        return None
    if isinstance(payload, dict) and isinstance(payload.get("event"), str):
        return payload
    return None
