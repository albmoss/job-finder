"""
Job Finder - Backend HTTP Server (Starlette + Uvicorn)
Wystawia API REST dla frontendu React/TypeScript, zarządza procesami, danymi i bezpieczeństwem.
"""

from datetime import datetime
import logging
import math
import os
from pathlib import Path
import re
import sys
from typing import List
from urllib.parse import urlparse

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from app_services import (
    WS_ALL,
    WS_STAGE_NAMES,
    WS_TAB_HINT,
    WS_TABS,
    WS_TOOLS,
    check_pipeline_prerequisites,
    fetch_job_from_link,
    get_api_keys_info,
    get_cv_info,
    get_env_fields_status,
    job_data_service,
    open_local_cv_pdf,
    save_env_keys,
    save_manual_job,
    save_pasted_cv_text,
    save_llm_settings,
    save_uploaded_cv,
)
from pipeline_manager import PipelineProcessManager
import ui_theme

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("server")

BASE_DIR = Path(__file__).parent
FRONTEND_DIST_DIR = BASE_DIR / "frontend" / "dist"

PAGE_SIZE = 25


def sanitize_log_line(line: str) -> str:
    if not line:
        return ""
    line = re.sub(r"AIza[0-9A-Za-z\-_]{4,}", "[KLUCZ_UKRYTY]", line)
    line = re.sub(r"Key\[\d+\]\s+[A-Za-z0-9\._\-]+", "Key[UKRYTY]", line)
    return line


def sanitize_logs(logs: List[str]) -> List[str]:
    return [sanitize_log_line(line) for line in logs]


# --- Middleware: Ochrona przed atakami typu Cross-Origin Mutation ---

async def origin_check_middleware(request: Request, call_next):
    """
    Wymusza wiązanie lokalne oraz chroni przed złośliwymi mutacjami z obcych domen (CSRF).
    Dla zapytań mutujących (POST, PUT, DELETE, PATCH) weryfikuje nagłówek Origin / Referer.
    """
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        source = origin or referer
        if source:
            parsed = urlparse(source)
            hostname = parsed.hostname or ""
            if hostname not in ("localhost", "127.0.0.1", "::1", "testserver"):
                logger.warning(f"Zablokowano zapytanie cross-origin: {source}")
                return JSONResponse(
                    {"error": "Forbidden: cross-origin mutation rejected"},
                    status_code=403,
                )
    response = await call_next(request)
    return response


# --- Endpointy API ---

async def api_bootstrap(request: Request) -> JSONResponse:
    """Zwraca metadane początkowe, tokeny motywu, status pipeline'u i statystyki bazy."""
    job_data_service.ensure_loaded()
    stats = job_data_service.get_stats()

    mgr = PipelineProcessManager.get_instance()
    p_state = mgr.get_state()
    p_state["logs"] = sanitize_logs(p_state.get("logs", []))

    return JSONResponse({
        "stats": stats,
        "tabs": WS_TABS,
        "tools": WS_TOOLS,
        "all_views": WS_ALL,
        "tab_hints": WS_TAB_HINT,
        "stages": WS_STAGE_NAMES,
        "source_colors": ui_theme.SOURCES,
        "source_fallback_color": ui_theme.SOURCE_FALLBACK,
        "state_colors": ui_theme.STATES,
        "pipeline": p_state,
    })


async def api_stats(request: Request) -> JSONResponse:
    job_data_service.ensure_loaded()
    return JSONResponse(job_data_service.get_stats())


