"""
Pipeline Process Manager - zarządzanie procesem pipeline'u i narzędzi.
Niezależny od bibliotek UI menedżer cyklu życia procesu, bezpiecznego zatrzymania i wznawiania.
"""

from collections import deque
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

from utils.safe_io import load_json_safe, save_json_atomic
try:
    import ui_theme
except ImportError:
    try:
        _scratch_dir = str(Path(__file__).parent)
        if _scratch_dir not in sys.path:
            sys.path.insert(0, _scratch_dir)
        import ui_theme
    except ImportError:
        ui_theme = None

logger = logging.getLogger(__name__)

STATE_LOCK_FILE = Path(__file__).parent / "pipeline_run_state.json"
STOP_FLAG_FILE = Path(__file__).parent / "pipeline_stop_requested.flag"
CHECKPOINT_FILE = Path(__file__).parent / "pipeline_checkpoint.json"


def _get_process_creation_time(pid):
    """Zwraca unikalny 64-bitowy czas utworzenia procesu (FILETIME) na Windows."""
    if not pid or pid <= 0:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return None
        try:
            class _FILETIME(ctypes.Structure):
                _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]
            creation, exit_t, kernel_t, user_t = _FILETIME(), _FILETIME(), _FILETIME(), _FILETIME()
            if kernel32.GetProcessTimes(h, ctypes.byref(creation), ctypes.byref(exit_t), ctypes.byref(kernel_t), ctypes.byref(user_t)):
                return (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            return None
        finally:
            kernel32.CloseHandle(h)
    except Exception:
        return None


def _is_pid_alive(pid, expected_create_time=None):
    """
    Bezpieczne sprawdzenie czy proces żyje. Jeśli podano expected_create_time,
    weryfikuje tożsamość procesu, chroniąc przed fałszywym wykryciem w przypadku recyclingu PID-ów na Windows.
    """
    if not pid or pid <= 0:
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        h = kernel32.OpenProcess(SYNCHRONIZE, 0, pid)
        if not h:
            return False
        try:
            wait_res = kernel32.WaitForSingleObject(h, 0)
            # WAIT_TIMEOUT (0x102) oznacza, że proces NIE zasygnalizował wyjścia, czyli wciąż żyje.
            # WAIT_OBJECT_0 (0x0) oznacza, że proces zakończył działanie (zasygnalizowany).
            if wait_res != 0x00000102:
                return False
        finally:
            kernel32.CloseHandle(h)
        if expected_create_time is not None:
            actual_time = _get_process_creation_time(pid)
            if actual_time is None or actual_time != expected_create_time:
                return False
        return True
    except Exception:
        return False


def _get_child_pids(parent_pid):
    """Zwraca listę PID-ów potomnych (rekurencyjnie) na Windows."""
    if not parent_pid or parent_pid <= 0:
        return []
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        TH32CS_SNAPPROCESS = 0x00000002
        class PROCESSENTRY32(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_char * 260),
            ]
        h_snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if h_snap == -1:
            return []
        tree = {}
        try:
            pe = PROCESSENTRY32()
            pe.dwSize = ctypes.sizeof(PROCESSENTRY32)
            if kernel32.Process32First(h_snap, ctypes.byref(pe)):
                while True:
                    tree.setdefault(pe.th32ParentProcessID, []).append(pe.th32ProcessID)
                    if not kernel32.Process32Next(h_snap, ctypes.byref(pe)):
                        break
        finally:
            kernel32.CloseHandle(h_snap)

        children = []
        queue = [parent_pid]
        while queue:
            curr = queue.pop(0)
            for child in tree.get(curr, []):
                children.append(child)
                queue.append(child)
        return children
    except Exception:
        return []


def _terminate_process_tree(pid, expected_create_time=None):
    """
    Bezpieczne zakończenie drzewa procesów na Windows z weryfikacją tożsamości.
    NIGDY nie zabija przypadkowego procesu w razie zwolnienia i ponownego przydzielenia PID-u.
    """
    if not pid or pid <= 0:
        return False
    if expected_create_time is not None:
        actual_time = _get_process_creation_time(pid)
        if actual_time is None or actual_time != expected_create_time:
            logger.warning(f"Zaniechano zatrzymania PID {pid}: czas utworzenia nie zgadza się (recykling PID).")
            return False

    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_TERMINATE = 0x0001
        children = _get_child_pids(pid)
        for c_pid in reversed(children):
            try:
                hc = kernel32.OpenProcess(PROCESS_TERMINATE, False, c_pid)
                if hc:
                    kernel32.TerminateProcess(hc, 1)
                    kernel32.CloseHandle(hc)
            except Exception:
                pass
        hp = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
        if hp:
            kernel32.TerminateProcess(hp, 1)
            kernel32.CloseHandle(hp)
            return True
    except Exception as e:
        logger.warning(f"Błąd podczas zatrzymywania drzewa procesów {pid}: {e}")
    return False


