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
    APP_STAGES,
    CV_PDF_PATH,
    DECISION_STATUSES,
    OFFER_TABS,
    check_pipeline_prerequisites,
    fetch_job_from_link,
    get_api_keys_info,
    get_cv_info,
    get_env_fields_status,
    job_data_service,
    pdf_is_current,
    save_env_keys,
    save_manual_job,
    save_pasted_cv_text,
    save_llm_settings,
    save_uploaded_cv,
)
from cv_tailor.routes import routes as cv_tailor_routes
from pipeline_manager import PipelineProcessManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("server")

BASE_DIR = Path(__file__).parent
FRONTEND_DIST_DIR = BASE_DIR / "frontend" / "dist"

PAGE_SIZE = 5


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

async def api_stats(request: Request) -> JSONResponse:
    job_data_service.ensure_loaded()
    return JSONResponse(job_data_service.get_stats())


async def api_offers(request: Request) -> JSONResponse:
    """Zwraca stronicowaną listę ofert dla zakładki (Dopasowane, Ukryte, Zapisane)."""
    tab = request.query_params.get("tab", "Dopasowane")
    if tab not in OFFER_TABS:
        return JSONResponse({"error": f"Nieznana zakładka: {tab}"}, status_code=400)
    search = request.query_params.get("search", "").strip()
    sort = "newest" if request.query_params.get("sort") == "newest" else "match"
    try:
        page = max(1, int(request.query_params.get("page", 1)))
    except ValueError:
        page = 1
    try:
        page_size = max(1, min(100, int(request.query_params.get("page_size", PAGE_SIZE))))
    except ValueError:
        page_size = PAGE_SIZE
    job_data_service.ensure_loaded()
    items = job_data_service.ws_collect(tab, search, sort)
    total = len(items)
    total_pages = max(1, math.ceil(total / page_size)) if total else 1
    page = min(page, total_pages)
    start = (page - 1) * page_size

    # Nowe = pierwszy raz zobaczone od startu ostatniego pobierania. Oba znaczniki to
    # isoformat czasu lokalnego, więc porównanie napisów = porównanie chwil.
    fresh_since = job_data_service.fresh_since()
    fresh_count = (
        sum(1 for job, _ in items if (job.scraped_at or "") >= fresh_since) if fresh_since else 0
    )
    rows = [job_data_service.list_item(job, rec, fresh_since) for job, rec in items[start : start + page_size]]

    return JSONResponse({
        "items": rows,
        "total": total,
        "page": page,
        "total_pages": total_pages,
        "page_size": page_size,
        "fresh_count": fresh_count,
        "fresh_since": fresh_since,
        "hidden_count": job_data_service.hidden_count(),
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


async def _json_body(request: Request):
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


async def api_decision_update(request: Request) -> JSONResponse:
    """Zapisuje decyzję: zapisana na później, wysłana (z etapem lejka) albo ukryta."""
    body = await _json_body(request)
    if body is None:
        return JSONResponse({"error": "Niepoprawny format JSON"}, status_code=400)
    link = body.get("link")
    status = body.get("status")
    stage = body.get("stage") or None
    cv_version_id = body.get("cv_version_id") or None
    if not link or status not in DECISION_STATUSES:
        return JSONResponse({"error": "Pola 'link' i 'status' (save, apply, reject) są wymagane"}, status_code=400)
    if stage is not None and stage not in APP_STAGES:
        return JSONResponse({"error": f"Nieznany etap: {stage}"}, status_code=400)

    ok = job_data_service.update_decision(link, status, stage=stage, cv_version_id=cv_version_id)
    if not ok:
        return JSONResponse({"error": "Błąd zapisu decyzji do pliku"}, status_code=500)
    return JSONResponse({"ok": True, "offer": job_data_service.get_offer_detail(link)})


async def api_note_update(request: Request) -> JSONResponse:
    """Zapisuje notatkę do oferty z decyzją; pusta notatka ją usuwa."""
    body = await _json_body(request)
    if body is None:
        return JSONResponse({"error": "Niepoprawny format JSON"}, status_code=400)
    link = body.get("link")
    if not link:
        return JSONResponse({"error": "Pole 'link' jest wymagane"}, status_code=400)
    note = str(body.get("note") or "").strip()[:2000]
    ok, msg = job_data_service.set_note(link, note)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return JSONResponse({"ok": True, "offer": job_data_service.get_offer_detail(link)})


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
    return JSONResponse({"ok": True, "changed": changed, "offer": job_data_service.get_offer_detail(link)})


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


async def api_applications(request: Request) -> JSONResponse:
    """Zwraca wysłane aplikacje z etapem lejka (wysłane, rozmowy, oferta pracy, zakończone)."""
    job_data_service.ensure_loaded()
    return JSONResponse(job_data_service.get_applications())


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
    """Zapisuje ręcznie dodaną ofertę do bazy; ze `status` od razu zapisuje też decyzję."""
    body = await _json_body(request)
    if body is None:
        return JSONResponse({"error": "Niepoprawny format JSON"}, status_code=400)
    status = body.get("status") or None
    if status is not None and status not in ("save", "apply"):
        return JSONResponse({"error": "Pole 'status' przyjmuje save albo apply"}, status_code=400)

    ok, msg = save_manual_job(body)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    link = body.get("link", "")
    if status and not job_data_service.update_decision(link, status):
        return JSONResponse({"error": "Oferta zapisana, ale nie udało się zapisać decyzji"}, status_code=500)

    return JSONResponse({"ok": True, "message": msg, "offer": job_data_service.get_offer_detail(link)})


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


async def api_pipeline_run_summary(request: Request) -> JSONResponse:
    """Liczby i najlepsze dopasowania bieżącego (albo ostatniego) wyszukiwania dla ekranu postępu."""
    state = PipelineProcessManager.get_instance().get_state()
    started = state.get("started_at")
    since = datetime.fromtimestamp(started).isoformat(timespec="seconds") if started else None
    telemetry = state.get("telemetry") or {}
    added = [s.get("added") for s in telemetry.get("sources") or [] if isinstance(s.get("added"), int)]
    scoring = telemetry.get("scoring") or {}
    to_check = scoring.get("to_score")
    downloaded = sum(added) if added else None
    if downloaded is None and isinstance(to_check, int):
        downloaded = to_check + (scoring.get("prefilter_rejected") or 0)
    return JSONResponse({
        "started_at": since,
        "downloaded": downloaded,
        "checked": scoring.get("scored") or 0,
        "to_check": to_check,
        **job_data_service.run_summary(since),
    })


# --- CV & API Keys ---

async def api_cv_get(request: Request) -> JSONResponse:
    return JSONResponse(get_cv_info())


async def api_cv_file(request: Request) -> Response:
    """Bieżące CV w PDF do podglądu w przeglądarce."""
    if not pdf_is_current():
        return JSONResponse({"error": "Brak pliku PDF z CV"}, status_code=404)
    return FileResponse(CV_PDF_PATH, media_type="application/pdf",
                        headers={"Content-Disposition": 'inline; filename="cv.pdf"', "Cache-Control": "no-store"})


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
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Endpoint nie istnieje"}, status_code=404)
    index_file = FRONTEND_DIST_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return Response(
        "Job Finder Frontend build not found. Run 'npm run build' in frontend/ directory.",
        media_type="text/plain",
        status_code=503,
    )


routes = [
    Route("/api/stats", api_stats, methods=["GET"]),
    Route("/api/offers", api_offers, methods=["GET"]),
    Route("/api/offers/detail", api_offer_detail, methods=["GET"]),
    Route("/api/offers/decision", api_decision_update, methods=["POST"]),
    Route("/api/offers/restore", api_decision_restore, methods=["POST"]),
    Route("/api/offers/note", api_note_update, methods=["POST"]),
    Route("/api/offers/next-step", api_next_step_update, methods=["POST"]),
    Route("/api/applications", api_applications, methods=["GET"]),
    Route("/api/tools/fetch-link", api_tool_fetch_link, methods=["POST"]),
    Route("/api/tools/save-manual-job", api_tool_save_manual_job, methods=["POST"]),
    Route("/api/pipeline/state", api_pipeline_state, methods=["GET"]),
    Route("/api/pipeline/prerequisites", api_pipeline_prerequisites, methods=["GET"]),
    Route("/api/pipeline/start", api_pipeline_start, methods=["POST"]),
    Route("/api/pipeline/stop", api_pipeline_stop, methods=["POST"]),
    Route("/api/pipeline/run-summary", api_pipeline_run_summary, methods=["GET"]),
    Route("/api/cv", api_cv_get, methods=["GET"]),
    Route("/api/cv/file", api_cv_file, methods=["GET"]),
    Route("/api/cv/upload", api_cv_upload, methods=["POST"]),
    Route("/api/cv/paste", api_cv_paste, methods=["POST"]),
    Route("/api/env-keys", api_env_keys_get, methods=["GET"]),
    Route("/api/env-keys", api_env_keys_save, methods=["POST"]),
    Route("/api/env-keys/llm", api_env_keys_llm, methods=["POST"]),
    *cv_tailor_routes,
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
