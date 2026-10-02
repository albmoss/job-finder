from __future__ import annotations

import html
import re
from pathlib import Path

from cv_tailor.review import CHG_CLOSE, CHG_OPEN, NUM_CLOSE, NUM_OPEN

TEMPLATE_PATH = Path(__file__).resolve().parent / "template.html"
DEFAULT_ORDER = ("summary", "experience", "projects", "skills", "education", "languages", "certificates", "other")
HEADINGS = {
    "pl": {"summary": "Profil", "experience": "Doświadczenie", "projects": "Projekty", "skills": "Umiejętności",
           "education": "Wykształcenie", "languages": "Języki", "certificates": "Certyfikaty"},
    "en": {"summary": "Profile", "experience": "Experience", "projects": "Projects", "skills": "Skills",
           "education": "Education", "languages": "Languages", "certificates": "Certificates"},
}
_MARKUP = {CHG_OPEN: '<mark class="chg">', CHG_CLOSE: "</mark>", NUM_OPEN: '<mark class="num">', NUM_CLOSE: "</mark>"}
_MARKUP_RE = re.compile("|".join(map(re.escape, _MARKUP)))


def _t(text) -> str:
    return _MARKUP_RE.sub(lambda m: _MARKUP[m.group(0)], html.escape(str(text or "")))


def _plain(text) -> str:
    return _MARKUP_RE.sub("", str(text or ""))


def _span(text) -> str:
    return f"<span>{_t(text)}</span>" if _plain(text).strip() else ""


def _dates(start, end) -> str:
    if _plain(start) and _plain(end):
        return f"{_t(start)} – {_t(end)}"
    return _t(start) or _t(end)


def _bullets(items) -> str:
    rows = "".join(f"<li>{_t(b)}</li>" for b in items or [] if _plain(b).strip())
    return f"<ul>{rows}</ul>" if rows else ""


def _link_label(url) -> str:
    return re.sub(r"^https?://(www\.)?", "", str(url or "")).rstrip("/")


def _section(heading: str, inner: str) -> str:
    return f"<section><h2>{_t(heading)}</h2>{inner}</section>" if inner.strip() else ""


def _entry(title: str, meta: str, sub: str, body: str) -> str:
    head = f'<div class="entry-head"><span class="entry-title">{title}</span>'
    head += f'<span class="entry-meta">{meta}</span></div>' if meta else "</div>"
    sub_html = f'<div class="entry-sub">{sub}</div>' if sub else ""
    return f'<div class="entry">{head}{sub_html}{body}</div>'


def _render_section(key: str, cv: dict, headings: dict) -> str:
    if key == "summary":
        return _section(headings[key], f"<p>{_t(cv['summary'])}</p>" if _plain(cv["summary"]).strip() else "")
    if key == "experience":
        rows = []
        for e in cv["experience"]:
            title = " · ".join(_t(x) for x in (e["title"], e["company"]) if _plain(x).strip())
            rows.append(_entry(title, _dates(e["start"], e["end"]), _t(e["location"]), _bullets(e["bullets"])))
        return _section(headings[key], "".join(rows))
    if key == "projects":
        rows = []
        for p in cv["projects"]:
            body = f"<p>{_t(p['description'])}</p>" if _plain(p["description"]).strip() else ""
            rows.append(_entry(_t(p["name"]), _t(_link_label(p["link"])) if p["link"] else "", "",
                               body + _bullets(p["bullets"])))
        return _section(headings[key], "".join(rows))
    if key == "skills":
        rows = []
        for g in cv["skills"]:
            items = ", ".join(_t(i) for i in g["items"] if _plain(i).strip())
            if not items:
                continue
            label = f'<span class="label">{_t(g["category"])}:</span> ' if _plain(g["category"]).strip() else ""
            rows.append(f'<div class="skills-row">{label}{items}</div>')
        return _section(headings[key], "".join(rows))
    if key == "education":
        rows = []
        for e in cv["education"]:
            title = " · ".join(_t(x) for x in (e["degree"], e["school"]) if _plain(x).strip())
            rows.append(_entry(title, _dates(e["start"], e["end"]), _t(e["location"]), _bullets(e["details"])))
        return _section(headings[key], "".join(rows))
    if key == "languages":
        items = []
        for lang in cv["languages"]:
            if not _plain(lang["name"]).strip():
                continue
            level = f" ({_t(lang['level'])})" if _plain(lang["level"]).strip() else ""
            items.append(f"{_t(lang['name'])}{level}")
        return _section(headings[key], f"<p>{', '.join(items)}</p>" if items else "")
    if key == "certificates":
        rows = []
        for c in cv["certificates"]:
            sub = ", ".join(_t(x) for x in (c["issuer"], c["date"]) if _plain(x).strip())
            rows.append(f"<p><span class=\"label\">{_t(c['name'])}</span>{' · ' + sub if sub else ''}</p>")
        return _section(headings[key], "".join(rows))
    if key == "other":
        return "".join(_section(_plain(o["heading"]), _bullets(o["items"])) for o in cv["other"])
    return ""


def render_html(cv: dict, language: str, order: list[str] | None = None, title: str = "CV") -> str:
    headings = HEADINGS.get(language, HEADINGS["pl"])
    keys = [k for k in order or [] if k in DEFAULT_ORDER]
    keys += [k for k in DEFAULT_ORDER if k not in keys]
    contact = cv["contact"]
    contact_line = "".join(_span(x) for x in [contact["city"], contact["email"], contact["phone"]])
    contact_line += "".join(_span(_link_label(u)) for u in contact["links"])
    parts = [f"<header><h1>{_t(cv['name'])}</h1>"]
    if _plain(cv["headline"]).strip():
        parts.append(f'<div class="headline">{_t(cv["headline"])}</div>')
    if contact_line:
        parts.append(f'<div class="contact">{contact_line}</div>')
    parts.append("</header>")
    parts += [_render_section(k, cv, headings) for k in keys]
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return (template.replace("{{lang}}", "en" if language == "en" else "pl")
            .replace("{{title}}", html.escape(_plain(title)))
            .replace("{{body}}", "\n".join(p for p in parts if p)))


def render_pdf(html_text: str, target: Path) -> int:
    from playwright.sync_api import sync_playwright

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(html_text, wait_until="load")
            page.wait_for_selector("body[data-fit]", timeout=10000)
            page.pdf(path=str(tmp), format="A4", print_background=True, prefer_css_page_size=True,
                     margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
        finally:
            browser.close()
    tmp.replace(target)
    from pypdf import PdfReader
    return len(PdfReader(str(target)).pages)
