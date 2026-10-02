from __future__ import annotations

import re
from typing import Any

_TOKEN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)|\[(\d+)\]")
_MISSING = object()


def parse(path: str) -> list[str | int] | None:
    path = (path or "").strip().lstrip("$").lstrip(".")
    if not path:
        return None
    out: list[str | int] = []
    pos = 0
    while pos < len(path):
        if path[pos] == "." and out:
            pos += 1
        m = _TOKEN.match(path, pos)
        if not m:
            return None
        out.append(m.group(1) if m.group(1) is not None else int(m.group(2)))
        pos = m.end()
    return out


def fmt(parts: list[str | int]) -> str:
    out = ""
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else p)
    return out


def _step(node: Any, part: str | int) -> Any:
    if isinstance(part, int):
        return node[part] if isinstance(node, list) and 0 <= part < len(node) else _MISSING
    return node[part] if isinstance(node, dict) and part in node else _MISSING


def get(doc: Any, path: str | list, default: Any = None) -> Any:
    parts = parse(path) if isinstance(path, str) else path
    if not parts:
        return default
    node = doc
    for part in parts:
        node = _step(node, part)
        if node is _MISSING:
            return default
    return node


def exists(doc: Any, path: str | list) -> bool:
    return get(doc, path, _MISSING) is not _MISSING


def set_(doc: Any, path: str | list, value: Any) -> bool:
    parts = parse(path) if isinstance(path, str) else path
    if not parts:
        return False
    parent = get(doc, parts[:-1]) if len(parts) > 1 else doc
    last = parts[-1]
    if isinstance(last, int) and isinstance(parent, list) and 0 <= last < len(parent):
        parent[last] = value
        return True
    if isinstance(last, str) and isinstance(parent, dict) and last in parent:
        parent[last] = value
        return True
    return False


def is_item(path: str | list) -> bool:
    parts = parse(path) if isinstance(path, str) else path
    return bool(parts) and isinstance(parts[-1], int)


def delete_many(doc: Any, paths: list[str]) -> None:
    items = []
    for path in paths:
        parts = parse(path)
        if parts and isinstance(parts[-1], int):
            items.append(parts)
    for parts in sorted(items, key=lambda p: [(1, x) if isinstance(x, int) else (0, x) for x in p], reverse=True):
        parent = get(doc, parts[:-1]) if len(parts) > 1 else doc
        if isinstance(parent, list) and 0 <= parts[-1] < len(parent):
            del parent[parts[-1]]


def insert(doc: Any, path: str, value: Any) -> bool:
    parts = parse(path)
    if not parts or not isinstance(parts[-1], int):
        return False
    parent = get(doc, parts[:-1]) if len(parts) > 1 else doc
    if not isinstance(parent, list):
        return False
    parent.insert(min(parts[-1], len(parent)), value)
    return True
