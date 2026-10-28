import hashlib
import logging
import os
import random
import re
import secrets
import sqlite3
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
}

index_holder: dict = {"path": None, "index": None}
index_lock = threading.Lock()
executor = ThreadPoolExecutor(WORKERS, thread_name_prefix="search")
searches: dict[str, dict] = {}
searches_lock = threading.Lock()
started_all: deque = deque()
limits_lock = threading.Lock()


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


def _take_slot(ip: str) -> tuple[str | None, str | None, str | None]:
    now = time.time()
    with limits_lock:
        while started_all and now - started_all[0] > DAY:
            started_all.popleft()
        if len(started_all) >= DAILY_LIMIT:
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


def _update(search_id: str, **fields) -> None:
    with searches_lock:
        search = searches.get(search_id)
        if search is not None:
            search.update(fields)


def _format_pick(pick: dict) -> dict:
    modes = [WORK_MODE_LABELS[m] for m in pick.get("work_modes") or [] if m in WORK_MODE_LABELS]
    return {
        "link": pick["link"], "title": pick["title"], "company": pick["company"],
        "location": pick.get("location") or "", "source": pick.get("source") or "",
        "salary": salary_text(pick.get("salary")), "work_mode": ", ".join(modes),
        "logo_url": pick.get("logo_url"), "percent": pick["percent"],
    }


def _run_search(search_id: str, data: bytes, ip_hash: str | None) -> None:
    try:
        _update(search_id, status="reading")
        text = _cv_text(data)
        del data
        _update(search_id, status="profile")
        profile = build_profile(text)
        if not profile["is_cv"] or not profile["roles"]:
            raise UserError("To nie wygląda na CV. Wgraj swoje CV w PDF albo DOCX.")
        _update(search_id, profile={
            "city": profile.get("city"), "seniority": profile.get("seniority"),
            "roles": (profile.get("roles") or [])[:3],
        })
        key = jev.api_key()
        if not key:
            raise RuntimeError("Brak TYPESAFE_API_KEY")

        def progress(stage, **counts):
            with searches_lock:
                search = searches.get(search_id)
                if search is not None:
                    search["status"] = stage
                    search["progress"].update(counts)

        result = quick.find_matches(current_index(), profile, text, key, progress=progress)
        picks = [_format_pick(p) for p in result.picks]
        random.shuffle(picks)
        low = min(max(max((p["percent"] for p in picks), default=0) + 3, 86), 95)
        teasers = [random.randint(low, 98) for _ in range(min(3, result.promising))]
        _update(search_id, status="done", finished=time.time(), result={
            "picks": picks, "good": result.good, "teasers": teasers,
        })
        logger.info(f"search {search_id[:8]}: ranked {result.ranked}, triaged {result.triaged}, "
                    f"scored {result.scored}, good {result.good}, errors {result.errors}")
    except UserError as e:
        _refund(ip_hash)
        _update(search_id, status="error", error=str(e), finished=time.time())
    except Exception as e:
        _refund(ip_hash)
        logger.exception(f"search {search_id[:8]} failed: {e}")
        _update(search_id, status="error", finished=time.time(),
                error="Coś poszło nie tak po naszej stronie. Spróbuj ponownie za kilka minut.")


def _purge_old() -> None:
    now = time.time()
    with searches_lock:
        for search_id in [s for s, v in searches.items() if now - v["created"] > SEARCH_TTL]:
            del searches[search_id]


async def api_search_start(request: Request) -> Response:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_UPLOAD:
            return JSONResponse({"error": "Plik jest za duży (limit 5 MB)."}, status_code=413)
    if not data.startswith((b"%PDF", b"PK")):
        return JSONResponse({"error": "Obsługujemy CV w PDF albo DOCX."}, status_code=400)
    code, refusal, ip_hash = _take_slot(_client_ip(request))
    if code:
        return JSONResponse({"error": refusal, "code": code}, status_code=429)
    _purge_old()
    search_id = secrets.token_urlsafe(18)
    with searches_lock:
        searches[search_id] = {"created": time.time(), "status": "queued", "progress": {},
                               "profile": None, "result": None, "error": None}
    executor.submit(_run_search, search_id, bytes(data), ip_hash)
    return JSONResponse({"id": search_id})


def api_search_state(request: Request) -> Response:
    search_id = request.path_params["search_id"]
    with searches_lock:
        search = searches.get(search_id)
        if search is None:
            return JSONResponse({"error": "Nie ma takiego wyszukiwania albo wygasło."}, status_code=404)
        ahead = sum(1 for v in searches.values() if v["status"] == "queued" and v["created"] < search["created"])
        body = {k: search[k] for k in ("status", "progress", "profile", "result", "error")}
    body["queue"] = ahead if body["status"] == "queued" else 0
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


def api_legal(request: Request) -> Response:
    return JSONResponse(LEGAL)


def _waitlist_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA_DIR / "waitlist.db", timeout=30)
    conn.execute("CREATE TABLE IF NOT EXISTS waitlist (email TEXT PRIMARY KEY, created_at TEXT NOT NULL)")
    return conn


async def api_waitlist(request: Request) -> Response:
    try:
        body = await request.json()
    except ValueError:
        body = None
    email = str((body or {}).get("email") or "").strip().lower()
    if not EMAIL_RE.match(email):
        return JSONResponse({"error": "Podaj poprawny adres e-mail."}, status_code=400)
    with closing(_waitlist_db()) as conn, conn:
        conn.execute("INSERT OR IGNORE INTO waitlist(email, created_at) VALUES (?, ?)",
                     (email, datetime.now().isoformat(timespec="seconds")))
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
    Route("/api/public/waitlist", api_waitlist, methods=["POST"]),
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
    threading.Thread(target=current_index, daemon=True).start()
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8600")),
                proxy_headers=True, forwarded_allow_ips=os.environ.get("PUBLIC_TRUSTED_PROXY", "127.0.0.1"),
                access_log=False)
