"""
Pipeline Process Manager - zarządzanie procesem pipeline'u i narzędzi.
Niezależny od bibliotek UI menedżer cyklu życia procesu i bezpiecznego zatrzymania.
"""

from collections import deque
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from utils import stop, telemetry
from utils.safe_io import load_json_safe, save_json_atomic

logger = logging.getLogger(__name__)

STATE_LOCK_FILE = Path(__file__).parent / "pipeline_run_state.json"
CHECKPOINT_FILE = Path(__file__).parent / "pipeline_checkpoint.json"
STOP_REQUESTED_MSG = ("Wysłano żądanie zatrzymania. Pipeline zapisze oferty pobrane do tej chwili "
                      "(w dopasowaniu - rozpoczęte oceny) i zakończy pracę.")
STAGE_OF = {"phase0_5": "phase0"}
FEED_EVENTS = {"stage", "pipeline", "source", "source_saved", "throttled", "profile", "prefilter",
               "matching_done"}


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
    ochronę przed wielokrotnym uruchomieniem i bezpieczne zatrzymanie (stop).
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
        self._events = deque(maxlen=200)
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
        self._echo = None
        self._pending_sync = None
        self._telemetry = {"sources": [], "scoring": None, "stages": {}}
        self._init_stages("full")

    @classmethod
    def get_instance(cls):
        with cls._singleton_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _init_stages(self, mode, stage=None):
        stages_def = [
            # Profil z CV (PHASE 0.5) jest częścią etapu 00 - tor ma zostać sześcioetapowy.
            {"id": "phase0", "num": "00", "title": "Porządki i profil z CV"},
            {"id": "phase1", "num": "01", "title": "Pobieranie ofert ze źródeł"},
            {"id": "phase1_5", "num": "1.5", "title": "Normalizacja linków"},
            {"id": "phase2", "num": "02", "title": "Deduplikacja bazy ofert"},
            {"id": "phase2_5", "num": "2.5", "title": "Czyszczenie opisów ofert"},
            {"id": "phase3", "num": "03", "title": "Dopasowanie do CV"},
        ]
        self._stages = []
        now = time.time()
        for s in stages_def:
            if mode == "standalone":
                # Pojedynczy krok (np. same scrapery) biegnie od razu jako swój etap;
                # reszta toru jest pominięta, a nie „czeka”.
                status = "running" if s["id"] == stage else "skipped"
            else:
                status = "skipped" if (mode == "skip_scraping" and s["id"] == "phase1") else "pending"
            self._stages.append({
                **s,
                "status": status,
                "started_at": now if status == "running" else None,
                "finished_at": None,
            })


    def _get_or_create_source(self, name: str, default_state: str = "running") -> dict:
        name = name.strip()
        for s in self._telemetry["sources"]:
            if s["name"] == name:
                return s
        entry = {
            "name": name,
            "state": default_state,
            "found": None,
            "added": None,
            "details_done": None,
            "details_total": None,
            "error": None,
            "health": None,
        }
        self._telemetry["sources"].append(entry)
        return entry

    def _ensure_scoring(self) -> dict:
        if self._telemetry["scoring"] is None:
            self._telemetry["scoring"] = {
                # Etap dopasowania (matching/run.py): odrzucone przez przesiew
                # (łącznie i per powód), do oceny, ocenione, błędy Jev i tempo
                # z linii „Scored X/Y (R/s)”.
                "prefilter_rejected": None,
                "prefilter_reasons": None,
                "to_score": None,
                "scored": 0,
                "errors": 0,
                "rate": None,
                # Czas ostatniego meldunku postępu (epoch s) - od niego liczy się ETA.
                "last_done_at": None,
            }
        return self._telemetry["scoring"]

    def _telemetry_snapshot(self) -> dict:
        scoring = self._telemetry["scoring"]
        return {
            "sources": [dict(src) for src in self._telemetry["sources"]],
            "scoring": dict(scoring) if scoring else None,
            "stages": {k: dict(v) for k, v in self._telemetry["stages"].items()},
        }

    def _apply_stage(self, pipeline_id: str, state: str) -> bool:
        stage_id = STAGE_OF.get(pipeline_id, pipeline_id)
        idx = next((i for i, s in enumerate(self._stages) if s["id"] == stage_id), None)
        if idx is None:
            return False
        stage = self._stages[idx]
        now = time.time()
        changed = False
        if state == "failed":
            if stage["status"] != "failed":
                stage["status"] = "failed"
                stage["finished_at"] = now
                changed = True
            return changed
        if state == "done":
            if pipeline_id in STAGE_OF.values():
                return False
            if stage["status"] == "running":
                stage["status"] = "done"
                stage["finished_at"] = now
                changed = True
            return changed
        for prev in self._stages[:idx]:
            if prev["status"] not in ("skipped", "failed", "done"):
                prev["status"] = "done"
                prev["finished_at"] = now
                changed = True
        if stage["status"] in ("skipped", "failed"):
            return changed
        if state == "cached":
            if stage["status"] != "done":
                stage["status"] = "done"
                changed = True
            return changed
        if stage["status"] != "running":
            # Etap 00 wraca z „done” do pracy, gdy po etapie z checkpointu rusza
            # profil z CV - czas liczy się od tej chwili.
            if stage.get("started_at") is None or stage["status"] == "done":
                stage["started_at"] = now
                stage["finished_at"] = None
            stage["status"] = "running"
            changed = True
        if self._active_stage_idx != idx:
            self._active_stage_idx = idx
            changed = True
        return changed

    def _apply_event(self, ev: dict) -> bool:
        """Zdarzenie z `utils.telemetry`; zwraca True, gdy zmienił się tor etapów albo wynik."""
        kind = ev.get("event")
        if kind in FEED_EVENTS:
            self._events.append(ev)
        if kind == "stage":
            return self._apply_stage(str(ev.get("id") or ""), str(ev.get("state") or ""))
        if kind == "pipeline":
            result = ev.get("result")
            self._pipeline_complete_seen = result == "complete"
            self._pipeline_incomplete_seen = result == "incomplete"
            self._pipeline_stopped_seen = result == "stopped"
            return True

        name = str(ev.get("name") or "").strip()
        if kind == "source" and name:
            state = ev.get("state") or "running"
            src = self._get_or_create_source(name, default_state=state)
            src["state"] = state
            if isinstance(ev.get("found"), int):
                src["found"] = ev["found"]
            if state == "failed":
                src["error"] = (ev.get("error") or "").strip()[:300] or None
        elif kind == "source_saved" and name:
            self._get_or_create_source(name, default_state="done")["added"] = ev.get("added")
        elif kind == "source_details" and name:
            for src in self._telemetry["sources"]:
                if src["name"] == name:
                    src["details_done"] = ev.get("done")
                    src["details_total"] = ev.get("total")
                    break
        elif kind == "source_health" and name:
            src = self._get_or_create_source(name, default_state="done")
            src["health"] = {"verdict": ev.get("verdict"), "detail": (ev.get("detail") or "")[:300]}
        elif kind == "stage_stats" and ev.get("id"):
            self._telemetry["stages"][ev["id"]] = {
                k: v for k, v in ev.items() if k not in ("event", "at", "id")}
        elif kind == "prefilter":
            sc = self._ensure_scoring()
            sc["prefilter_rejected"] = ev.get("rejected")
            sc["prefilter_reasons"] = ev.get("reasons") or {}
            sc["to_score"] = ev.get("to_score")
            self._telemetry["stages"]["phase3"] = {
                "prefilter_rejected": sc["prefilter_rejected"],
                "prefilter_reasons": sc["prefilter_reasons"],
                "to_score": sc["to_score"],
            }
        elif kind == "scored":
            sc = self._ensure_scoring()
            sc["scored"] = ev.get("done") or 0
            sc["to_score"] = ev.get("total")
            sc["rate"] = ev.get("rate")
            sc["last_done_at"] = round(time.time(), 1)
            self._telemetry["stages"].setdefault("phase3", {}).update(
                {"scored": sc["scored"], "total": sc["to_score"], "rate": sc["rate"]})
        elif kind == "jev_error":
            self._ensure_scoring()["errors"] += 1
        elif kind == "matching_done":
            sc = self._ensure_scoring()
            sc["errors"] = ev.get("errors") or 0
            self._telemetry["stages"].setdefault("phase3", {}).update(
                {"scored_now": ev.get("scored_now"), "errors": sc["errors"],
                 "with_percent": ev.get("with_percent")})
        return False

    def _release_finished_process(self):
        """
        Po zakończeniu własnego procesu oddaje widok przebiegowi, który zapisał stan
        z innego procesu (np. uruchomionemu z terminala): plik stanu ma wtedy inny PID.
        """
        proc = self._process
        if proc is None or proc.poll() is None:
            return
        if self._thread is not None and self._thread.is_alive():
            return
        if not STATE_LOCK_FILE.exists():
            return
        data = load_json_safe(STATE_LOCK_FILE, default=None)
        if isinstance(data, dict) and data.get("pid") and data.get("pid") != proc.pid:
            self._process = None
            self._process_create_time = None
            self._cmd = data.get("cmd")
            self._mode = data.get("mode") or "full"

    def is_running(self):
        with self._lock:
            self._release_finished_process()
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

    def start_pipeline(self, mode="full", cmd=None, echo=None, stage=None):
        """`echo`: funkcja dostająca każdą linię wyjścia procesu (np. druk w konsoli).
        `stage`: w trybie „standalone” identyfikator etapu, który ten krok wykonuje."""
        with self._lock:
            if self.is_running():
                active_pid = self._process.pid if (self._process and self._process.poll() is None) else "inny proces"
                return False, f"Pipeline jest już uruchomiony (PID: {active_pid})."
            stop.clear()

            self._mode = mode
            self._cmd = cmd
            self._echo = echo
            self._init_stages(mode, stage)
            self._active_stage_idx = next(
                (i for i, s in enumerate(self._stages) if s["status"] == "running"), 0)
            self._logs.clear()
            self._events.clear()
            self._started_at = time.time()
            self._finished_at = None
            self._telemetry = {"sources": [], "scoring": None, "stages": {}}
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
            return True, f"Uruchomiono proces (PID: {self._process.pid})."

    def stop_pipeline(self, force=False):
        """
        Bezpieczne zatrzymanie procesu pipeline'u na żądanie użytkownika.
        Domyślnie (force=False) zatrzymuje kooperatywnie: flaga stopu działa między etapami,
        a w dopasowaniu po rozpoczętych ocenach, więc wyniki zdążą się zapisać.
        Przy force=True natychmiast kończy drzewo procesów.
        """
        with self._lock:
            if not self.is_running():
                return False, "Pipeline nie jest obecnie uruchomiony."

            if self._process is None:
                disk_state = load_json_safe(STATE_LOCK_FILE, default=None) or {}
                return self._stop_foreign_run(disk_state, force)

            if self._status == "stopping":
                force = True

            self._status = "stopping"
            proc = self._process
            pid = proc.pid if proc else None
            create_time = self._process_create_time
        try:
            stop.request()
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
            stop.clear()
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
            proc = self._process
            if not _terminate_process_tree(pid, create_time) and proc is not None and proc.poll() is None:
                return False, "Nie udało się zakończyć procesu pipeline'u."
            stop.clear()
            with self._lock:
                self._running = False
                self._status = "stopped"
                self._success = False
                if self._active_stage_idx < len(self._stages):
                    if self._stages[self._active_stage_idx]["status"] == "running":
                        self._stages[self._active_stage_idx]["status"] = "pending"
                self._sync_state_to_disk(force=True)
            return True, "Wymuszono natychmiastowe zatrzymanie procesu (rozpoczęte oceny mogły nie zostać zapisane)."

        return True, STOP_REQUESTED_MSG

    def _stop_foreign_run(self, disk_state, force):
        """
        Zatrzymuje przebieg prowadzony przez inny proces (np. uruchomiony z terminala).
        Plik stanu należy do tamtego procesu, więc ten menedżer go nie nadpisuje:
        zostawia flagę stopu, a przy wymuszeniu kończy drzewo procesów.
        Właściciel przebiegu zapisze stan końcowy sam.
        """
        if stop.requested():
            force = True
        try:
            stop.request()
        except Exception as e:
            logger.warning(f"Błąd tworzenia pliku flagi stopu: {e}")
            return False, f"Nie udało się zgłosić zatrzymania: {e}"
        if not force:
            return True, STOP_REQUESTED_MSG

        pid = disk_state.get("pid")
        logger.info(f"Wymuszono natychmiastowe zatrzymanie procesu pipeline'u (PID: {pid}).")
        if not _terminate_process_tree(pid, disk_state.get("create_time")):
            return False, "Nie udało się zakończyć procesu pipeline'u."
        return True, "Wymuszono natychmiastowe zatrzymanie procesu (rozpoczęte oceny mogły nie zostać zapisane)."

    def wait(self):
        """Czeka na koniec przebiegu uruchomionego przez ten menedżer; zwraca kod wyjścia procesu."""
        thread = self._thread
        while thread is not None and thread.is_alive():
            thread.join(0.5)
        return self._exit_code

    def _sync_state_to_disk(self, force=False):
        now = time.time()
        if not force and (now - self._last_disk_sync) < 0.35:
            # Odroczony zapis: inaczej ostatnia linia przed dłuższą ciszą (np. start scrapera
            # albo wolne zapytanie do Jev) nie trafia na dysk, a stamtąd czyta go serwer.
            if self._pending_sync is None:
                self._pending_sync = threading.Timer(0.4, self._flush_pending_sync)
                self._pending_sync.daemon = True
                self._pending_sync.start()
            return
        pending, self._pending_sync = self._pending_sync, None
        if pending is not None:
            pending.cancel()
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
                    "events": list(self._events),
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

    def _flush_pending_sync(self):
        with self._lock:
            self._pending_sync = None
        self._sync_state_to_disk(force=True)

    def _reader_loop(self):
        proc = self._process
        stages = self._stages
        self._pipeline_complete_seen = False
        self._pipeline_incomplete_seen = False
        self._pipeline_stopped_seen = False

        try:
            for raw_line in proc.stdout:
                line = raw_line.rstrip()
                event = telemetry.parse(line)
                if event is None and self._echo:
                    self._echo(raw_line.rstrip("\r\n"))
                if not line:
                    continue

                with self._lock:
                    changed = False
                    if event is None:
                        self._logs.append(line)
                    else:
                        changed = self._apply_event(event)
                    self._sync_state_to_disk(force=changed)
            code = proc.wait()
        except Exception as e:
            code = -1
            with self._lock:
                self._error_message = str(e)
        stop_requested = stop.requested()
        if stop_requested:
            stop.clear()

        with self._lock:
            self._running = False
            self._exit_code = code
            self._finished_at = time.time()
            now = self._finished_at

            any_failed = any(s["status"] == "failed" for s in stages)
            is_standalone = bool(self._cmd and not any("run_final_pipeline" in str(arg) for arg in self._cmd))

            if (self._pipeline_stopped_seen or self._status in ("stopping", "stopped")
                    or (stop_requested and not self._pipeline_complete_seen)):
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
            self._release_finished_process()
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
                    progress_label = f"Zatrzymywanie po bieżącym etapie ({current_title})..."
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
                    "events": list(self._events),
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

                        if is_alive and status == "running" and stop.requested():
                            status = "stopping"

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
                            disk_state["progress_label"] = f"Zatrzymywanie po bieżącym etapie ({current_title})..."
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
                        disk_state.setdefault("events", [])
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
                "events": list(self._events),
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

