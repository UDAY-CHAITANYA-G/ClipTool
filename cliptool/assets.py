"""Uploaded watermark images and app-wide settings (default watermark).

Everything lives under assets/ in the project folder:
  assets/watermarks/wm_<hash>.<ext>   uploaded images (PNG, JPG, WebP)
  assets/settings.json                {"watermark": {...layer..., "auto": bool}, "title": {...}}

Two default images can be added to every new clip automatically:
  watermark  - your channel logo (default spot: centred just below the captions)
  title      - a game / show title logo (default spot: top centre)
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ASSETS = Path("assets").resolve()
WATERMARKS = ASSETS / "watermarks"
SETTINGS = ASSETS / "settings.json"
MAX_UPLOAD = 10 * 1024 * 1024
MAX_IMAGES_PER_CLIP = 3
NAME_RE = re.compile(r"wm_[0-9a-f]{12}\.(png|jpg|webp)")

# Named spots for an image (centre of the image, as a share of the frame).
POSITIONS = {
    "top-left": (0.12, 0.07), "top": (0.5, 0.07), "top-right": (0.88, 0.07),
    "left": (0.12, 0.5), "center": (0.5, 0.5), "right": (0.88, 0.5),
    "bottom-left": (0.12, 0.93), "bottom": (0.5, 0.93), "bottom-right": (0.88, 0.93),
    "below-captions": (0.5, 0.84), "top-title": (0.5, 0.13),
}
DEFAULT_LAYER = {"file": None, "x": 0.5, "y": 0.84, "size": 0.28, "opacity": 0.9, "start": 0.0, "end": None}
DEFAULTS = {  # kind -> starting layout for that default image
    "watermark": {**DEFAULT_LAYER},
    "title": {**DEFAULT_LAYER, "x": 0.5, "y": 0.13, "size": 0.5, "opacity": 1.0},
}
DEFAULT_IDS = {"watermark": "wdefault", "title": "tdefault"}


def sniff(data: bytes) -> str | None:
    """File type from magic bytes (never trust the file name)."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def save_upload(data: bytes) -> dict:
    """Validate and store an uploaded image. Returns its info. Raises ValueError for bad files."""
    if not data:
        raise ValueError("The file is empty.")
    if len(data) > MAX_UPLOAD:
        raise ValueError("Images must be 10 MB or smaller.")
    ext = sniff(data)
    if not ext:
        raise ValueError("Upload a PNG, JPG or WebP image.")
    import cv2
    import numpy as np

    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise ValueError("That image couldn't be read.")
    h, w = img.shape[:2]
    if w < 8 or h < 8 or w > 8000 or h > 8000:
        raise ValueError("Images must be between 8 and 8000 pixels on each side.")
    WATERMARKS.mkdir(parents=True, exist_ok=True)
    name = f"wm_{hashlib.sha1(data).hexdigest()[:12]}.{ext}"
    path = WATERMARKS / name
    if not path.exists():
        path.write_bytes(data)
    return info(path, w, h)


def is_opaque(path: Path) -> bool:
    """True when the image has no real transparency (no alpha channel, or every pixel fully opaque)."""
    import cv2

    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    return img is None or img.ndim < 3 or img.shape[2] < 4 or int(img[..., 3].min()) == 255


