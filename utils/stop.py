"""Flaga kooperatywnego zatrzymania przebiegu: serwer ją zakłada, etapy i scrapery sprawdzają."""

from pathlib import Path

STOP_FLAG_FILE = Path(__file__).resolve().parent.parent / "pipeline_stop_requested.flag"


def requested() -> bool:
    return STOP_FLAG_FILE.exists()


def request() -> None:
    STOP_FLAG_FILE.touch()


def clear() -> None:
    try:
        STOP_FLAG_FILE.unlink(missing_ok=True)
    except OSError:
        pass
