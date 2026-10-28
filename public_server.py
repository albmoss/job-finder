import asyncio
import hashlib
import logging
import os
import random
import re
import secrets
import sqlite3
import statistics
import tempfile
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

import config
from app_services import WORK_MODE_LABELS, salary_text
from matching import jev, quick
from public_accounts import (LOGIN_TTL, PLANS, SESSION_TTL, Accounts, MailUnavailable, mail_mode,
                             send_login_link)
from utils.cv_parser import CVParser
from utils.cv_profile import build_profile
from utils.logos import logo_png

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("public")

BASE_DIR = Path(__file__).parent
DIST_DIR = BASE_DIR / "frontend" / "dist-public"
DATA_DIR = Path(os.environ.get("PUBLIC_DATA_DIR") or BASE_DIR / "public_data")
DB_PATH = Path(os.environ.get("PUBLIC_DB_PATH") or DATA_DIR / "offers")
FREE_WINDOW_DAYS = int(os.environ.get("PUBLIC_FREE_WINDOW_DAYS", "30"))
UNLIMITED_IPS = {ip.strip() for ip in os.environ.get("PUBLIC_UNLIMITED_IPS", "").split(",") if ip.strip()}
DAILY_LIMIT = int(os.environ.get("PUBLIC_DAILY_LIMIT", "300"))
WORKERS = int(os.environ.get("PUBLIC_WORKERS", "2"))
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("PUBLIC_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()]
SITE_URL = os.environ.get("PUBLIC_SITE_URL", "").strip().rstrip("/")
PRO_SCORED = int(os.environ.get("PUBLIC_PRO_SCORED", "300"))
PRO_DAILY = int(os.environ.get("PUBLIC_PRO_DAILY", "3"))
LOGIN_MAILS_PER_IP_HOUR = 5
JEV_USD_PER_MTOK = 0.042
SESSION_COOKIE = "traffy_session"
MAX_UPLOAD = 5 * 1024 * 1024
MIN_CV_CHARS = 200
SEARCH_TTL = 2 * 3600
DAY = 24 * 3600
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[a-z]{2,}$", re.IGNORECASE)
LEGAL = {
    "operator": os.environ.get("PUBLIC_OPERATOR_NAME", "").strip(),
    "email": os.environ.get("PUBLIC_OPERATOR_EMAIL", "").strip(),
    "hosting": os.environ.get("PUBLIC_HOSTING_PROVIDER", "").strip(),
    "cv_reader": config.LLM.provider.label + (
        f" ({urlparse(config.LLM.base_url).hostname})" if config.LLM.provider.id == "openai" else ""),
    "search_ttl_hours": SEARCH_TTL // 3600,
    "free_window_days": FREE_WINDOW_DAYS,
    "session_days": SESSION_TTL // DAY,
    "login_minutes": LOGIN_TTL // 60,
}

index_holder: dict = {"path": None, "index": None}
index_lock = threading.Lock()
executor = ThreadPoolExecutor(WORKERS, thread_name_prefix="search")
searches: dict[str, dict] = {}
searches_lock = threading.Lock()
running_pro: dict[int, str] = {}
started_all: deque = deque()
limits_lock = threading.Lock()
login_mails: dict[str, deque] = {}
accounts = Accounts(DATA_DIR / "accounts.db")


def current_index() -> quick.OfferIndex:
    path = DB_PATH
    if DB_PATH.is_dir():
        snapshots = sorted(DB_PATH.glob("offers-*.db"))
        if not snapshots:
            raise RuntimeError(f"Brak bazy ofert w {DB_PATH}")
        path = snapshots[-1]
    with index_lock:
        if index_holder["path"] != path:
            index_holder.update(path=path, index=quick.OfferIndex(path))
        index = index_holder["index"]
    return index


class UserError(Exception):
    pass


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _limits_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA_DIR / "limits.db", timeout=30)
    conn.execute("CREATE TABLE IF NOT EXISTS free_use (ip_hash TEXT PRIMARY KEY, used_at REAL NOT NULL)")
    return conn


def _ip_hash(ip: str) -> str:
    salt_file = DATA_DIR / "ip_salt"
    salt = os.environ.get("PUBLIC_IP_SALT", "").strip()
    if not salt:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        if not salt_file.exists():
            salt_file.write_text(secrets.token_hex(32), encoding="utf-8")
        salt = salt_file.read_text(encoding="utf-8").strip()
    return hashlib.sha256(f"{salt}|{ip}".encode("utf-8")).hexdigest()