def remove_background(bgr):
    """Make a logo's background transparent and trim to the logo.

    Works for solid backgrounds and for fake "transparent" checkerboards drawn into the picture:
    the background colours are sampled from the image border, every pixel close to one of them
    becomes transparent, stray marks far from the logo (e.g. a corner sparkle) are dropped, edge
    colours are un-mixed from the background, and the result is cropped to the logo.
    Returns a BGRA numpy array.
    """
    import cv2
    import numpy as np

    img = bgr[..., :3].astype(np.float32)
    h, w = img.shape[:2]
    band = max(4, int(min(h, w) * 0.03))
    # Background colours: the top edge and the upper part of the side edges (where a fake
    # checkerboard or a plain background shows). A differently coloured band elsewhere on the edge
    # (e.g. a cream strip at the bottom) is removed below because it touches the image border.
    border = np.concatenate([img[:band].reshape(-1, 3), img[: h * 3 // 5, :band].reshape(-1, 3),
                             img[: h * 3 // 5, -band:].reshape(-1, 3)])
    k = 3
    _, _, centers = cv2.kmeans(border, k, None, (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.5),
                               3, cv2.KMEANS_PP_CENTERS)
    dists = np.stack([np.linalg.norm(img - c, axis=2) for c in centers], axis=2)
    nearest = dists.argmin(axis=2)
    d = dists.min(axis=2)
    # Generous threshold: tinted checker seen through "glass" parts of a logo also goes transparent.
    alpha = np.clip((d - 45.0) / (85.0 - 45.0), 0, 1)

    # Full-width (or full-height) bands along an edge - e.g. a cream strip at the bottom with a
    # soft gradient into the checkerboard - are background: clear those rows / columns.
    solid = alpha > 0.5
    for axis, size in ((1, h), (0, w)):              # rows, then columns
        frac = solid.mean(axis=axis)                  # share of each row/column that is "not checker"
        for order in (range(size), range(size - 1, -1, -1)):   # from each edge inwards
            cut = None
            for i in order:
                if frac[i] > 0.6:
                    cut = i
                else:
                    break
            if cut is not None:
                lo, hi = (0, cut + 1) if order.start == 0 else (cut, size)
                # also eat the soft gradient next to the band
                grow = max(4, int(0.01 * size))
                lo, hi = max(0, lo - (grow if lo else 0)), min(size, hi + (grow if hi < size else 0))
                if axis == 1:
                    alpha[lo:hi, :] = 0
                else:
                    alpha[:, lo:hi] = 0

    mask = (alpha > 0.5).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)

    # Keep the logo: big parts plus anything close to them; drop far-away specks.
    if n > 1:
        areas = stats[1:, cv2.CC_STAT_AREA]
        big = [i + 1 for i, a in enumerate(areas) if a >= 0.05 * areas.max()]
        x0 = min(stats[i, cv2.CC_STAT_LEFT] for i in big)
        y0 = min(stats[i, cv2.CC_STAT_TOP] for i in big)
        x1 = max(stats[i, cv2.CC_STAT_LEFT] + stats[i, cv2.CC_STAT_WIDTH] for i in big)
        y1 = max(stats[i, cv2.CC_STAT_TOP] + stats[i, cv2.CC_STAT_HEIGHT] for i in big)
        mx, my = int((x1 - x0) * 0.08), int((y1 - y0) * 0.08)
        keep = np.zeros(n, bool)
        for i in range(1, n):
            cx = stats[i, cv2.CC_STAT_LEFT] + stats[i, cv2.CC_STAT_WIDTH] / 2
            cy = stats[i, cv2.CC_STAT_TOP] + stats[i, cv2.CC_STAT_HEIGHT] / 2
            keep[i] = x0 - mx <= cx <= x1 + mx and y0 - my <= cy <= y1 + my
        region = cv2.dilate(keep[labels].astype(np.uint8), np.ones((5, 5), np.uint8))
        alpha *= region

    # Un-mix edge pixels from the background colour they were blended with.
    bg = centers[nearest]
    a3 = np.maximum(alpha[..., None], 1e-3)
    fg = np.clip((img - (1 - a3) * bg) / a3, 0, 255)
    fg = np.where(alpha[..., None] > 0.98, img, fg)

    ys, xs = np.nonzero(alpha > 0.05)
    if len(xs) == 0:
        raise ValueError("Couldn't find a logo in that image - it looks like all background.")
    pad = max(4, int(0.02 * max(np.ptp(xs), np.ptp(ys))))
    xa, xb = max(0, xs.min() - pad), min(w, xs.max() + pad + 1)
    ya, yb = max(0, ys.min() - pad), min(h, ys.max() + pad + 1)
    out = np.dstack([fg, alpha * 255]).astype(np.uint8)
    return out[ya:yb, xa:xb]


def clean_image(name: str) -> dict:
    """Store a background-removed, trimmed copy of a stored image. Returns the new image's info."""
    import cv2

    p = path_of(name)
    if not p:
        raise ValueError("That image doesn't exist.")
    src = cv2.imread(str(p), cv2.IMREAD_COLOR)
    if src is None:
        raise ValueError("That image couldn't be read.")
    # Large uploads: work at max 1600 px, plenty for a watermark.
    scale = 1600 / max(src.shape[:2])
    if scale < 1:
        src = cv2.resize(src, (int(src.shape[1] * scale), int(src.shape[0] * scale)), interpolation=cv2.INTER_AREA)
    rgba = remove_background(src)
    ok, png = cv2.imencode(".png", rgba)
    if not ok:
        raise ValueError("Couldn't save the cleaned image.")
    return save_upload(png.tobytes())


def info(path: Path, w: int | None = None, h: int | None = None) -> dict:
    if w is None:
        import cv2

        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        h, w = (img.shape[:2] if img is not None else (0, 0))
    return {"name": path.name, "w": int(w), "h": int(h), "bytes": path.stat().st_size,
            "alpha": path.suffix in (".png", ".webp"), "opaque": is_opaque(path)}


def path_of(name: str | None) -> Path | None:
    """Absolute path of a stored watermark, or None if the name is not a valid stored file."""
    if not name or not NAME_RE.fullmatch(name):
        return None
    p = WATERMARKS / name
    return p if p.is_file() else None


def list_images() -> list[dict]:
    if not WATERMARKS.exists():
        return []
    files = sorted((p for p in WATERMARKS.iterdir() if NAME_RE.fullmatch(p.name)), key=lambda p: p.stat().st_mtime, reverse=True)
    return [info(p) for p in files]


def delete_image(name: str) -> bool:
    p = path_of(name)
    if not p:
        return False
    p.unlink()
    s = load_settings()
    for kind in DEFAULTS:
        if s[kind].get("file") == name:
            s[kind]["file"] = None
            s[kind]["auto"] = False
    save_settings(s)
    return True


DEFAULT_PATHS = {"output": "output", "work": "work"}  # relative to the project folder


def load_settings() -> dict:
    s = {kind: {**layer, "auto": False} for kind, layer in DEFAULTS.items()}
    s["paths"] = {"output": None, "work": None}
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        for kind in DEFAULTS:
            s[kind].update({k: v for k, v in data.get(kind, {}).items() if k in s[kind]})
        s["paths"].update({k: v for k, v in (data.get("paths") or {}).items() if k in s["paths"] and isinstance(v, str)})
    except (OSError, ValueError):
        pass
    for kind in DEFAULTS:
        if not path_of(s[kind].get("file")):
            s[kind]["file"] = None
            s[kind]["auto"] = False
    return s


def save_settings(s: dict) -> dict:
    ASSETS.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(s, indent=2), encoding="utf-8")
    return s


def clean_layer(layer: dict, require_file: bool = True) -> dict:
    """Validate one image layer. Raises ValueError."""
    if not isinstance(layer, dict):
        raise ValueError("Bad image layer.")
    name = layer.get("file")
    if require_file and not path_of(name):
        raise ValueError("That watermark image no longer exists - upload it again.")
    end = layer.get("end")
    out = {
        "id": re.sub(r"[^a-z0-9]", "", str(layer.get("id", "")))[:12] or "w0",
        "file": name if path_of(name) else None,
        "x": min(1.0, max(0.0, float(layer.get("x", 0.88)))),
        "y": min(1.0, max(0.0, float(layer.get("y", 0.07)))),
        "size": round(min(0.9, max(0.03, float(layer.get("size", 0.18)))), 3),
        "opacity": round(min(1.0, max(0.05, float(layer.get("opacity", 0.85)))), 2),
        "start": max(0.0, float(layer.get("start", 0) or 0)),
        "end": None if end is None else max(0.1, float(end)),
    }
    if out["end"] is not None and out["end"] <= out["start"]:
        raise ValueError("An image must end after it starts.")
    return out


def default_layers_for_clip() -> list[dict]:
    """Default images (title logo, channel logo) whose "add to new clips" is on, as clip layers."""
    s = load_settings()
    out = []
    for kind in ("title", "watermark"):
        d = s[kind]
        if d.get("auto") and path_of(d.get("file")):
            out.append({"id": DEFAULT_IDS[kind], "file": d["file"], "x": d["x"], "y": d["y"], "size": d["size"],
                        "opacity": d["opacity"], "start": 0.0, "end": None})
    return out


def default_layer_for_clip() -> dict | None:
    """Back-compat: the channel-logo default only."""
    return next((l for l in default_layers_for_clip() if l["id"] == "wdefault"), None)


def with_defaults(images: list[dict] | None) -> list[dict]:
    """A clip's images with the current defaults put in (replacing older copies of them), max 3."""
    defaults = default_layers_for_clip()
    ids = {d["id"] for d in defaults}
    own = [i for i in (images or []) if i.get("id") not in ids and i.get("id") not in DEFAULT_IDS.values()]
    return (defaults + own)[:MAX_IMAGES_PER_CLIP]


# ---- storage folders ---------------------------------------------------------

def folder(kind: str) -> Path:
    """Where downloaded videos ("work") or finished clips ("output") are stored."""
    custom = load_settings()["paths"].get(kind)
    return Path(custom).resolve() if custom else Path(DEFAULT_PATHS[kind]).resolve()


def set_folders(paths: dict) -> dict:
    """Validate and save custom folders. None/"" means the default. Creates folders that don't exist."""
    s = load_settings()
    for kind in ("output", "work"):
        if kind not in paths:
            continue
        raw = (paths[kind] or "").strip().strip('"')
        if not raw:
            s["paths"][kind] = None
            continue
        p = Path(raw).expanduser()
        if not p.is_absolute():
            raise ValueError(r"Use a full folder path, like C:\Videos\ClipTool.")
        try:
            p.mkdir(parents=True, exist_ok=True)
            probe = p / ".cliptool_write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as e:
            raise ValueError(f"Can't use {p}: {e.strerror or e}") from None
        s["paths"][kind] = str(p.resolve())
    o, w = folder_from(s, "output"), folder_from(s, "work")
    if o == w:
        raise ValueError("Use different folders for downloads and finished clips.")
    return save_settings(s)


def folder_from(s: dict, kind: str) -> Path:
    custom = s["paths"].get(kind)
    return Path(custom).resolve() if custom else Path(DEFAULT_PATHS[kind]).resolve()


def folder_size(p: Path) -> int:
    try:
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except OSError:
        return 0


def browse(path: str | None) -> dict:
    """List sub-folders of `path` (or drives / home when empty) for the folder picker."""
    import os
    import string

    if not path:
        drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")] if os.name == "nt" else ["/"]
        home = Path.home()
        return {"path": "", "parent": None, "dirs": [{"name": str(home), "path": str(home)}] +
                [{"name": d, "path": d} for d in drives]}
    p = Path(path).expanduser()
    if not p.is_dir():
        raise ValueError("That folder doesn't exist.")
    p = p.resolve()
    dirs = []
    try:
        for c in sorted(p.iterdir(), key=lambda c: c.name.lower()):
            if c.is_dir() and not c.name.startswith((".", "$")):
                dirs.append({"name": c.name, "path": str(c)})
    except PermissionError:
        pass
    parent = str(p.parent) if p.parent != p else ""
    return {"path": str(p), "parent": parent, "dirs": dirs[:500]}
