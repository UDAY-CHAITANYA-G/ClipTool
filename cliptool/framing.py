"""Face-aware framing for vertical clips (OpenCV Haar face detector, fully offline).

Samples the clip at 2 fps, finds faces, and decides:
- "crop" centred on the speaker when one face position dominates the clip;
- "blur" (full frame over a blurred copy) when faces jump around or there are none,
  so nothing important is cut off.
"""
from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np

from .ffmpeg import ffmpeg_exe


@lru_cache(maxsize=8)
def video_size(path: str) -> tuple[int, int]:
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", path], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", proc.stderr)
    if not m:
        raise RuntimeError("Could not read the source video's size.")
    return int(m.group(1)), int(m.group(2))


@lru_cache(maxsize=1)
def _detector():
    import cv2

    return cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")


def face_positions(source: Path, start: float, dur: float, fps: float = 2.0, width: int = 480) -> tuple[list[float], int]:
    """Return (normalised x-centres of the main face per sampled frame, frames sampled)."""
    W, H = video_size(str(source))
    h = int(round(width * H / W / 2) * 2)
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
         "-i", str(source), "-vf", f"fps={fps},scale={width}:{h},format=gray", "-f", "rawvideo", "-"],
        capture_output=True)
    frames = np.frombuffer(proc.stdout, dtype=np.uint8)
    n = len(frames) // (width * h)
    frames = frames[: n * width * h].reshape(n, h, width)
    det = _detector()
    xs = []
    min_face = max(24, h // 12)
    for f in frames:
        faces = det.detectMultiScale(f, scaleFactor=1.15, minNeighbors=5, minSize=(min_face, min_face))
        if len(faces):
            x, y, fw, fh = max(faces, key=lambda r: r[2] * r[3])  # largest face = main speaker
            xs.append((x + fw / 2) / width)
    return xs, n


def choose_framing(source: Path, start: float, dur: float) -> tuple[str, float]:
    """Return ("crop", x_centre 0-1) or ("blur", 0.5)."""
    xs, n = face_positions(source, start, dur)
    if n == 0 or len(xs) < 0.35 * n:
        return "blur", 0.5
    xs_arr = np.array(xs)
    med = float(np.median(xs_arr))
    # Share of face sightings close to the median position.
    steady = float(np.mean(np.abs(xs_arr - med) < 0.12))
    if steady < 0.7:
        return "blur", 0.5
    return "crop", med


def crop_x(source: Path, x_centre: float, out_w: int = 1080, out_h: int = 1920) -> int:
    """Left edge of an out_w-wide crop, after scaling the source to out_h tall."""
    W, H = video_size(str(source))
    scaled_w = int(round(W * out_h / H / 2) * 2)
    x = int(x_centre * scaled_w - out_w / 2)
    return max(0, min(scaled_w - out_w, x))