def _daily_full(now: float) -> bool:
    while started_all and now - started_all[0] > DAY:
        started_all.popleft()
    return len(started_all) >= DAILY_LIMIT


def _take_slot(ip: str) -> tuple[str | None, str | None, str | None]:
    now = time.time()
    with limits_lock:
        if _daily_full(now):
            return "busy", "Serwis jest dziś bardzo oblegany. Wróć jutro.", None
        ip_hash = None
        if ip not in UNLIMITED_IPS:
            ip_hash = _ip_hash(ip)
            with closing(_limits_db()) as conn, conn:
                conn.execute("DELETE FROM free_use WHERE used_at < ?", (now - FREE_WINDOW_DAYS * DAY,))
                if conn.execute("SELECT 1 FROM free_use WHERE ip_hash = ?", (ip_hash,)).fetchone():
                    return "free_used", "Darmowe wyszukiwanie z tego adresu zostało już wykorzystane.", None
                conn.execute("INSERT INTO free_use(ip_hash, used_at) VALUES (?, ?)", (ip_hash, now))
        started_all.append(now)
    return None, None, ip_hash


def _refund(ip_hash: str | None) -> None:
    if ip_hash:
        with limits_lock, closing(_limits_db()) as conn, conn:
            conn.execute("DELETE FROM free_use WHERE ip_hash = ?", (ip_hash,))


def _cv_text(data: bytes) -> str:
    if data.startswith(b"%PDF"):
        suffix, extract = ".pdf", CVParser.extract_from_pdf
    elif data.startswith(b"PK"):
        suffix, extract = ".docx", CVParser.extract_from_docx
    else:
        raise UserError("Obsługujemy CV w PDF albo DOCX.")
    fd, name = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        text = CVParser.clean_text(extract(name) or "")
    finally:
        os.unlink(name)
    if len(text) < MIN_CV_CHARS:
        raise UserError("Nie udało się odczytać tekstu z CV. Jeśli to skan, wgraj wersję z tekstem.")
    return text


def _cv_profile(text: str) -> dict:
    profile = build_profile(text)
    if not profile["is_cv"] or not profile["roles"]:
        raise UserError("To nie wygląda na CV. Wgraj swoje CV w PDF albo DOCX.")
    return profile


def _profile_summary(profile: dict) -> dict:
    return {"city": profile.get("city"), "seniority": profile.get("seniority"),
            "roles": (profile.get("roles") or [])[:3]}


def _jev_key() -> str:
    key = jev.api_key()
    if not key:
        raise RuntimeError("Brak TYPESAFE_API_KEY")
    return key


def _update(search_id: str, **fields) -> None:
    with searches_lock:
        search = searches.get(search_id)
        if search is not None:
            search.update(fields)


def _progress(search_id: str):
    def progress(stage, **counts):
        with searches_lock:
            search = searches.get(search_id)
            if search is not None:
                search["status"] = stage
                search["progress"].update(counts)
    return progress


def _fail(search_id: str, error: Exception) -> None:
    if isinstance(error, UserError):
        _update(search_id, status="error", error=str(error), finished=time.time())
        return
    logger.error(f"search {search_id[:8]} failed: {error}", exc_info=error)
    _update(search_id, status="error", finished=time.time(),
            error="Coś poszło nie tak po naszej stronie. Spróbuj ponownie za kilka minut.")


def _format_pick(pick: dict) -> dict:
    modes = [WORK_MODE_LABELS[m] for m in pick.get("work_modes") or [] if m in WORK_MODE_LABELS]
    return {
        "link": pick["link"], "title": pick["title"], "company": pick["company"],
        "location": pick.get("location") or "", "source": pick.get("source") or "",
        "salary": salary_text(pick.get("salary")), "work_mode": ", ".join(modes),
        "logo_url": pick.get("logo_url"), "percent": pick["percent"],
    }


