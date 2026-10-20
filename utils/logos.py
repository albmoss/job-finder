import hashlib
import io
import logging
import os
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from ui_theme import TOKENS

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / "logo_cache"
VERSION = 1
MAX_BYTES = 3_000_000
WORK_SIDE = 400
OUT_SIDE = 128
RETRY_FAILED_S = 24 * 3600
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0 Safari/537.36")
INK = tuple(int(TOKENS["bg"][i:i + 2], 16) for i in (1, 3, 5))
LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

_guard = threading.Lock()
_locks: dict = {}


def _dominant(px: np.ndarray, step: float = 1 / 8, tol: float = 0.15):
    q = np.round(px / step).astype(np.int16)
    _, inv, counts = np.unique(q, axis=0, return_inverse=True, return_counts=True)
    mode = px[inv.reshape(-1) == counts.argmax()].mean(0)
    return mode, float((np.sqrt(((px - mode) ** 2).sum(-1)) < tol).mean())


def _inside(solid: np.ndarray) -> np.ndarray:
    mask = Image.fromarray(np.pad(solid, 1).astype(np.uint8) * 255, "L").copy()
    ImageDraw.floodfill(mask, (0, 0), 128)
    filled = Image.fromarray(((np.asarray(mask) != 128)[1:-1, 1:-1]).astype(np.uint8) * 255, "L")
    return np.asarray(filled.filter(ImageFilter.MinFilter(3))) > 0


def _ring(a: np.ndarray) -> np.ndarray:
    return np.concatenate([a[:2].reshape(-1, 4), a[-2:].reshape(-1, 4),
                           a[:, :2].reshape(-1, 4), a[:, -2:].reshape(-1, 4)])


def _box(mask: np.ndarray, threshold: float, margin: float):
    ys, xs = np.where(mask > threshold)
    if len(xs) == 0:
        return None
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    m = int(round(max(x1 - x0, y1 - y0) * margin))
    h, w = mask.shape
    return max(0, y0 - m), min(h, y1 + m), max(0, x0 - m), min(w, x1 + m)


def _crop(shape: np.ndarray, *layers: np.ndarray):
    box = _box(shape, 0.3, 0.03)
    if box is None:
        return None
    y0, y1, x0, x1 = box
    return [layer[y0:y1, x0:x1] for layer in (shape,) + layers]


def render(data: bytes) -> Optional[bytes]:
    img = Image.open(io.BytesIO(data))
    img.seek(0)
    img = ImageOps.exif_transpose(img.convert("RGBA"))
    img.thumbnail((WORK_SIDE, WORK_SIDE), Image.LANCZOS)
    a = np.asarray(img).astype(np.float32) / 255.0
    rgb, alpha = a[..., :3], a[..., 3]
    ring = _ring(a)

    transparent = (ring[:, 3] < 0.1).mean() > 0.5
    flat_bg = dark_bg = False
    if transparent:
        ink = alpha.copy()
    else:
        bg, share = _dominant(ring[:, :3])
        flat_bg = share >= 0.55
        if flat_bg and float(bg @ LUMA) <= 0.8:
            dark_bg = True
            ink = np.clip((np.abs(rgb @ LUMA - float(bg @ LUMA)) - 0.2) / 0.25, 0, 1) * alpha
        elif flat_bg:
            ink = np.clip((np.sqrt(((rgb - bg) ** 2).sum(-1)) - 0.06) / 0.2, 0, 1) * alpha
        else:
            ink = alpha.copy()

    cropped = _crop(ink, rgb)
    if cropped is None:
        return None
    cink, crgb = cropped
    solid = cink > 0.5
    inner = crgb[solid]
    gray = None
    shape = cink
    if solid.mean() > 0.6 and len(inner) and not dark_bg:
        dom, share = _dominant(inner)
        if share > 0.35:
            inside = _inside(solid)
            d2 = np.sqrt(((crgb - dom) ** 2).sum(-1))
            knock = np.clip((d2 - 0.08) / 0.2, 0, 1) * inside
            detail = (knock[inside] > 0.5).mean() if inside.any() else 0.0
            if 0.03 < detail < 0.6:
                shape = knock
        else:
            gray = crgb @ LUMA
            shape = cink if flat_bg else np.ones_like(cink)
    elif transparent and solid.any():
        lum = crgb @ LUMA
        white = ((lum > 0.9) & solid).sum() / solid.sum()
        if 0.03 < white < 0.7:
            shape = cink * (1 - np.clip((lum - 0.75) / 0.2, 0, 1))

    if gray is not None:
        color = gray[..., None].repeat(3, -1)
    else:
        cropped = _crop(shape)
        if cropped is None:
            return None
        shape = cropped[0]
        color = np.ones(shape.shape + (3,), dtype=np.float32) * (np.array(INK, dtype=np.float32) / 255.0)
    out = np.dstack([color, shape])

    result = Image.fromarray((out * 255).round().astype(np.uint8), "RGBA")
    result.thumbnail((OUT_SIDE, OUT_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    result.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _download(url: str) -> bytes:
    with requests.get(url, timeout=(5, 10), stream=True, headers={"User-Agent": USER_AGENT}) as resp:
        resp.raise_for_status()
        data = b""
        for chunk in resp.iter_content(65536):
            data += chunk
            if len(data) > MAX_BYTES:
                raise ValueError("logo za duże")
        return data


def _write(path: Path, data: bytes):
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def logo_png(url: str) -> Optional[bytes]:
    if urlparse(url).scheme not in ("http", "https"):
        return None
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    path = CACHE_DIR / f"v{VERSION}_{key}.png"
    failed = CACHE_DIR / f"v{VERSION}_{key}.fail"
    with _guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        if path.exists():
            return path.read_bytes()
        if failed.exists() and time.time() - failed.stat().st_mtime < RETRY_FAILED_S:
            return None
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        try:
            png = render(_download(url))
        except Exception as e:
            logger.info(f"Logo {url}: {e}")
            png = None
        if png is None:
            _write(failed, b"")
            return None
        _write(path, png)
        failed.unlink(missing_ok=True)
        return png