async def api_offers(request: Request) -> JSONResponse:
    """Zwraca stronicowaną listę ofert dla aktywnej zakładki i zapytania wyszukiwania."""
    tab = request.query_params.get("tab", "Dopasowane")
    search = request.query_params.get("search", "").strip()
    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except ValueError:
        page = 1
    try:
        page_size = max(1, min(100, int(request.query_params.get("page_size", PAGE_SIZE))))
    except ValueError:
        page_size = PAGE_SIZE
    # Oferty z danym brakiem („Pokaż N ofert” w panelu braków): zastępuje zakładkę.
    gap = request.query_params.get("gap", "").strip() or None
    try:
        gap_threshold = max(30, min(80, int(request.query_params.get("gap_threshold", 50))))
    except ValueError:
        gap_threshold = 50

    job_data_service.ensure_loaded()
    items = job_data_service.ws_collect(tab, search, gap=gap, gap_threshold=gap_threshold)
    total = len(items)
    total_pages = max(1, math.ceil(total / page_size)) if total else 1
    page = min(page, total_pages)

    start = (page - 1) * page_size
    slice_items = items[start : start + page_size]

    # Wartość sortowania na początku każdej strony — UI podpisuje nią skok o wiele stron.
    # Tylko tam, gdzie lista jest ułożona po tej wartości; reszta zakładek idzie od najnowszych.
    if gap or tab in ("Dopasowane", "Wszystkie"):
        page_marks = [
            int(m.match_percentage) if m else None
            for _, m, _, _ in items[::page_size]
        ]
    elif tab == "Ocenione":
        page_marks = [r for _, _, _, r in items[::page_size]]
    else:
        page_marks = None

    # Nowe = pierwszy raz zobaczone od startu ostatniego pobierania. Oba znaczniki to
    # isoformat czasu lokalnego, więc porównanie napisów = porównanie chwil.
    fresh_since = job_data_service.fresh_since()
    fresh_count = (
        sum(1 for job, _, _, _ in items if (job.scraped_at or "") >= fresh_since) if fresh_since else 0
    )

    rows = []
    for idx, (job, match, status, rating) in enumerate(slice_items, start=start):
        pct = int(match.match_percentage) if match else None
        dot_color, dot_label = ui_theme.STATES.get(status, (None, None)) if status in ui_theme.STATES else (None, None)
        if status in ("apply", "save", "aspirational", "reject", "rated"):
            from app_services import DECISION_STYLE
            dot_color, dot_label = DECISION_STYLE.get(status, (None, None))
            if status == "rated" and rating:
                dot_label = f"ocena {rating}/10"

        decided_at = job_data_service.get_decision(job.link)[3] if status else None
        rows.append({
            "rank": idx + 1,
            "link": job.link,
            "title": job.title or "Bez tytułu",
            "company": job.company or "—",
            "location": job.location or "Warszawa",
            "source": job.source or "—",
            "source_color": ui_theme.source_color(job.source),
            "is_gone": job.link in job_data_service.zdjete,
            "is_new": bool(fresh_since) and (job.scraped_at or "") >= fresh_since,
            "match_percentage": pct,
            "status": status,
            "rating": rating,
            "decided_at": decided_at,
            "dot_color": dot_color,
            "dot_label": dot_label,
        })

    return JSONResponse({
        "items": rows,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "page_size": page_size,
        "page_marks": page_marks,
        "fresh_count": fresh_count,
        "fresh_since": fresh_since,
        "gap": gap,
        "gap_threshold": gap_threshold if gap else None,
    })


async def api_offer_detail(request: Request) -> JSONResponse:
    """Zwraca pełne szczegóły oferty: dopasowanie, chipy, sformatowany opis i stan decyzji."""
    link = request.query_params.get("link", "")
    if not link:
        return JSONResponse({"error": "Brak parametru link"}, status_code=400)
    detail = job_data_service.get_offer_detail(link)
    if detail is None:
        return JSONResponse({"error": "Oferta nie została znaleziona w bazie"}, status_code=404)
    return JSONResponse(detail)


