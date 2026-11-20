"""Uploaded watermark images and app-wide settings (default watermark).

Everything lives under assets/ in the project folder:
  assets/watermarks/wm_<hash>.<ext>   uploaded images (PNG, JPG, WebP)
  assets/settings.json                {"watermark": {...default layer..., "auto": bool}}
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
}
DEFAULT_LAYER = {"file": None, "x": 0.88, "y": 0.07, "size": 0.18, "opacity": 0.85, "start": 0.0, "end": None}


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


def info(path: Path, w: int | None = None, h: int | None = None) -> dict:
    if w is None:
        import cv2

        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        h, w = (img.shape[:2] if img is not None else (0, 0))
    return {"name": path.name, "w": int(w), "h": int(h), "bytes": path.stat().st_size,
            "alpha": path.suffix in (".png", ".webp")}


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
    if s["watermark"].get("file") == name:
        s["watermark"]["file"] = None
        s["watermark"]["auto"] = False
        save_settings(s)
    return True


def load_settings() -> dict:
    s = {"watermark": {**DEFAULT_LAYER, "auto": False}}
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        s["watermark"].update({k: v for k, v in data.get("watermark", {}).items() if k in s["watermark"]})
    except (OSError, ValueError):
        pass
    if not path_of(s["watermark"].get("file")):
        s["watermark"]["file"] = None
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


def default_layer_for_clip() -> dict | None:
    """The default watermark as a clip layer, if "add to new clips" is on and the image exists."""
    wm = load_settings()["watermark"]
    if not wm.get("auto") or not path_of(wm.get("file")):
        return None
    return {"id": "wdefault", "file": wm["file"], "x": wm["x"], "y": wm["y"], "size": wm["size"],
            "opacity": wm["opacity"], "start": 0.0, "end": None}