def _run_search(search_id: str, data: bytes, ip_hash: str | None) -> None:
    tokens = offers = 0
    ok = False
    try:
        _update(search_id, status="reading")
        text = _cv_text(data)
        del data
        _update(search_id, status="profile")
        profile = _cv_profile(text)
        _update(search_id, profile=_profile_summary(profile))
        result = quick.find_matches(current_index(), profile, text, _jev_key(), progress=_progress(search_id))
        tokens = result.tokens
        picks = [_format_pick(p) for p in result.picks]
        random.shuffle(picks)
        low = min(max(max((p["percent"] for p in picks), default=0) + 3, 86), 95)
        teasers = [random.randint(low, 98) for _ in range(min(3, result.promising))]
        _update(search_id, status="done", finished=time.time(), result={
            "picks": picks, "good": result.good, "teasers": teasers,
        })
        ok, offers = True, len(picks)
        logger.info(f"search {search_id[:8]}: ranked {result.ranked}, triaged {result.triaged}, "
                    f"scored {result.scored}, good {result.good}, errors {result.errors}")
    except Exception as e:
        _refund(ip_hash)
        _fail(search_id, e)
    finally:
        accounts.log_search("guest", None, ok, tokens, offers)


def _run_pro(search_id: str, user_id: int, data: bytes | None) -> None:
    tokens = offers = 0
    ok = False
    try:
        saved = accounts.cv(user_id)
        if data is not None:
            _update(search_id, status="reading")
            text = _cv_text(data)
            del data
            sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if saved and saved[1] == sha:
                profile = saved[2]
            else:
                _update(search_id, status="profile")
                profile = _cv_profile(text)
                accounts.set_cv(user_id, text, sha, profile)
        elif saved:
            text, _, profile = saved
        else:
            raise UserError("Najpierw dodaj swoje CV.")
        _update(search_id, profile=_profile_summary(profile))
        result = quick.score_top(current_index(), profile, text, _jev_key(), PRO_SCORED,
                                 accounts.known_percents(user_id), progress=_progress(search_id))
        tokens = result.tokens
        picks = [_format_pick(p) for p in result.picks]
        accounts.replace_matches(user_id, picks)
        _update(search_id, status="done", finished=time.time(), result={"count": len(picks)})
        ok, offers = True, len(picks)
        logger.info(f"pro search {search_id[:8]}: ranked {result.ranked}, scored {result.scored}, "
                    f"reused {result.reused}, errors {result.errors}")
    except Exception as e:
        _fail(search_id, e)
    finally:
        accounts.log_search("pro", user_id, ok, tokens, offers)
        with searches_lock:
            running_pro.pop(user_id, None)


def _purge_old() -> None:
    now = time.time()
    with searches_lock:
        for search_id in [s for s, v in searches.items() if now - v["created"] > SEARCH_TTL]:
            del searches[search_id]


def _new_search(kind: str, user_id: int | None = None) -> str:
    _purge_old()
    search_id = secrets.token_urlsafe(18)
    with searches_lock:
        searches[search_id] = {"created": time.time(), "kind": kind, "user_id": user_id, "status": "queued",
                               "progress": {}, "profile": None, "result": None, "error": None}
        if user_id is not None:
            running_pro[user_id] = search_id
    return search_id


async def _read_upload(request: Request) -> bytes | JSONResponse:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_UPLOAD:
            return JSONResponse({"error": "Plik jest za duży (limit 5 MB)."}, status_code=413)
    return bytes(data)


async def api_search_start(request: Request) -> Response:
    data = await _read_upload(request)
    if isinstance(data, Response):
        return data
    if not data.startswith((b"%PDF", b"PK")):
        return JSONResponse({"error": "Obsługujemy CV w PDF albo DOCX."}, status_code=400)
    code, refusal, ip_hash = _take_slot(_client_ip(request))
    if code:
        return JSONResponse({"error": refusal, "code": code}, status_code=429)
    search_id = _new_search("guest")
    executor.submit(_run_search, search_id, data, ip_hash)
    return JSONResponse({"id": search_id})


def api_search_state(request: Request) -> Response:
    search_id = request.path_params["search_id"]
    with searches_lock:
        search = searches.get(search_id)
        if search is None:
            return JSONResponse({"error": "Nie ma takiego wyszukiwania albo wygasło."}, status_code=404)
        ahead = sum(1 for v in searches.values() if v["status"] == "queued" and v["created"] < search["created"])
        body = {k: search[k] for k in ("kind", "status", "progress", "profile", "result", "error")}
    body["queue"] = ahead if body["status"] == "queued" else 0
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


def api_legal(request: Request) -> Response:
    return JSONResponse(LEGAL)


def _session(request: Request) -> str:
    return request.cookies.get(SESSION_COOKIE, "")


def _user(request: Request) -> dict | None:
    session = _session(request)
    return accounts.user_for_session(session) if session else None


