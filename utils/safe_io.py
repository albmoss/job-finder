"""
Bezpieczny zapis JSON: atomowy + rotacyjne backupy.

Powód: user_decisions.json i preference_profile.json to dane, których NIE DA SIĘ
odtworzyć (setki ręcznych ocen). Zapis wprost do pliku oznacza, że przerwanie
procesu w trakcie writeu zostawia obcięty/pusty plik i dane przepadają.
"""

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

BACKUP_DIR_NAME = "backups"
DEFAULT_KEEP = 10

# Głębokość historii zależy od tego, czy plik da się odtworzyć.
# jobs_database.json i analyzed_jobs_waterfall.json odtwarza pipeline, a ważą
# po ~30 MB i są przepisywane przez cztery etapy każdego przebiegu - dziesięć
# pokoleń każdego z nich to ponad pół giga kopii danych, które i tak umie
# wyprodukować `run_final_pipeline.py`. Zostawiamy poprzednią wersję (jest po co
# cofnąć nieudany etap) i na tym koniec. Reszta - z user_decisions.json na czele -
# trzyma pełne dziesięć, bo tych danych nie odtworzy nic.
REGENERABLE_KEEP = 2
_KEEP_BY_NAME = {
    "jobs_database.json": REGENERABLE_KEEP,
    "analyzed_jobs_waterfall.json": REGENERABLE_KEEP,
}


def _backup_dir(filepath: Path) -> Path:
    d = filepath.parent / BACKUP_DIR_NAME
    d.mkdir(exist_ok=True)
    return d


def rotate_backup(filepath, keep: int = None):
    """Skopiuj aktualny plik do backups/ i zostaw tylko `keep` najnowszych kopii."""
    filepath = Path(filepath)
    if not filepath.exists() or filepath.stat().st_size == 0:
        return

    if keep is None:
        keep = _KEEP_BY_NAME.get(filepath.name, DEFAULT_KEEP)

    bdir = _backup_dir(filepath)
    # Milisekundy w stemplu, bo etapy pipeline'u potrafią przepisać ten sam plik
    # dwa razy w tej samej sekundzie - przy samych sekundach druga kopia
    # nadpisywała pierwszą i historia była płytsza, niż deklaruje `keep`.
    now = datetime.now()
    stamp = now.strftime("%Y%m%d_%H%M%S") + f"_{now.microsecond // 1000:03d}"
    target = bdir / f"{filepath.name}.{stamp}.bak"

    try:
        shutil.copy2(filepath, target)
    except Exception as e:
        logger.warning(f"Could not back up {filepath.name}: {e}")
        return

    # Rotacja - usuń najstarsze
    try:
        existing = sorted(bdir.glob(f"{filepath.name}.*.bak"))
        for old in existing[:-keep]:
            old.unlink()
    except Exception as e:
        logger.debug(f"Backup rotation failed: {e}")


def save_json_atomic(filepath, data, backup: bool = False, keep: int = None, indent: int = 2):
    """
    Zapisz JSON atomowo: najpierw .tmp, potem os.replace (operacja atomowa na NTFS/POSIX).
    Dzięki temu docelowy plik nigdy nie jest w stanie częściowo zapisanym.

    backup=True dodatkowo archiwizuje poprzednią wersję przed nadpisaniem.
    """
    filepath = Path(filepath)

    if backup:
        rotate_backup(filepath, keep=keep)

    tmp = filepath.with_suffix(filepath.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, filepath)
        return True
    except Exception as e:
        logger.error(f"Atomic write of {filepath.name} failed: {e}")
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass
        return False


def load_json_safe(filepath, default=None):
    """
    Wczytaj JSON. Jeśli plik jest uszkodzony, spróbuj najnowszego backupu
    zamiast zwracać pustą strukturę (co skasowałoby dane przy kolejnym zapisie).
    """
    filepath = Path(filepath)
    if default is None:
        default = {}

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception as e:
        logger.error(f"{filepath.name} corrupt ({e}) - trying backup...")

    bdir = filepath.parent / BACKUP_DIR_NAME
    if bdir.exists():
        for candidate in sorted(bdir.glob(f"{filepath.name}.*.bak"), reverse=True):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.warning(f"Odtworzono {filepath.name} z backupu {candidate.name}")
                return data
            except Exception:
                continue

    logger.error(f"No usable backup for {filepath.name} - returning default")
    return default
