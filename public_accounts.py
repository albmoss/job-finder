import hashlib
import json
import logging
import os
import secrets
import smtplib
import sqlite3
import ssl
import time
from contextlib import closing
from email.message import EmailMessage
from pathlib import Path

logger = logging.getLogger("public.accounts")

DAY = 24 * 3600
LOGIN_TTL = 20 * 60
LOGIN_RESEND = 60
SESSION_TTL = 30 * DAY
PLANS = ("free", "pro")
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("PUBLIC_ADMIN_EMAILS", "").split(",") if e.strip()}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    plan TEXT NOT NULL DEFAULT 'free' CHECK (plan IN ('free', 'pro')),
    created_at REAL NOT NULL,
    last_login REAL,
    cv_text TEXT,
    cv_sha TEXT,
    profile TEXT,
    cv_at REAL
);
CREATE TABLE IF NOT EXISTS login_tokens (
    hash TEXT PRIMARY KEY, email TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS matches (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    link TEXT NOT NULL,
    percent INTEGER NOT NULL,
    offer TEXT NOT NULL,
    PRIMARY KEY (user_id, link)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS decisions (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    link TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('save', 'reject')),
    at REAL NOT NULL,
    PRIMARY KEY (user_id, link)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS search_log (
    at REAL NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('guest', 'pro')),
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    ok INTEGER NOT NULL,
    tokens INTEGER NOT NULL,
    offers INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS search_log_at ON search_log (at);
"""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def is_admin(email: str) -> bool:
    return email.lower() in ADMIN_EMAILS


class Accounts:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def request_login(self, email: str) -> str | None:
        now = time.time()
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM login_tokens WHERE expires < ?", (now,))
            if conn.execute("SELECT 1 FROM login_tokens WHERE email = ? AND created > ?",
                            (email, now - LOGIN_RESEND)).fetchone():
                return None
            token = secrets.token_urlsafe(32)
            conn.execute("INSERT INTO login_tokens VALUES (?, ?, ?, ?)", (_hash(token), email, now, now + LOGIN_TTL))
        return token

    def verify_login(self, token: str) -> str | None:
        now = time.time()
        with closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT email FROM login_tokens WHERE hash = ? AND expires >= ?",
                               (_hash(token), now)).fetchone()
            if row is None:
                return None
            if conn.execute("DELETE FROM login_tokens WHERE hash = ?", (_hash(token),)).rowcount != 1:
                return None
            conn.execute("INSERT INTO users(email, created_at) VALUES (?, ?) ON CONFLICT(email) DO NOTHING",
                         (row["email"], now))
            user_id = conn.execute("SELECT id FROM users WHERE email = ?", (row["email"],)).fetchone()["id"]
            conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, user_id))
            conn.execute("DELETE FROM sessions WHERE expires < ?", (now,))
            session = secrets.token_urlsafe(32)
            conn.execute("INSERT INTO sessions VALUES (?, ?, ?)", (_hash(session), user_id, now + SESSION_TTL))
        return session

    def user_for_session(self, session: str) -> dict | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT u.id, u.email, u.plan, u.profile, u.cv_at FROM sessions s JOIN users u ON u.id = s.user_id "
                "WHERE s.hash = ? AND s.expires >= ?", (_hash(session), time.time())).fetchone()
        if row is None:
            return None
        user = dict(row)
        user["profile"] = json.loads(user["profile"]) if user["profile"] else None
        user["admin"] = is_admin(user["email"])
        user["pro"] = user["plan"] == "pro" or user["admin"]
        return user

    def logout(self, session: str) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM sessions WHERE hash = ?", (_hash(session),))

    def delete_user(self, user_id: int) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def cv(self, user_id: int) -> tuple[str, str, dict] | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT cv_text, cv_sha, profile FROM users WHERE id = ?", (user_id,)).fetchone()
        if row is None or not row["cv_text"]:
            return None
        return row["cv_text"], row["cv_sha"], json.loads(row["profile"])

    def set_cv(self, user_id: int, text: str, sha: str, profile: dict) -> None:
        with closing(self._connect()) as conn, conn:
            old = conn.execute("SELECT cv_sha FROM users WHERE id = ?", (user_id,)).fetchone()
            if old and old["cv_sha"] != sha:
                conn.execute("DELETE FROM matches WHERE user_id = ?", (user_id,))
            conn.execute("UPDATE users SET cv_text = ?, cv_sha = ?, profile = ?, cv_at = ? WHERE id = ?",
                         (text, sha, json.dumps(profile, ensure_ascii=False), time.time(), user_id))

    def known_percents(self, user_id: int) -> dict[str, int]:
        with closing(self._connect()) as conn:
            return dict(conn.execute("SELECT link, percent FROM matches WHERE user_id = ?", (user_id,)).fetchall())

    def replace_matches(self, user_id: int, picks: list[dict]) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM matches WHERE user_id = ?", (user_id,))
            conn.executemany("INSERT INTO matches VALUES (?, ?, ?, ?)", [
                (user_id, p["link"], p["percent"], json.dumps(p, ensure_ascii=False)) for p in picks])

    def offers(self, user_id: int) -> list[dict]:
        with closing(self._connect()) as conn:
            matched = {row["link"]: json.loads(row["offer"]) for row in conn.execute(
                "SELECT link, offer FROM matches WHERE user_id = ? ORDER BY percent DESC", (user_id,))}
            decided = {row["link"]: row["status"] for row in conn.execute(
                "SELECT link, status FROM decisions WHERE user_id = ?", (user_id,))}
        return [{**offer, "decision": decided.get(link)} for link, offer in matched.items()]

    def decide(self, user_id: int, link: str, status: str | None) -> None:
        with closing(self._connect()) as conn, conn:
            if status is None:
                conn.execute("DELETE FROM decisions WHERE user_id = ? AND link = ?", (user_id, link))
            else:
                conn.execute("INSERT INTO decisions VALUES (?, ?, ?, ?) ON CONFLICT(user_id, link) "
                             "DO UPDATE SET status = excluded.status, at = excluded.at",
                             (user_id, link, status, time.time()))

    def log_search(self, kind: str, user_id: int | None, ok: bool, tokens: int, offers: int) -> None:
        with closing(self._connect()) as conn, conn:
            conn.execute("INSERT INTO search_log VALUES (?, ?, ?, ?, ?, ?)",
                         (time.time(), kind, user_id, int(ok), tokens, offers))

    def pro_searches_today(self, user_id: int) -> int:
        with closing(self._connect()) as conn:
            return conn.execute("SELECT COUNT(*) FROM search_log WHERE kind = 'pro' AND user_id = ? AND ok = 1 "
                                "AND at > ?", (user_id, time.time() - DAY)).fetchone()[0]

    def set_plan(self, user_id: int, plan: str) -> bool:
        with closing(self._connect()) as conn, conn:
            return conn.execute("UPDATE users SET plan = ? WHERE id = ?", (plan, user_id)).rowcount == 1

    def users(self) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT u.id, u.email, u.plan, u.created_at, u.last_login, u.cv_at IS NOT NULL AS has_cv, "
                "(SELECT COUNT(*) FROM matches m WHERE m.user_id = u.id) AS matches, "
                "(SELECT COUNT(*) FROM search_log l WHERE l.user_id = u.id AND l.kind = 'pro') AS pro_searches "
                "FROM users u ORDER BY u.created_at DESC").fetchall()
        return [{**dict(r), "has_cv": bool(r["has_cv"]), "admin": is_admin(r["email"])} for r in rows]

    def search_stats(self, since: float) -> dict[str, dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT kind, COUNT(*) AS total, SUM(ok) AS ok, SUM(tokens) AS tokens FROM search_log "
                "WHERE at > ? GROUP BY kind", (since,)).fetchall()
        stats = {kind: {"total": 0, "ok": 0, "tokens": 0} for kind in ("guest", "pro")}
        for r in rows:
            stats[r["kind"]] = {"total": r["total"], "ok": r["ok"] or 0, "tokens": r["tokens"] or 0}
        return stats


class MailUnavailable(Exception):
    pass


def mail_mode() -> str:
    if os.environ.get("PUBLIC_MAIL_LOG", "").strip() == "1":
        return "log"
    if os.environ.get("PUBLIC_SMTP_HOST", "").strip() and os.environ.get("PUBLIC_SMTP_PASSWORD", "").strip():
        return "smtp"
    return "off"


def send_login_link(email: str, url: str) -> None:
    mode = mail_mode()
    if mode == "log":
        logger.warning(f"PUBLIC_MAIL_LOG: link logowania dla {email}: {url}")
        return
    if mode == "off":
        raise MailUnavailable("Brak PUBLIC_SMTP_HOST / PUBLIC_SMTP_PASSWORD")
    user = os.environ.get("PUBLIC_SMTP_USER", "").strip()
    sender = os.environ.get("PUBLIC_MAIL_FROM", "").strip() or user
    msg = EmailMessage()
    msg["Subject"] = "Logowanie do traffy.pl"
    msg["From"] = sender
    msg["To"] = email
    msg.set_content(
        "Kliknij link, żeby zalogować się do traffy.pl:\n\n"
        f"{url}\n\n"
        f"Link działa {LOGIN_TTL // 60} minut i tylko raz. Jeśli to nie Ty prosiłeś o logowanie, zignoruj tę wiadomość.\n")
    host = os.environ["PUBLIC_SMTP_HOST"].strip()
    port = int(os.environ.get("PUBLIC_SMTP_PORT", "465"))
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=20) as smtp:
            smtp.login(user, os.environ["PUBLIC_SMTP_PASSWORD"])
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.starttls(context=context)
            smtp.login(user, os.environ["PUBLIC_SMTP_PASSWORD"])
            smtp.send_message(msg)