def _me(user: dict) -> dict:
    with searches_lock:
        running = running_pro.get(user["id"])
    left = None if user["admin"] or not user["pro"] else max(0, PRO_DAILY - accounts.pro_searches_today(user["id"]))
    return {
        "email": user["email"], "plan": user["plan"], "pro": user["pro"], "admin": user["admin"],
        "cv": {**_profile_summary(user["profile"]), "at": user["cv_at"]} if user["profile"] else None,
        "running": running, "searches_left": left,
    }


def _denied(user: dict | None, need: str) -> JSONResponse | None:
    if user is None:
        return JSONResponse({"error": "Zaloguj się.", "code": "login"}, status_code=401)
    if need == "pro" and not user["pro"]:
        return JSONResponse({"error": "Pełna lista jest w planie Pro.", "code": "pro"}, status_code=403)
    if need == "admin" and not user["admin"]:
        return JSONResponse({"error": "Brak dostępu."}, status_code=403)
    return None


def json_endpoint(handler):
    async def endpoint(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return JSONResponse({"error": "Niepoprawny format JSON"}, status_code=400)
        return await asyncio.to_thread(handler, request, body)
    return endpoint


def _login_mail_allowed(ip: str) -> bool:
    now = time.time()
    with limits_lock:
        sent = login_mails.setdefault(ip, deque())
        while sent and now - sent[0] > 3600:
            sent.popleft()
        if len(sent) >= LOGIN_MAILS_PER_IP_HOUR and ip not in UNLIMITED_IPS:
            return False
        sent.append(now)
    return True


def api_login_request(request: Request, body: dict) -> Response:
    email = str(body.get("email") or "").strip().lower()
    if not EMAIL_RE.match(email):
        return JSONResponse({"error": "Podaj poprawny adres e-mail."}, status_code=400)
    if mail_mode() == "off":
        logger.error("Logowanie niedostępne: brak konfiguracji PUBLIC_SMTP_*")
        return JSONResponse({"error": "Logowanie jest chwilowo niedostępne. Spróbuj później."}, status_code=503)
    if not _login_mail_allowed(_client_ip(request)):
        return JSONResponse({"error": "Za dużo prób. Spróbuj za godzinę."}, status_code=429)
    token = accounts.request_login(email)
    if token is None:
        return JSONResponse({"ok": True})
    base = SITE_URL or str(request.base_url).rstrip("/")
    try:
        send_login_link(email, f"{base}/#/zaloguj/{token}")
    except (MailUnavailable, OSError) as e:
        logger.error(f"wysyłka linku logowania nie powiodła się: {e}")
        return JSONResponse({"error": "Nie udało się wysłać e-maila. Spróbuj za chwilę."}, status_code=502)
    return JSONResponse({"ok": True})


def _set_session(response: Response, request: Request, session: str) -> None:
    response.set_cookie(SESSION_COOKIE, session, max_age=SESSION_TTL, httponly=True, samesite="lax",
                        secure=request.url.scheme == "https", path="/")


def api_login_verify(request: Request, body: dict) -> Response:
    session = accounts.verify_login(str(body.get("token") or ""))
    if session is None:
        return JSONResponse({"error": "Link wygasł albo został już użyty. Poproś o nowy."}, status_code=400)
    response = JSONResponse({"user": _me(accounts.user_for_session(session))})
    _set_session(response, request, session)
    return response


def api_logout(request: Request, body: dict) -> Response:
    if _session(request):
        accounts.logout(_session(request))
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


def api_me(request: Request) -> Response:
    user = _user(request)
    return JSONResponse({"user": _me(user) if user else None}, headers={"Cache-Control": "no-store"})


def api_me_delete(request: Request, body: dict) -> Response:
    user = _user(request)
    if denied := _denied(user, "login"):
        return denied
    accounts.delete_user(user["id"])
    response = JSONResponse({"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


async def api_pro_search(request: Request) -> Response:
    user = await asyncio.to_thread(_user, request)
    if denied := _denied(user, "pro"):
        return denied
    data = await _read_upload(request)
    if isinstance(data, Response):
        return data
    if data and not data.startswith((b"%PDF", b"PK")):
        return JSONResponse({"error": "Obsługujemy CV w PDF albo DOCX."}, status_code=400)
    me = await asyncio.to_thread(_me, user)
    with searches_lock:
        running = running_pro.get(user["id"])
    if running:
        return JSONResponse({"id": running})
    if not data and not me["cv"]:
        return JSONResponse({"error": "Najpierw dodaj swoje CV."}, status_code=400)
    if me["searches_left"] == 0:
        return JSONResponse({"error": f"Dziś wykorzystano już {PRO_DAILY} wyszukiwania. Wróć jutro."},
                            status_code=429)
    with limits_lock:
        if _daily_full(time.time()):
            return JSONResponse({"error": "Serwis jest dziś bardzo oblegany. Wróć jutro."}, status_code=429)
        started_all.append(time.time())
    search_id = _new_search("pro", user["id"])
    executor.submit(_run_pro, search_id, user["id"], data or None)
    return JSONResponse({"id": search_id})


def api_pro_offers(request: Request) -> Response:
    user = _user(request)
    if denied := _denied(user, "pro"):
        return denied
    return JSONResponse({"offers": accounts.offers(user["id"]), "user": _me(user)},
                        headers={"Cache-Control": "no-store"})


def api_pro_decision(request: Request, body: dict) -> Response:
    user = _user(request)
    if denied := _denied(user, "pro"):
        return denied
    link, status = body.get("link"), body.get("status")
    if not isinstance(link, str) or not link or status not in ("save", "reject", None):
        return JSONResponse({"error": "Pola 'link' i 'status' (save, reject, null) są wymagane"}, status_code=400)
    accounts.decide(user["id"], link, status)
    return JSONResponse({"ok": True})


def _waitlist() -> list[dict]:
    path = DATA_DIR / "waitlist.db"
    if not path.exists():
        return []
    with closing(sqlite3.connect(path, timeout=30)) as conn:
        return [{"email": e, "created_at": c} for e, c in
                conn.execute("SELECT email, created_at FROM waitlist ORDER BY created_at DESC")]


def _scrapers(status: dict) -> list[dict]:
    rows = []
    for key, entry in sorted(status.items()):
        name, _, scope = key.partition("|")
        history = [h.get("count", 0) for h in entry.get("history") or []]
        rows.append({
            "name": name, "scope": "cała Polska" if scope == "all" else "kandydat",
            "last_run": entry.get("timestamp") or entry.get("last_run_date"), "status": entry.get("status"),
            "count": entry.get("jobs_count"), "median": statistics.median(history) if history else None,
        })
    return rows


def _connections(meta: dict, snapshot: str | None, error: str | None) -> list[dict]:
    llm = config.LLM
    mail = mail_mode()
    published = meta.get("published_at")
    age_days = (datetime.now() - datetime.fromisoformat(published)).days if published else None
    return [
        {"name": "Baza ofert", "ok": error is None and age_days is not None and age_days <= 2,
         "detail": error or (f"{snapshot}, opublikowana {published.replace('T', ' ')}" if published else
                             f"{snapshot}, bez metadanych (stara kopia)")},
        {"name": "Jev (TypeSafe)", "ok": bool(jev.api_key()),
         "detail": "klucz ustawiony" if jev.api_key() else "brak TYPESAFE_API_KEY"},
        {"name": f"Odczyt CV ({llm.provider.label})", "ok": not llm.error,
         "detail": llm.error or f"{len(llm.api_keys)} klucz(e), model {llm.models[0]}"},
        {"name": "Wysyłka e-maili", "ok": mail == "smtp",
         "detail": {"smtp": f"SMTP {os.environ.get('PUBLIC_SMTP_HOST', '')}",
                    "log": "tryb testowy: linki logowania tylko w dzienniku serwera",
                    "off": "brak PUBLIC_SMTP_HOST / PUBLIC_SMTP_PASSWORD, logowanie nie działa"}[mail]},
        {"name": "Dane prowadzącego", "ok": bool(LEGAL["operator"] and LEGAL["email"]),
         "detail": "ustawione" if LEGAL["operator"] and LEGAL["email"] else
         "brak PUBLIC_OPERATOR_NAME / PUBLIC_OPERATOR_EMAIL"},
        {"name": "Płatności", "ok": False, "detail": "niepodłączone, plan Pro nadaje admin"},
    ]


def api_admin(request: Request) -> Response:
    user = _user(request)
    if denied := _denied(user, "admin"):
        return denied
    meta, snapshot, total, error = {}, None, None, None
    try:
        index = current_index()
        meta, snapshot, total = index.meta(), index.path.name, index.total
    except RuntimeError as e:
        error = str(e)
    now = time.time()
    with limits_lock:
        _daily_full(now)
        used_today = len(started_all)
    return JSONResponse({
        "offers": {"snapshot": snapshot, "published_at": meta.get("published_at"), "total": total,
                   "sources": meta.get("sources") or []},
        "scrapers": _scrapers(meta.get("scrapers") or {}),
        "users": accounts.users(),
        "searches": {"day": accounts.search_stats(now - DAY), "week": accounts.search_stats(now - 7 * DAY)},
        "usd_per_mtok": JEV_USD_PER_MTOK,
        "limits": {"daily": DAILY_LIMIT, "used_today": used_today, "pro_scored": PRO_SCORED,
                   "pro_daily": PRO_DAILY},
        "connections": _connections(meta, snapshot, error),
        "waitlist": _waitlist(),
    }, headers={"Cache-Control": "no-store"})


def api_admin_plan(request: Request, body: dict) -> Response:
    user = _user(request)
    if denied := _denied(user, "admin"):
        return denied
    user_id, plan = body.get("user_id"), body.get("plan")
    if not isinstance(user_id, int) or plan not in PLANS:
        return JSONResponse({"error": "Pola 'user_id' i 'plan' (free, pro) są wymagane"}, status_code=400)
    if not accounts.set_plan(user_id, plan):
        return JSONResponse({"error": "Nie ma takiego użytkownika."}, status_code=404)
    return JSONResponse({"ok": True})


def api_logo(request: Request) -> Response:
    url = request.query_params.get("src", "")
    if not url or not current_index().has_logo(url):
        return Response(status_code=404)
    png = logo_png(url)
    if png is None:
        return Response(status_code=404, headers={"Cache-Control": "no-cache"})
    etag = '"' + hashlib.sha1(png).hexdigest()[:16] + '"'
    headers = {"Cache-Control": "public, max-age=86400", "ETag": etag}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(png, media_type="image/png", headers=headers)


def spa(request: Request) -> Response:
    if request.url.path.startswith("/api/"):
        return JSONResponse({"error": "Not found"}, status_code=404)
    page = DIST_DIR / "public.html"
    if not page.exists():
        return Response("Brak builda frontendu: npm run build:public", status_code=503)
    return FileResponse(page, headers={"Cache-Control": "no-cache"})


routes = [
    Route("/api/public/legal", api_legal, methods=["GET"]),
    Route("/api/public/search", api_search_start, methods=["POST"]),
    Route("/api/public/search/{search_id}", api_search_state, methods=["GET"]),
    Route("/api/public/auth/request", json_endpoint(api_login_request), methods=["POST"]),
    Route("/api/public/auth/verify", json_endpoint(api_login_verify), methods=["POST"]),
    Route("/api/public/auth/logout", json_endpoint(api_logout), methods=["POST"]),
    Route("/api/public/me", api_me, methods=["GET"]),
    Route("/api/public/me/delete", json_endpoint(api_me_delete), methods=["POST"]),
    Route("/api/public/pro/search", api_pro_search, methods=["POST"]),
    Route("/api/public/pro/offers", api_pro_offers, methods=["GET"]),
    Route("/api/public/pro/decision", json_endpoint(api_pro_decision), methods=["POST"]),
    Route("/api/public/admin", api_admin, methods=["GET"]),
    Route("/api/public/admin/plan", json_endpoint(api_admin_plan), methods=["POST"]),
    Route("/api/public/logo", api_logo, methods=["GET"]),
]
if (DIST_DIR / "assets").exists():
    routes.append(Mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets"))
routes.append(Route("/{path:path}", spa, methods=["GET"]))

app = Starlette(routes=routes, middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)])


if __name__ == "__main__":
    from utils.console import force_utf8
    force_utf8()
    if not (LEGAL["operator"] and LEGAL["email"]):
        logger.warning("Brak PUBLIC_OPERATOR_NAME / PUBLIC_OPERATOR_EMAIL: polityka prywatności bez administratora")
    if mail_mode() == "off":
        logger.warning("Brak PUBLIC_SMTP_HOST / PUBLIC_SMTP_PASSWORD: logowanie nie działa")
    threading.Thread(target=current_index, daemon=True).start()
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8600")),
                proxy_headers=True, forwarded_allow_ips=os.environ.get("PUBLIC_TRUSTED_PROXY", "127.0.0.1"),
                access_log=False)