async def api_decision_update(request: Request) -> JSONResponse:
    """Zapisuje lub aktualizuje decyzję użytkownika dla danej oferty."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Niepoprawny format JSON"}, status_code=400)

    link = body.get("link")
    status = body.get("status")
    rating = body.get("rating")
    stage = body.get("stage")

    if not link or not status:
        return JSONResponse({"error": "Pola 'link' i 'status' są wymagane"}, status_code=400)

    if rating is not None:
        try:
            rating = max(1, min(10, int(rating)))
        except (ValueError, TypeError):
            rating = 5

    ok = job_data_service.update_decision(link, status, rating, stage=stage)
    if not ok:
        return JSONResponse({"error": "Błąd zapisu decyzji do pliku"}, status_code=500)

    return JSONResponse({
        "ok": True,
        "stats": job_data_service.get_stats(),
        "offer": job_data_service.get_offer_detail(link),
    })


async def api_decision_restore(request: Request) -> JSONResponse:
    """Cofa decyzję użytkownika dla danej oferty (przywraca do bazy jako nieocenioną)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Niepoprawny format JSON"}, status_code=400)

    link = body.get("link")
    if not link:
        return JSONResponse({"error": "Pole 'link' jest wymagane"}, status_code=400)

    changed = job_data_service.restore_decision(link)
    return JSONResponse({
        "ok": True,
        "changed": changed,
        "stats": job_data_service.get_stats(),
        "offer": job_data_service.get_offer_detail(link),
    })


async def api_next_step_update(request: Request) -> JSONResponse:
    """Zapisuje następny krok aplikacji (`label`, `due`); pusty `label` go usuwa."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Niepoprawny format JSON"}, status_code=400)

    link = body.get("link")
    if not link:
        return JSONResponse({"error": "Pole 'link' jest wymagane"}, status_code=400)
    label = re.sub(r"\s+", " ", str(body.get("label") or "")).strip()[:80]
    due = body.get("due") or None
    if due is not None:
        due = str(due).strip()
        # "YYYY-MM-DD HH:MM" albo sam dzień, gdy godzina nie jest znana.
        fmt = "%Y-%m-%d %H:%M" if len(due) > 10 else "%Y-%m-%d"
        try:
            datetime.strptime(due, fmt)
        except ValueError:
            return JSONResponse({"error": "Termin w formacie RRRR-MM-DD albo RRRR-MM-DD GG:MM"}, status_code=400)
    if not label:
        due = None

    ok, msg = job_data_service.set_next_step(link, label, due)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return JSONResponse({"ok": True, "offer": job_data_service.get_offer_detail(link)})


async def api_offer_delete(request: Request) -> JSONResponse:
    """Bezpowrotnie usuwa ofertę z bazy, ocen AI i decyzji użytkownika."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Niepoprawny format JSON"}, status_code=400)

    link = body.get("link")
    if not link:
        return JSONResponse({"error": "Pole 'link' jest wymagane"}, status_code=400)

    job_data_service.delete_job_permanent(link)
    return JSONResponse({
        "ok": True,
        "stats": job_data_service.get_stats(),
    })


async def api_activity(request: Request) -> JSONResponse:
    """Zwraca listę ostatnich operacji (scraping, AI, profil) oraz 7 ostatnich decyzji."""
    job_data_service.ensure_loaded()
    return JSONResponse({
        "activity_rows": job_data_service.get_activity_rows(limit=6),
        "recent_decisions": job_data_service.get_recent_decisions(limit=7),
    })


async def api_applications(request: Request) -> JSONResponse:
    """Zwraca oferty w lejku rekrutacji z podziałem na etapy, statystykami i wiekiem na etapie."""
    job_data_service.ensure_loaded()
    return JSONResponse(job_data_service.get_applications())


async def api_gaps(request: Request) -> JSONResponse:
    """Zwraca analizę brakujących umiejętności dla danego progu dopasowania."""
    try:
        threshold = int(request.query_params.get("threshold", 50))
    except ValueError:
        threshold = 50
    threshold = max(30, min(80, threshold))
    gaps_data = job_data_service.get_skill_gaps(threshold)
    return JSONResponse(gaps_data)


