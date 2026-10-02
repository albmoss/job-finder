from __future__ import annotations

import copy
from typing import Any

from cv_tailor import jpath

CHG_OPEN, CHG_CLOSE, NUM_OPEN, NUM_CLOSE = "\x01", "\x02", "\x03", "\x04"


class ReviewError(Exception):
    pass


def _change_at(v: dict, path: str) -> dict | None:
    return next((c for c in v.get("changes") or [] if c["path"] == path and c["kind"] != "removed"), None)


def _revert_value(v: dict, c: dict) -> Any:
    current = jpath.get(v["tailored"], c["path"])
    if isinstance(current, str) and c["before"]:
        return c["before"]
    base_value = jpath.get(v["base"], c["path"])
    if base_value is not None and type(base_value) is type(current):
        return copy.deepcopy(base_value)
    return current


def _restore_value(v: dict, c: dict) -> Any:
    base_value = jpath.get(v["base"], c["path"])
    parent = jpath.get(v["tailored"], jpath.parse(c["path"])[:-1])
    sample = parent[0] if isinstance(parent, list) and parent else None
    if base_value is not None and (not isinstance(base_value, str) or base_value == c["before"]):
        return copy.deepcopy(base_value)
    if sample is None or isinstance(sample, str):
        return c["before"]
    return None


def _number_removed_value(v: dict, path: str) -> tuple[str, Any]:
    c = _change_at(v, path)
    if c and c["kind"] == "added":
        return "drop", None
    if c and c["kind"] == "modified" and c["before"]:
        return "set", c["before"]
    base_value = jpath.get(v["base"], path)
    if isinstance(base_value, str):
        return "set", base_value
    return ("drop", None) if jpath.is_item(path) else ("keep", None)


def _wrap(value: Any, open_: str, close: str) -> Any:
    if isinstance(value, str):
        return f"{open_}{value}{close}" if value else value
    if isinstance(value, list):
        return [_wrap(x, open_, close) for x in value]
    if isinstance(value, dict):
        return {k: _wrap(x, open_, close) for k, x in value.items()}
    return value


def _overlay(v: dict) -> tuple[dict, list[str], list[tuple[str, Any]], list[str]]:
    cv = copy.deepcopy(v["tailored"])
    overrides: dict[str, Any] = {}
    drops: list[str] = []
    restores: list[tuple[str, Any]] = []
    marked: list[str] = []
    for c in v.get("changes") or []:
        path, kind = c["path"], c["kind"]
        if c["state"] == "reverted":
            if kind == "modified":
                overrides[path] = _revert_value(v, c)
            elif kind == "added":
                drops.append(path)
            else:
                value = _restore_value(v, c)
                if value is not None:
                    restores.append((path, value))
        elif kind != "removed":
            if c["state"] == "edited":
                overrides[path] = c["after"]
            marked.append(path)
    for n in v.get("numbers") or []:
        if n["state"] == "edited":
            overrides[n["path"]] = n["text"]
        elif n["state"] == "removed":
            action, value = _number_removed_value(v, n["path"])
            if action == "drop":
                drops.append(n["path"])
            elif action == "set":
                overrides[n["path"]] = value
    for path, value in overrides.items():
        jpath.set_(cv, path, value)
    return cv, drops, restores, marked


def number_text(v: dict, n: dict, overlay: dict | None = None) -> str:
    if n["state"] == "edited":
        return n["text"]
    doc = overlay if overlay is not None else _overlay(v)[0]
    value = jpath.get(doc, n["path"])
    return value if isinstance(value, str) else ""


def effective_cv(v: dict, which: str = "tailored", marks: bool = False) -> dict:
    if which == "base":
        return copy.deepcopy(v["base"])
    cv, drops, restores, marked = _overlay(v)
    if marks:
        for n in v.get("numbers") or []:
            if n["state"] != "pending":
                continue
            text = jpath.get(cv, n["path"])
            if not isinstance(text, str) or not text:
                continue
            number = n["number"]
            pos = text.find(number) if number else -1
            if pos >= 0:
                text = f"{text[:pos]}{NUM_OPEN}{number}{NUM_CLOSE}{text[pos + len(number):]}"
            else:
                text = f"{NUM_OPEN}{text}{NUM_CLOSE}"
            jpath.set_(cv, n["path"], text)
        for path in marked:
            c = _change_at(v, path)
            value = jpath.get(cv, path)
            if c is None or value is None:
                continue
            plain = value.translate({ord(NUM_OPEN): None, ord(NUM_CLOSE): None}) if isinstance(value, str) else value
            if isinstance(plain, str) and plain == c["before"]:
                continue
            jpath.set_(cv, path, _wrap(value, CHG_OPEN, CHG_CLOSE))
    jpath.delete_many(cv, drops)
    for path, value in sorted(restores, key=lambda r: jpath.parse(r[0])[-1]):
        jpath.insert(cv, path, value)
    return cv


def _editable(v: dict) -> None:
    if v["status"] not in ("draft", "ready"):
        raise ReviewError("Tę wersję można sprawdzać dopiero po przygotowaniu szkicu.")


def _pick(items: list[dict], idx: Any) -> dict:
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        raise ReviewError("Pole 'idx' musi być liczbą.") from None
    item = next((x for x in items if x["idx"] == idx), None)
    if item is None:
        raise ReviewError("Nie ma takiej pozycji.")
    return item


def apply_change(v: dict, idx: Any, action: str, text: str | None) -> None:
    _editable(v)
    c = _pick(v["changes"], idx)
    if action == "revert":
        c["state"] = "reverted"
        c["after"] = c["model_after"]
    elif action == "apply":
        c["state"] = "applied"
        c["after"] = c["model_after"]
    elif action == "edit":
        text = (text or "").strip()
        if not text:
            raise ReviewError("Podaj nowy tekst.")
        if c["kind"] == "removed" or not isinstance(jpath.get(v["tailored"], c["path"]), str):
            raise ReviewError("Tej zmiany nie można poprawić tekstem. Cofnij ją albo zostaw.")
        c["state"] = "edited"
        c["after"] = text
    else:
        raise ReviewError("Nieznana akcja.")
    v["status"] = "draft"


def apply_number(v: dict, idx: Any, action: str, text: str | None) -> None:
    _editable(v)
    n = _pick(v["numbers"], idx)
    if action == "confirm":
        n["state"] = "confirmed"
    elif action == "edit":
        text = (text or "").strip()
        if not text:
            raise ReviewError("Podaj poprawiony tekst.")
        n["state"] = "edited"
        n["text"] = text
    elif action == "remove":
        n["state"] = "removed"
    else:
        raise ReviewError("Nieznana akcja.")
    v["status"] = "draft"


CHANGE_KEYS = ("idx", "path", "section", "before", "after", "reason", "state")
NUMBER_KEYS = ("idx", "path", "number", "question", "state")


def detail(v: dict, summary: dict) -> dict:
    overlay = _overlay(v)[0] if v.get("tailored") else None
    numbers = []
    for n in v.get("numbers") or []:
        item = {k: n[k] for k in NUMBER_KEYS}
        item["text"] = number_text(v, n, overlay) if overlay is not None else n.get("text", "")
        numbers.append(item)
    return {
        **summary,
        "facts": v.get("facts") or "",
        "lock_contact": bool(v.get("lock_contact")),
        "keep_order": bool(v.get("keep_order")),
        "changes": [{k: c[k] for k in CHANGE_KEYS} for c in v.get("changes") or []],
        "numbers": numbers,
        "questions": v.get("questions") or [],
        "missing": v.get("missing") or [],
        "match": v.get("match"),
    }