class PipelineProcessManager:
    """
    Niezależny od UI menedżer procesu pipeline'u.
    Gwarantuje przetrwanie procesu, bezpieczny reattachment,
    ochronę przed wielokrotnym uruchomieniem, bezpieczne zatrzymanie (stop)
    oraz niezawodne wznawianie (resume) z checkpointów.
    """
    _singleton_lock = threading.RLock()
    _instance = None

    def __init__(self):
        self._process = None
        self._process_create_time = None
        self._cmd = None
        self._thread = None
        self._lock = threading.RLock()
        self._logs = deque(maxlen=300)
        self._mode = "full"
        self._stages = []
        self._running = False
        self._status = "idle"  # idle / running / stopping / stopped / failed / completed
        self._success = None
        self._exit_code = None
        self._error_message = None
        self._active_stage_idx = 0
        self._last_disk_sync = 0.0
        self._pipeline_complete_seen = False
        self._pipeline_incomplete_seen = False
        self._pipeline_stopped_seen = False
        self._started_at = None
        self._finished_at = None
        self._telemetry = {"sources": [], "scoring": None, "stages": {}}
        self._init_stages("full")

    @classmethod
    def get_instance(cls):
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _init_stages(self, mode):
        stages_def = [
            {"id": "phase0", "num": "00", "title": "Archiwizacja starych ofert", "pattern": r"PHASE 0\b"},
            {"id": "phase1", "num": "01", "title": "Pobieranie ofert ze źródeł", "pattern": r"PHASE 1\b(?![\.\d])"},
            {"id": "phase1_5", "num": "1.5", "title": "Normalizacja linków", "pattern": r"PHASE 1\.5\b"},
            {"id": "phase2", "num": "02", "title": "Deduplikacja bazy ofert", "pattern": r"PHASE 2\b(?![\.\d])"},
            {"id": "phase2_5", "num": "2.5", "title": "Czyszczenie opisów ofert", "pattern": r"PHASE 2\.5\b"},
            {"id": "phase3", "num": "03", "title": "Analiza i ocena AI", "pattern": r"PHASE 3\b"},
            {"id": "phase4", "num": "04", "title": "Ewaluacja rankingu", "pattern": r"PHASE 4\b"},
        ]
        self._stages = []
        for s in stages_def:
            status = "skipped" if (mode == "skip_scraping" and s["id"] == "phase1") else "pending"
            self._stages.append({
                **s,
                "status": status,
                "started_at": None,
                "finished_at": None,
            })


    def _get_or_create_source(self, name: str, default_state: str = "running") -> dict:
        name = name.strip()
        for s in self._telemetry["sources"]:
            if s["name"] == name:
                return s
        color = "#8A8F98"
        if ui_theme and hasattr(ui_theme, "source_color"):
            try:
                color = ui_theme.source_color(name)
            except Exception:
                pass
        entry = {
            "name": name,
            "color": color,
            "state": default_state,
            "found": None,
            "added": None,
            "details_done": None,
            "details_total": None,
        }
        self._telemetry["sources"].append(entry)
        return entry

    def _ensure_scoring(self) -> dict:
        if self._telemetry["scoring"] is None:
            key_count = self._telemetry.get("key_count")
            self._telemetry["scoring"] = {
                "total": None,
                "processed": 0,
                "batch": 0,
                "batch_size": None,
                "remaining": None,
                "model": None,
                "key": None,
                "key_count": key_count,
                "cooldowns": {},
                # Tempo oceny: start pierwszej paczki i koniec ostatniej udanej (epoch s).
                "first_batch_at": None,
                "last_done_at": None,
            }
        return self._telemetry["scoring"]

    def _telemetry_snapshot(self) -> dict:
        scoring = self._telemetry["scoring"]
        snapshot = {
            "sources": [dict(src) for src in self._telemetry["sources"]],
            "scoring": None,
            "stages": {k: dict(v) for k, v in self._telemetry["stages"].items()},
            "key_count": self._telemetry.get("key_count"),
        }
        if scoring:
            sc_copy = dict(scoring)
            if isinstance(sc_copy.get("cooldowns"), dict):
                sc_copy["cooldowns"] = dict(sc_copy["cooldowns"])
            snapshot["scoring"] = sc_copy
        return snapshot

    def _parse_telemetry(self, line: str):
        # Źródła ofert (scrapery)
        m = re.search(r"Running\s+(.+?)\s+scraper\.\.\.", line)
        if m:
            src = self._get_or_create_source(m.group(1), default_state="running")
            src["state"] = "running"
            return

        m = re.search(r"[✓\u2713√]\s*(.+?):\s*Successfully scraped\s+(\d+)\s+jobs", line)
        if m:
            src = self._get_or_create_source(m.group(1), default_state="done")
            src["state"] = "done"
            src["found"] = int(m.group(2))
            return

        m = re.search(r"Skipping\s+(.+?)\s*-\s*Already scraped", line)
        if m:
            src = self._get_or_create_source(m.group(1), default_state="skipped")
            src["state"] = "skipped"
            return

        m = re.search(r"[✗\u2717]\s*(.+?):\s*(?:Failed|Thread crashed)\b", line, re.IGNORECASE)
        if m:
            src = self._get_or_create_source(m.group(1), default_state="failed")
            src["state"] = "failed"
            return

        cl = re.sub(r"^.*?-\s*(?:INFO|WARNING|ERROR|DEBUG|CRITICAL)\s*-\s*", "", line).strip()
        m = re.search(r"^([^:\n\r]+?):\s*saved\s+(\d+)\s+new offers", cl, re.IGNORECASE)
        if not m:
            m = re.search(r"(?:^|[-:]\s+)([^:\n\r]+?):\s*saved\s+(\d+)\s+new offers", line, re.IGNORECASE)
        if m:
            src = self._get_or_create_source(m.group(1), default_state="done")
            src["added"] = int(m.group(2))
            return

        # Postęp pobierania opisów: "aplikuj.pl: 1200/3482 descriptions" (ldjson_scraper_base),
        # "OLX: 120/500 opisow" (olx_scraper._melduj), podsumowanie "fetched X/Y descriptions".
        # OLX melduje się krótszą nazwą niż źródło ("OLX Praca"); nieznanych nazw nie zakładamy.
        m = re.search(r"^([^:\n\r]+?):\s*(fetched\s+)?(\d+)/(\d+)\s+(?:descriptions|opisow)\b", cl)
        if m:
            name = m.group(1).strip()
            for src in self._telemetry["sources"]:
                if src["name"] == name or src["name"].startswith(name + " "):
                    total = int(m.group(4))
                    src["details_total"] = total
                    src["details_done"] = total if m.group(2) else int(m.group(3))
                    break
            return

        # Liczby etapów porządkowych (arkusz pipeline'u). Wzorce = treść logów
        # purge_stale_offers / migrate_normalize_links / deduplicate_db / clean_db;
        # zmiana tekstu logu wymaga zmiany wzorca.
        stages = self._telemetry["stages"]
        m = re.search(r"REMOVED \(stale\):\s*(\d+)", line)
        if m:
            stages["phase0"] = {"removed": int(m.group(1))}
            return

        m = re.search(r"jobs_database\.json:\s*(\d+)\s*->\s*(\d+)\s*\(scalono\s+(\d+)\)", line)
        if m:
            stages["phase1_5"] = {"links": int(m.group(2)), "merged": int(m.group(3))}
            return

        m = re.search(r"jobs_database\.json:\s*(\d+)\s*->\s*(\d+)\s*\(removed\s+(\d+)\)", line)
        if m:
            stages["phase2"] = {"removed": int(m.group(3))}
            return

        m = re.search(r"jobs_database\.json:\s*\d+\s+ofert,\s*brak duplikatow", line)
        if m:
            stages["phase2"] = {"removed": 0}
            return

        # clean_db przycina dwa pliki; sumujemy znaki opisów z obu.
        m = re.search(r"\bBefore:\s*([\d,]+)\s+chars", line)
        if m:
            diet = stages.setdefault("phase2_5", {"chars_before": 0, "chars_after": 0})
            diet["chars_before"] += int(m.group(1).replace(",", ""))
            return

        m = re.search(r"\bAfter:\s*([\d,]+)\s+chars", line)
        if m:
            diet = stages.setdefault("phase2_5", {"chars_before": 0, "chars_after": 0})
            diet["chars_after"] += int(m.group(1).replace(",", ""))
            return

        # Analiza i punktacja AI (waterfall)
        m = re.search(r"Processing\s+(\d+)\s+jobs\s*\(Target Batch Size:\s*(\d+)", line)
        if m:
            sc = self._ensure_scoring()
            sc["total"] = int(m.group(1))
            sc["batch_size"] = int(m.group(2))
            return

        m = re.search(r"Nothing left to do", line)
        if m:
            sc = self._ensure_scoring()
            sc["total"] = 0
            sc["remaining"] = 0
            return

        m = re.search(r"Batch\s+(\d+)\s*\(Processing\s+\d+\s+jobs,\s+(\d+)\s+remaining in queue\)", line)
        if m:
            sc = self._ensure_scoring()
            sc["batch"] = int(m.group(1))
            sc["remaining"] = int(m.group(2))
            if sc["first_batch_at"] is None:
                sc["first_batch_at"] = round(time.time(), 1)
            return

        m = re.search(r"Success!\s+Processed\s+(\d+)\s+out of\s+\d+", line)
        if m:
            sc = self._ensure_scoring()
            sc["processed"] += int(m.group(1))
            sc["last_done_at"] = round(time.time(), 1)
            return

        m = re.search(r"Loaded API State:\s*Model\[\d+\]\s*Key\[(\d+)\]", line)
        if m:
            sc = self._ensure_scoring()
            sc["key"] = int(m.group(1))
            return

        m = re.search(r"Model:\s*(.+?)\s*\|\s*Key\[(\d+)\]", line)
        if m:
            sc = self._ensure_scoring()
            sc["model"] = m.group(1).strip()
            sc["key"] = int(m.group(2))
            return

        m = re.search(r"Rate limit hit\.\s*Sleeping\s+(\d+)s", line)
        if m:
            sc = self._ensure_scoring()
            sleep_s = int(m.group(1))
            if sc.get("key") is not None:
                sc["cooldowns"][str(sc["key"])] = round(time.time() + sleep_s, 1)
            return
    def is_running(self):
        with self._lock:
            if self._process is not None:
                poll = self._process.poll()
                if poll is None:
                    return True
                self._running = False
                return False

            if STATE_LOCK_FILE.exists():
                try:
                    data = load_json_safe(STATE_LOCK_FILE, default={})
                    if not data or data.get("exit_code") is not None or data.get("running") is False:
                        return False
                    pid = data.get("pid")
                    create_time = data.get("create_time")
                    if pid and _is_pid_alive(pid, create_time):
                        return True
                except Exception:
                    pass
            return False

    def start_pipeline(self, mode="full", cmd=None, is_resume=False):
        with self._lock:
            if self.is_running():
                active_pid = self._process.pid if (self._process and self._process.poll() is None) else "inny proces"
                return False, f"Pipeline jest już uruchomiony (PID: {active_pid})."
            try:
                if STOP_FLAG_FILE.exists():
                    STOP_FLAG_FILE.unlink()
            except Exception:
                pass

            self._mode = mode
            self._cmd = cmd
            if not is_resume:
                self._init_stages(mode)
                self._active_stage_idx = 0
            self._logs.clear()
            self._started_at = time.time()
            self._finished_at = None
            # Liczba kluczy od startu przebiegu: karta kluczy w arkuszu nie stoi pusta
            # przez cały scraping (telemetria oceny powstaje dopiero przy pierwszej paczce).
            key_count = None
            try:
                from app_services import get_api_keys_info
                info = get_api_keys_info()
                if info and info.get("count", 0) > 0:
                    key_count = int(info["count"])
            except Exception:
                pass
            self._telemetry = {"sources": [], "scoring": None, "stages": {}, "key_count": key_count}
            self._running = True
            self._status = "running"
            self._success = None
            self._exit_code = None
            self._error_message = None
            self._pipeline_complete_seen = False
            self._pipeline_incomplete_seen = False
            self._pipeline_stopped_seen = False

            cwd = str(Path(__file__).parent)
            if cmd is None:
                cmd = [sys.executable, "-u", "run_final_pipeline.py"]
                if mode == "skip_scraping":
                    cmd.append("--skip-scraping")

            try:
                env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PIPELINE_MANAGED": "1"}
                self._process = subprocess.Popen(
                    cmd,
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=env
                )
                self._process_create_time = _get_process_creation_time(self._process.pid)
                try:
                    self._sync_state_to_disk(force=True)
                except Exception as le:
                    logger.warning(f"Błąd zapisu locka: {le}")
            except Exception as e:
                self._running = False
                self._status = "failed"
                self._success = False
                self._finished_at = time.time()
                self._error_message = str(e)
                logger.error(f"Nie udało się uruchomić pipeline: {e}")
                return False, f"Błąd uruchomienia procesu: {e}"

            self._thread = threading.Thread(target=self._reader_loop, daemon=True)
            self._thread.start()
            action_lbl = "Wznowiono" if is_resume else "Uruchomiono"
            return True, f"{action_lbl} proces (PID: {self._process.pid})."

    def stop_pipeline(self, force=False):
        """
        Bezpieczne zatrzymanie procesu pipeline'u na żądanie użytkownika.
        Domyślnie (force=False) zatrzymuje kooperatywnie na granicy paczki,
        czekając na bezpieczny zapis in-flight batcha bez utraty danych i tokenów.
        Przy force=True natychmiast kończy drzewo procesów.
        """
        with self._lock:
            if not self.is_running():
                return False, "Pipeline nie jest obecnie uruchomiony."

            if self._status == "stopping":
                force = True

            self._status = "stopping"
            proc = self._process
            pid = proc.pid if proc else None
            create_time = self._process_create_time
        try:
            STOP_FLAG_FILE.touch()
        except Exception as e:
            logger.warning(f"Błąd tworzenia pliku flagi stopu: {e}")

        self._sync_state_to_disk(force=True)

        stopped_cooperatively = False
        start_wait = time.time()
        while time.time() - start_wait < 1.5:
            if proc and proc.poll() is not None:
                stopped_cooperatively = True
                break
            if not _is_pid_alive(pid, create_time):
                stopped_cooperatively = True
                break
            time.sleep(0.1)

        if stopped_cooperatively:
            try:
                if STOP_FLAG_FILE.exists():
                    STOP_FLAG_FILE.unlink()
            except Exception:
                pass
            with self._lock:
                self._running = False
                self._status = "stopped"
                self._success = False
                if self._active_stage_idx < len(self._stages):
                    if self._stages[self._active_stage_idx]["status"] == "running":
                        self._stages[self._active_stage_idx]["status"] = "pending"
                self._sync_state_to_disk(force=True)
            return True, "Zatrzymano proces pipeline'u."

        if force:
            logger.info(f"Wymuszono natychmiastowe zatrzymanie procesu pipeline'u (PID: {pid}).")
            forced_ok = _terminate_process_tree(pid, create_time)
            try:
                if STOP_FLAG_FILE.exists():
                    STOP_FLAG_FILE.unlink()
            except Exception:
                pass
            with self._lock:
                self._running = False
                self._status = "stopped"
                self._success = False
                if self._active_stage_idx < len(self._stages):
                    if self._stages[self._active_stage_idx]["status"] == "running":
                        self._stages[self._active_stage_idx]["status"] = "pending"
                self._sync_state_to_disk(force=True)
            return True, "Wymuszono natychmiastowe zatrzymanie procesu (trwająca paczka mogła nie zostać zapisana)."

        return True, "Wysłano żądanie zatrzymania. Pipeline dokończy bieżącą paczkę i zapisze wyniki przed wyjściem."

    def resume_pipeline(self):
        """
        Wznawia przerwany lub nieudany pipeline z zachowaniem trybu i checkpointów.
        Zwraca (bool, str).
        """
        with self._lock:
            if self.is_running():
                return False, "Pipeline jest już uruchomiony."

            state = self.get_state()
            if not state.get("can_resume", False):
                return False, "Brak przerwanego pipeline'u do wznowienia."

            cp = load_json_safe(CHECKPOINT_FILE, default={}) or {}
            completed_stages = set(cp.get("completed_stages", []))
            options = cp.get("options", {})

            saved_cmd = self._cmd or state.get("cmd")
            is_standalone = bool(saved_cmd and not any("run_final_pipeline" in str(arg) for arg in saved_cmd))

            mode = self._mode or options.get("mode", "full")
            if options.get("skip_scraping"):
                mode = "skip_scraping"

            if is_standalone:
                cmd = list(saved_cmd)
                action_desc = f"krok: {Path(cmd[-1]).name if len(cmd) > 2 else 'narzędzie standalone'}"
                first_incomplete = action_desc
            else:
                disk_stages_by_id = {s["id"]: s for s in state.get("stages", []) if isinstance(s, dict) and "id" in s}
                for s in self._stages:
                    if s["id"] in completed_stages:
                        s["status"] = "done"
                        if s.get("started_at") is None and s["id"] in disk_stages_by_id:
                            s["started_at"] = disk_stages_by_id[s["id"]].get("started_at")
                        if s.get("finished_at") is None and s["id"] in disk_stages_by_id:
                            s["finished_at"] = disk_stages_by_id[s["id"]].get("finished_at")
                    elif s["status"] != "skipped":
                        s["status"] = "pending"
                        s["started_at"] = None
                        s["finished_at"] = None
                    else:
                        s["started_at"] = None
                        s["finished_at"] = None
                first_incomplete = None
                first_idx = 0
                for idx, s in enumerate(self._stages):
                    if s["status"] not in ("done", "skipped"):
                        first_incomplete = s["title"]
                        first_idx = idx
                        break

                cmd = [sys.executable, "-u", "run_final_pipeline.py", "--resume"]
                if mode == "skip_scraping" or options.get("skip_scraping"):
                    cmd.append("--skip-scraping")
                if options.get("rescore_all"):
                    cmd.append("--rescore-all")
                if options.get("rescore_changed"):
                    cmd.append("--rescore-changed")

                self._active_stage_idx = first_idx

        ok, msg = self.start_pipeline(mode=mode, cmd=cmd, is_resume=True)
        if ok:
            stage_hint = f" od etapu: {first_incomplete}" if first_incomplete else ""
            return True, f"Wznowiono pipeline{stage_hint}."
        return False, msg

    def _sync_state_to_disk(self, force=False):
        now = time.time()
        if not force and (now - self._last_disk_sync) < 0.35:
            return
        self._last_disk_sync = now
        try:
            with self._lock:
                state = {
                    "pid": self._process.pid if self._process else None,
                    "create_time": self._process_create_time,
                    "cmd": self._cmd,
                    "running": self._running,
                    "status": self._status,
                    "mode": self._mode,
                    "started_at": self._started_at,
                    "finished_at": self._finished_at,
                    "telemetry": self._telemetry_snapshot(),
                    "stages": [dict(s) for s in self._stages],
                    "logs": list(self._logs),
                    "current_stage_idx": self._active_stage_idx,
                    "current_stage_title": (self._stages[self._active_stage_idx]["title"]
                                           if self._active_stage_idx < len(self._stages) else ""),
                    "success": self._success,
                    "exit_code": self._exit_code,
                    "error_message": self._error_message,
                    "stopped": self._status == "stopped",
                }
            ok = save_json_atomic(STATE_LOCK_FILE, state, backup=False)
            if not ok:
                logger.warning("Błąd zapisu stanu procesu: save_json_atomic zwrócił False po wyczerpaniu prób")
        except Exception as e:
            logger.warning(f"Błąd zapisu stanu procesu: {e}")

    def _reader_loop(self):
        proc = self._process
        stages = self._stages
        self._pipeline_complete_seen = False
        self._pipeline_incomplete_seen = False
        self._pipeline_stopped_seen = False

        try:
            for raw_line in proc.stdout:
                line = raw_line.rstrip()
                if not line:
                    continue

                with self._lock:
                    self._logs.append(line)
                    self._parse_telemetry(line)

                    now = time.time()
                    stage_changed = False
                    for s in stages:
                        if re.search(s["pattern"], line) and ("failed" in line.lower() or "error" in line.lower()):
                            if s["status"] != "failed":
                                stage_changed = True
                                s["finished_at"] = now
                            s["status"] = "failed"

                    for idx, s in enumerate(stages):
                        if re.search(s["pattern"], line) and "failed" not in line.lower() and "error" not in line.lower():
                            for prev_idx in range(idx):
                                if stages[prev_idx]["status"] not in ("skipped", "failed", "done"):
                                    stages[prev_idx]["status"] = "done"
                                    stages[prev_idx]["finished_at"] = now
                                    stage_changed = True
                            if s["status"] != "skipped" and s["status"] != "failed":
                                if s["status"] != "running":
                                    stage_changed = True
                                    if s.get("started_at") is None:
                                        s["started_at"] = now
                                elif self._active_stage_idx != idx:
                                    stage_changed = True
                                s["status"] = "running"
                                self._active_stage_idx = idx
                            break

                    if "PIPELINE COMPLETE" in line:
                        self._pipeline_complete_seen = True
                    elif "PIPELINE INCOMPLETE" in line:
                        self._pipeline_incomplete_seen = True
                    elif "PIPELINE STOPPED" in line:
                        self._pipeline_stopped_seen = True

                    force_sync = stage_changed or self._pipeline_complete_seen or self._pipeline_incomplete_seen or self._pipeline_stopped_seen
                    self._sync_state_to_disk(force=force_sync)
            code = proc.wait()
        except Exception as e:
            code = -1
            with self._lock:
                self._error_message = str(e)
        try:
            if STOP_FLAG_FILE.exists():
                STOP_FLAG_FILE.unlink()
        except Exception:
            pass

        with self._lock:
            self._running = False
            self._exit_code = code
            self._finished_at = time.time()
            now = self._finished_at

            any_failed = any(s["status"] == "failed" for s in stages)
            is_standalone = bool(self._cmd and not any("run_final_pipeline" in str(arg) for arg in self._cmd))

            if self._pipeline_stopped_seen or self._status in ("stopping", "stopped"):
                self._status = "stopped"
                self._success = False
                if self._active_stage_idx < len(stages) and stages[self._active_stage_idx]["status"] == "running":
                    stages[self._active_stage_idx]["status"] = "pending"
            elif is_standalone:
                if code == 0:
                    self._status = "completed"
                    self._success = True
                    for s in stages:
                        if s["status"] == "running":
                            s["status"] = "done"
                            s["finished_at"] = now
                else:
                    self._status = "failed"
                    self._success = False
                    if self._active_stage_idx < len(stages):
                        stages[self._active_stage_idx]["status"] = "failed"
                        stages[self._active_stage_idx]["finished_at"] = now
            elif code == 0 and self._pipeline_complete_seen and not any_failed and not self._pipeline_incomplete_seen:
                self._status = "completed"
                self._success = True
                for s in stages:
                    if s["status"] not in ("skipped", "failed"):
                        if s["status"] != "done" or s.get("finished_at") is None:
                            s["finished_at"] = now
                        s["status"] = "done"
            else:
                self._status = "failed"
                self._success = False
                if not any_failed and self._active_stage_idx < len(stages):
                    stages[self._active_stage_idx]["status"] = "failed"
                    stages[self._active_stage_idx]["finished_at"] = now
                for s in stages:
                    if s["status"] == "failed" and s.get("finished_at") is None:
                        s["finished_at"] = now
            self._sync_state_to_disk(force=True)

    def get_state(self):
        with self._lock:
            if self._process is not None:
                is_alive = self._process.poll() is None
                if not is_alive and self._status == "running":
                    if self._exit_code is not None:
                        is_standalone = bool(self._cmd and not any("run_final_pipeline" in str(arg) for arg in self._cmd))
                        if self._pipeline_stopped_seen or self._status == "stopping":
                            self._status = "stopped"
                        elif is_standalone:
                            self._status = "completed" if self._exit_code == 0 else "failed"
                        elif self._exit_code == 0 and self._pipeline_complete_seen and not self._pipeline_incomplete_seen:
                            self._status = "completed"
                        else:
                            self._status = "failed"

                current_title = (self._stages[self._active_stage_idx]["title"]
                                 if self._active_stage_idx < len(self._stages) else "")
                total = len(self._stages)
                done_count = sum(1 for s in self._stages if s.get("status") in ("done", "skipped"))
                has_unfinished = any(s.get("status") not in ("done", "skipped") for s in self._stages)
                can_resume = not is_alive and self._status in ("stopped", "failed") and has_unfinished

                if self._status == "completed":
                    progress_percent = 100.0
                    progress_label = f"Ukończono wszystkie etapy ({done_count}/{total})"
                elif self._status == "idle":
                    progress_percent = None
                    progress_label = "Oczekuje na uruchomienie"
                elif self._status == "stopping":
                    progress_percent = round((done_count / total) * 100, 1) if total else None
                    progress_label = f"Zatrzymywanie po zakończeniu bieżącej paczki ({current_title})..."
                elif self._status == "running":
                    progress_percent = round((done_count / total) * 100, 1) if total else None
                    progress_label = f"Etap {self._active_stage_idx + 1} z {total}: {current_title}"
                elif self._status == "stopped":
                    progress_percent = round((done_count / total) * 100, 1) if total else None
                    progress_label = f"Zatrzymano na etapie {self._active_stage_idx + 1}/{total}: {current_title}"
                elif self._status == "failed":
                    progress_percent = round((done_count / total) * 100, 1) if total else None
                    progress_label = f"Błąd na etapie {self._active_stage_idx + 1}/{total}: {current_title}"
                else:
                    progress_percent = round((done_count / total) * 100, 1) if total else None
                    progress_label = current_title

                return {
                    "running": is_alive,
                    "pid": self._process.pid,
                    "mode": self._mode,
                    "cmd": self._cmd,
                    "started_at": self._started_at,
                    "finished_at": self._finished_at,
                    "stages": [dict(s) for s in self._stages],
                    "telemetry": self._telemetry_snapshot(),
                    "logs": list(self._logs),
                    "current_stage_idx": self._active_stage_idx,
                    "current_stage_title": current_title,
                    "success": self._success,
                    "exit_code": self._exit_code,
                    "error_message": self._error_message,
                    "status": self._status,
                    "can_resume": can_resume,
                    "progress_percent": progress_percent,
                    "progress_label": progress_label,
                }

            if STATE_LOCK_FILE.exists():
                try:
                    disk_state = load_json_safe(STATE_LOCK_FILE, default=None)
                    if disk_state:
                        if disk_state.get("exit_code") is not None or disk_state.get("running") is False:
                            is_alive = False
                        else:
                            pid = disk_state.get("pid")
                            create_time = disk_state.get("create_time")
                            is_alive = bool(pid and _is_pid_alive(pid, create_time))
                        disk_state["running"] = is_alive

                        status = disk_state.get("status")
                        if not status:
                            if is_alive:
                                status = "running"
                            elif disk_state.get("success") is True:
                                status = "completed"
                            elif disk_state.get("stopped"):
                                status = "stopped"
                            elif disk_state.get("exit_code") is not None or disk_state.get("success") is False:
                                status = "failed"
                            else:
                                status = "idle"

                        if not is_alive:
                            if status in ("running", "stopping"):
                                status = "stopped" if disk_state.get("stopped") else "failed"
                            if disk_state.get("success") is None:
                                disk_state["success"] = (status == "completed")

                        disk_state["status"] = status
                        stages = disk_state.get("stages", [])
                        total = len(stages)
                        done_count = sum(1 for s in stages if s.get("status") in ("done", "skipped"))
                        has_unfinished = any(s.get("status") not in ("done", "skipped") for s in stages)
                        can_resume = not is_alive and status in ("stopped", "failed") and has_unfinished
                        disk_state["can_resume"] = can_resume

                        current_idx = disk_state.get("current_stage_idx", 0)
                        current_title = disk_state.get("current_stage_title", "")

                        if status == "completed":
                            disk_state["progress_percent"] = 100.0
                            disk_state["progress_label"] = f"Ukończono wszystkie etapy ({done_count}/{total})"
                        elif status == "idle":
                            disk_state["progress_percent"] = None
                            disk_state["progress_label"] = "Oczekuje na uruchomienie"
                        elif status == "stopping":
                            disk_state["progress_percent"] = round((done_count / total) * 100, 1) if total else None
                            disk_state["progress_label"] = f"Zatrzymywanie po zakończeniu bieżącej paczki ({current_title})..."
                        elif status == "running":
                            disk_state["progress_percent"] = round((done_count / total) * 100, 1) if total else None
                            disk_state["progress_label"] = f"Etap {current_idx + 1} z {total}: {current_title}"
                        elif status == "stopped":
                            disk_state["progress_percent"] = round((done_count / total) * 100, 1) if total else None
                            disk_state["progress_label"] = f"Zatrzymano na etapie {current_idx + 1}/{total}: {current_title}"
                        elif status == "failed":
                            disk_state["progress_percent"] = round((done_count / total) * 100, 1) if total else None
                            disk_state["progress_label"] = f"Błąd na etapie {current_idx + 1}/{total}: {current_title}"

                        disk_state.setdefault("started_at", None)
                        disk_state.setdefault("finished_at", None)
                        disk_state.setdefault("telemetry", {"sources": [], "scoring": None, "stages": {}})
                        disk_state["telemetry"].setdefault("stages", {})
                        for s in disk_state.get("stages", []):
                            s.setdefault("started_at", None)
                            s.setdefault("finished_at", None)
                        return disk_state
                except Exception as e:
                    logger.warning(f"Błąd odczytu stanu procesu z dysku: {e}")

            can_resume_fallback = False
            if CHECKPOINT_FILE.exists():
                try:
                    cp_data = load_json_safe(CHECKPOINT_FILE, default={}) or {}
                    completed = set(cp_data.get("completed_stages", []))
                    if any(s["id"] not in completed for s in self._stages if s["status"] != "skipped"):
                        can_resume_fallback = True
                except Exception:
                    pass

            total = len(self._stages)
            current_title = (self._stages[self._active_stage_idx]["title"]
                             if self._active_stage_idx < len(self._stages) else "")
            return {
                "running": False,
                "pid": None,
                "mode": self._mode,
                "cmd": self._cmd,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "stages": [dict(s) for s in self._stages],
                "telemetry": self._telemetry_snapshot(),
                "logs": list(self._logs),
                "current_stage_idx": self._active_stage_idx,
                "current_stage_title": current_title,
                "success": self._success,
                "exit_code": self._exit_code,
                "error_message": self._error_message,
                "status": "idle" if not can_resume_fallback else "stopped",
                "can_resume": can_resume_fallback,
                "progress_percent": None,
                "progress_label": "Oczekuje na uruchomienie" if not can_resume_fallback else "Gotowy do wznowienia",
            }

