from __future__ import annotations

import asyncio

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.routing import Route

from cv_tailor import review, store, tailor
from cv_tailor.model import cv_text, llm_settings
from cv_tailor.render import render_html, render_pdf
from utils.data_models import is_placeholder_description


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


async def _body(request: Request) -> dict | None:
    try:
        data = await request.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _with_body(handler):
    async def endpoint(request: Request):
        data = await _body(request)
        if data is None:
            return _error("Niepoprawny format JSON")
        return await asyncio.to_thread(handler, request, data)
    return endpoint


def _detail(v: dict) -> dict:
    return review.detail(v, store.to_summary(v))


def _missing() -> JSONResponse:
    return _error("Nie znaleziono tej wersji CV.", 404)


def list_versions(request: Request) -> JSONResponse:
    link = request.query_params.get("link")
    return JSONResponse({"items": store.list_versions(link or None)})


def create_version(request: Request, data: dict) -> JSONResponse:
    if data.get("consent") is not True:
        return _error("Potwierdź zgodę na wysłanie CV i oferty do modelu.")
    link = str(data.get("link") or "").strip()
    if not link:
        return _error("Pole 'link' jest wymagane")
    language = data.get("language") or "pl"
    if language not in ("pl", "en"):
        return _error("Język CV musi być 'pl' albo 'en'.")
    if cv_text() is None:
        return _error("Brak CV. Najpierw wgraj CV.")
    settings = llm_settings()
    if not settings.api_keys:
        return _error(settings.error or "Brak klucza modelu. Dodaj go w ustawieniach.")
    offer = tailor.load_offer(link)
    if offer is None:
        return _error("Oferta nie została znaleziona w bazie", 404)
    description = offer["description"].strip()
    if not description or is_placeholder_description(description):
        return _error("Ta oferta nie ma opisu, więc nie ma do czego dopasować CV.")
    v = store.create(offer["link"], offer["company"], offer["title"], {
        "language": language,
        "facts": str(data.get("facts") or "").strip(),
        "lock_contact": bool(data.get("lock_contact", True)),
        "keep_order": bool(data.get("keep_order", True)),
    })
    tailor.start(v["id"])
    return JSONResponse({"ok": True, "version": store.summary(v["id"])})


def get_version(request: Request) -> JSONResponse:
    v = store.get(request.path_params["id"])
    return JSONResponse(_detail(v)) if v else _missing()


def cancel_version(request: Request) -> JSONResponse:
    v = tailor.cancel(request.path_params["id"])
    return JSONResponse({"ok": True, "version": store.to_summary(v)}) if v else _missing()


def retry_version(request: Request) -> JSONResponse:
    version_id = request.path_params["id"]
    v = store.get(version_id)
    if v is None:
        return _missing()
    if v["status"] != "failed":
        return _error("Ponowić można tylko nieudaną wersję.")
    if cv_text() is None:
        return _error("Brak CV. Najpierw wgraj CV.")
    settings = llm_settings()
    if not settings.api_keys:
        return _error(settings.error or "Brak klucza modelu. Dodaj go w ustawieniach.")
    v = tailor.retry(version_id)
    return JSONResponse({"ok": True, "version": store.to_summary(v)})


def _review_op(request: Request, data: dict, op) -> JSONResponse:
    errors: list[str] = []

    def fn(v: dict):
        try:
            op(v, data.get("idx"), str(data.get("action") or ""), data.get("text"))
        except review.ReviewError as e:
            errors.append(str(e))
            return False

    v = store.update(request.path_params["id"], fn)
    if v is None:
        return _missing()
    if errors:
        return _error(errors[0])
    return JSONResponse(_detail(v))


def change(request: Request, data: dict) -> JSONResponse:
    return _review_op(request, data, review.apply_change)


def number(request: Request, data: dict) -> JSONResponse:
    return _review_op(request, data, review.apply_number)


def _html(v: dict, which: str, marks: bool) -> str:
    cv = review.effective_cv(v, which, marks)
    order = None if v.get("keep_order") or which == "base" else v.get("section_order")
    return render_html(cv, v.get("language") or "pl", order, title=f"CV {v.get('company') or ''}".strip())


def finalize(request: Request) -> JSONResponse:
    version_id = request.path_params["id"]
    v = store.get(version_id)
    if v is None:
        return _missing()
    if v["status"] not in ("draft", "ready"):
        return _error("Tę wersję można zapisać dopiero po przygotowaniu szkicu.")
    pending = store.to_summary(v)["pending_numbers"]
    if pending:
        return _error(f"Przed pobraniem sprawdź liczby do potwierdzenia ({pending}).")
    try:
        render_pdf(_html(v, "tailored", False), store.pdf_path(version_id))
    except Exception as e:
        return _error(f"Nie udało się utworzyć PDF: {type(e).__name__}: {str(e)[:200]}", 500)
    file_name = f"cv_{store.slug(v.get('company') or '')}_v{v['version']}.pdf"

    def fn(cur: dict):
        if cur["status"] not in ("draft", "ready"):
            return False
        cur.update(status="ready", file_name=file_name)

    v = store.update(version_id, fn)
    return JSONResponse(_detail(v))


def pdf(request: Request):
    version_id = request.path_params["id"]
    v = store.get(version_id)
    if v is None:
        return _missing()
    path = store.pdf_path(version_id)
    if v["status"] != "ready" or not path.exists():
        return _error("PDF tej wersji nie jest jeszcze gotowy.", 404)
    return FileResponse(path, media_type="application/pdf", filename=v["file_name"])


def html_preview(request: Request):
    v = store.get(request.path_params["id"])
    if v is None:
        return _missing()
    which = request.query_params.get("which") or "tailored"
    if which not in ("tailored", "base"):
        return _error("Parametr 'which' musi mieć wartość 'tailored' albo 'base'.")
    if not v.get("base") or (which == "tailored" and not v.get("tailored")):
        return _error("Ta wersja nie ma jeszcze treści CV.", 404)
    return HTMLResponse(_html(v, which, request.query_params.get("marks") == "1"))


def get_instructions(request: Request) -> JSONResponse:
    return JSONResponse(tailor.instructions())


def save_instructions(request: Request, data: dict) -> JSONResponse:
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        return _error("Instrukcje nie mogą być puste.")
    return JSONResponse(tailor.save_instructions(text))


def reset_instructions(request: Request) -> JSONResponse:
    return JSONResponse(tailor.reset_instructions())


routes = [
    Route("/api/cv-versions", list_versions, methods=["GET"]),
    Route("/api/cv-versions", _with_body(create_version), methods=["POST"]),
    Route("/api/cv-versions/{id}", get_version, methods=["GET"]),
    Route("/api/cv-versions/{id}/cancel", cancel_version, methods=["POST"]),
    Route("/api/cv-versions/{id}/retry", retry_version, methods=["POST"]),
    Route("/api/cv-versions/{id}/change", _with_body(change), methods=["POST"]),
    Route("/api/cv-versions/{id}/number", _with_body(number), methods=["POST"]),
    Route("/api/cv-versions/{id}/finalize", finalize, methods=["POST"]),
    Route("/api/cv-versions/{id}/pdf", pdf, methods=["GET"]),
    Route("/api/cv-versions/{id}/html", html_preview, methods=["GET"]),
    Route("/api/cv-tailor/instructions", get_instructions, methods=["GET"]),
    Route("/api/cv-tailor/instructions", _with_body(save_instructions), methods=["POST"]),
    Route("/api/cv-tailor/instructions/reset", reset_instructions, methods=["POST"]),
]