async def api_tool_fetch_link(request: Request) -> JSONResponse:
    """Pobiera dane oferty ze wskazanego adresu URL za pomocą modułu link_fetcher."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Niepoprawny format JSON"}, status_code=400)

    url = (body.get("url") or "").strip()
    if not url:
        return JSONResponse({"error": "Podaj prawidłowy adres URL oferty"}, status_code=400)

    try:
        fetched = fetch_job_from_link(url)
        return JSONResponse({"ok": True, "data": fetched})
    except Exception as e:
        logger.error(f"Błąd pobierania oferty z linku {url}: {e}")
        return JSONResponse({"error": f"Nie udało się odczytać oferty ze wskazanego adresu: {e}"}, status_code=500)


async def api_tool_save_manual_job(request: Request) -> JSONResponse:
    """Zapisuje ręcznie dodaną/poprawioną ofertę do bazy."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Niepoprawny format JSON"}, status_code=400)

    ok, msg = save_manual_job(body)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)

    return JSONResponse({
        "ok": True,
        "message": msg,
        "stats": job_data_service.get_stats(),
        "offer": job_data_service.get_offer_detail(body.get("link", "")),
    })


# --- Pipeline & Narzędzia ---

async def api_pipeline_state(request: Request) -> JSONResponse:
    """Zwraca aktualny stan menedżera procesu pipeline'u, etapy i logi."""
    mgr = PipelineProcessManager.get_instance()
    state = mgr.get_state()
    state["logs"] = sanitize_logs(state.get("logs", []))
    return JSONResponse(state)


async def api_pipeline_prerequisites(request: Request) -> JSONResponse:
    """Sprawdza wymagania do uruchomienia pipeline'u (CV, klucze API, Playwright, Chromium)."""
    return JSONResponse(check_pipeline_prerequisites())


async def api_pipeline_start(request: Request) -> JSONResponse:
    """Uruchamia pełny pipeline lub tryb skip_scraping."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    mode = body.get("mode", "full")
    if mode not in ("full", "skip_scraping"):
        mode = "full"

    mgr = PipelineProcessManager.get_instance()
    ok, msg = mgr.start_pipeline(mode=mode)
    state = mgr.get_state()
    state["logs"] = sanitize_logs(state.get("logs", []))
    if not ok:
        return JSONResponse({"ok": False, "error": msg, "state": state}, status_code=400)
    return JSONResponse({"ok": True, "message": msg, "state": state})


async def api_pipeline_stop(request: Request) -> JSONResponse:
    """Kooperatywne zatrzymanie pipeline'u na granicy bieżącej paczki."""
    mgr = PipelineProcessManager.get_instance()
    ok, msg = mgr.stop_pipeline(force=False)
    state = mgr.get_state()
    state["logs"] = sanitize_logs(state.get("logs", []))
    return JSONResponse({"ok": ok, "message": msg, "state": state})


async def api_pipeline_force_stop(request: Request) -> JSONResponse:
    """Natychmiastowe, wymuszone zakończenie drzewa procesów pipeline'u."""
    mgr = PipelineProcessManager.get_instance()
    ok, msg = mgr.stop_pipeline(force=True)
    state = mgr.get_state()
    state["logs"] = sanitize_logs(state.get("logs", []))
    return JSONResponse({"ok": ok, "message": msg, "state": state})


async def api_pipeline_resume(request: Request) -> JSONResponse:
    """Wznawia pipeline z zapisanego checkpointu."""
    mgr = PipelineProcessManager.get_instance()
    ok, msg = mgr.resume_pipeline()
    state = mgr.get_state()
    state["logs"] = sanitize_logs(state.get("logs", []))
    if not ok:
        return JSONResponse({"ok": False, "error": msg, "state": state}, status_code=400)
    return JSONResponse({"ok": True, "message": msg, "state": state})


async def api_pipeline_reload_data(request: Request) -> JSONResponse:
    """Odświeża dane z dysku w pamięci podręcznej serwera."""
    job_data_service.reload()
    return JSONResponse({"ok": True, "stats": job_data_service.get_stats()})


async def api_pipeline_run_step(request: Request) -> JSONResponse:
    """Uruchamia pojedynczy krok zaawansowany."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Niepoprawny format JSON"}, status_code=400)

    step = body.get("step")
    mgr = PipelineProcessManager.get_instance()
    if mgr.is_running():
        return JSONResponse({"error": "Inny proces jest już w toku"}, status_code=400)

    cmd_map = {
        "scrapers": [sys.executable, "-u", "main_scraper.py"],
        "profile": [sys.executable, "-u", "generate_preference_profile.py"],
        "analysis": [sys.executable, "-u", "waterfall_analysis.py"],
        "rescore_all": [sys.executable, "-u", "waterfall_analysis.py", "--rescore-all"],
    }
    if step not in cmd_map:
        return JSONResponse({"error": f"Nieznany krok: {step}"}, status_code=400)

    ok, msg = mgr.start_pipeline(mode="standalone", cmd=cmd_map[step], is_resume=False)
    state = mgr.get_state()
    state["logs"] = sanitize_logs(state.get("logs", []))
    if not ok:
        return JSONResponse({"ok": False, "error": msg, "state": state}, status_code=400)
    return JSONResponse({"ok": True, "message": msg, "state": state})


# --- CV & API Keys ---

async def api_cv_get(request: Request) -> JSONResponse:
    return JSONResponse(get_cv_info())


async def api_cv_upload(request: Request) -> JSONResponse:
    """Obsługuje przesyłanie pliku CV (multipart form lub JSON base64)."""
    mgr = PipelineProcessManager.get_instance()
    if mgr.is_running():
        return JSONResponse({"error": "Nie można zmieniać CV w trakcie działania pipeline'u"}, status_code=400)

    content_type = request.headers.get("content-type", "")
    filename, content_bytes = "", b""

    if "multipart/form-data" in content_type:
        form = await request.form()
        uploaded_file = form.get("file")
        if not uploaded_file:
            return JSONResponse({"error": "Brak pliku w formularzu (pole 'file')"}, status_code=400)
        filename = getattr(uploaded_file, "filename", "cv.pdf")
        content_bytes = await uploaded_file.read()
    else:
        try:
            body = await request.json()
            filename = body.get("filename", "cv.txt")
            import base64
            content_bytes = base64.b64decode(body.get("content_base64", ""))
        except Exception:
            return JSONResponse({"error": "Niepoprawny format żądania przesłania pliku"}, status_code=400)

    if not content_bytes:
        return JSONResponse({"error": "Przesłany plik jest pusty"}, status_code=400)

    ok, msg = save_uploaded_cv(filename, content_bytes)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)

    return JSONResponse({"ok": True, "message": msg, "cv_info": get_cv_info()})


async def api_cv_paste(request: Request) -> JSONResponse:
    mgr = PipelineProcessManager.get_instance()
    if mgr.is_running():
        return JSONResponse({"error": "Nie można zmieniać CV w trakcie działania pipeline'u"}, status_code=400)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Niepoprawny format JSON"}, status_code=400)

    text = body.get("text", "")
    ok, msg = save_pasted_cv_text(text)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)

    return JSONResponse({"ok": True, "message": msg, "cv_info": get_cv_info()})


async def api_cv_open_local(request: Request) -> JSONResponse:
    ok, msg = open_local_cv_pdf()
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return JSONResponse({"ok": True, "message": msg})


async def api_env_keys_get(request: Request) -> JSONResponse:
    return JSONResponse({
        "fields": get_env_fields_status(),
        "api_info": get_api_keys_info(),
    })


async def api_env_keys_save(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Niepoprawny format JSON"}, status_code=400)

    keys = body.get("keys", {})
    if not isinstance(keys, dict):
        return JSONResponse({"error": "Pole 'keys' musi być obiektem JSON"}, status_code=400)

    ok, msg = save_env_keys(keys)
    if not ok:
        return JSONResponse({"error": msg}, status_code=500)

    return JSONResponse({
        "ok": True,
        "message": msg,
        "fields": get_env_fields_status(),
        "api_info": get_api_keys_info(),
    })


async def api_env_keys_llm(request: Request) -> JSONResponse:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Niepoprawny format JSON"}, status_code=400)

    models, base_url = body.get("models"), body.get("base_url")
    if not all(v is None or isinstance(v, str) for v in (body.get("key"), models, base_url)):
        return JSONResponse({"error": "Pola key, models i base_url muszą być tekstem"}, status_code=400)

    ok, msg = save_llm_settings(str(body.get("provider") or ""), body.get("key") or "", models, base_url)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return JSONResponse({
        "ok": True,
        "message": msg,
        "api_info": get_api_keys_info(),
        "fields": get_env_fields_status(),
    })


# --- Routing i obsługa statycznego frontendu ---

async def spa_index_fallback(request: Request) -> Response:
    index_file = FRONTEND_DIST_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return Response(
        "Job Finder Frontend build not found. Run 'npm run build' in frontend/ directory.",
        media_type="text/plain",
        status_code=503,
    )


routes = [
    # Bootstrap & Stats
    Route("/api/bootstrap", api_bootstrap, methods=["GET"]),
    Route("/api/stats", api_stats, methods=["GET"]),
    # Offers & Decisions
    Route("/api/offers", api_offers, methods=["GET"]),
    Route("/api/offers/detail", api_offer_detail, methods=["GET"]),
    Route("/api/offers/decision", api_decision_update, methods=["POST"]),
    Route("/api/offers/restore", api_decision_restore, methods=["POST"]),
    Route("/api/offers/delete", api_offer_delete, methods=["POST"]),
    Route("/api/offers/next-step", api_next_step_update, methods=["POST"]),
    # Activity & Gaps
    Route("/api/activity", api_activity, methods=["GET"]),
    Route("/api/applications", api_applications, methods=["GET"]),
    Route("/api/gaps", api_gaps, methods=["GET"]),
    # Tools
    Route("/api/tools/fetch-link", api_tool_fetch_link, methods=["POST"]),
    Route("/api/tools/save-manual-job", api_tool_save_manual_job, methods=["POST"]),
    # Pipeline
    Route("/api/pipeline/state", api_pipeline_state, methods=["GET"]),
    Route("/api/pipeline/prerequisites", api_pipeline_prerequisites, methods=["GET"]),
    Route("/api/pipeline/start", api_pipeline_start, methods=["POST"]),
    Route("/api/pipeline/stop", api_pipeline_stop, methods=["POST"]),
    Route("/api/pipeline/force-stop", api_pipeline_force_stop, methods=["POST"]),
    Route("/api/pipeline/resume", api_pipeline_resume, methods=["POST"]),
    Route("/api/pipeline/reload-data", api_pipeline_reload_data, methods=["POST"]),
    Route("/api/pipeline/run-step", api_pipeline_run_step, methods=["POST"]),
    # CV & Keys
    Route("/api/cv", api_cv_get, methods=["GET"]),
    Route("/api/cv/upload", api_cv_upload, methods=["POST"]),
    Route("/api/cv/paste", api_cv_paste, methods=["POST"]),
    Route("/api/cv/open-local", api_cv_open_local, methods=["POST"]),
    Route("/api/env-keys", api_env_keys_get, methods=["GET"]),
    Route("/api/env-keys", api_env_keys_save, methods=["POST"]),
    Route("/api/env-keys/llm", api_env_keys_llm, methods=["POST"]),
]

# Static files mount
if FRONTEND_DIST_DIR.exists():
    routes.append(Mount("/assets", StaticFiles(directory=str(FRONTEND_DIST_DIR / "assets")), name="assets"))
routes.append(Route("/{path:path}", spa_index_fallback, methods=["GET"]))


app = Starlette(
    routes=routes,
    middleware=[
        Middleware(
            TrustedHostMiddleware,
            allowed_hosts=["127.0.0.1", "localhost", "::1"],
        ),
        Middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8501", "http://127.0.0.1:8501"],
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["*"],
        ),
        Middleware(BaseHTTPMiddleware, dispatch=origin_check_middleware),
    ],
)


def run_server(host: str = "127.0.0.1", port: int = 8501):
    logger.info(f"Uruchamianie Job Finder na http://{host}:{port} (lokalne wiązanie)")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8501))
    run_server(host="127.0.0.1", port=port)
