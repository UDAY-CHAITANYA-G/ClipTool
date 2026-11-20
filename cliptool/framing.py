"""Content-aware framing for vertical clips (fully offline: ffmpeg + OpenCV).

A 9:16 crop of a 16:9 video keeps only ~32% of the width, so the crop has to go where
the action is - or not crop at all. For each clip we sample frames at 4 fps and build a
per-column "interest" profile from:

- faces (OpenCV Haar detector) - strongest signal;
- motion (difference between consecutive frames) - where things move.

Then we choose one of three framings:

- "crop": one steady speaker -> fixed crop centred on the face;
- "pan":  the interesting region fits in the crop window but moves -> a smoothed
          camera pan that follows it (per-frame crop updates via ffmpeg sendcmd);
- "blur": the action is spread wider than the window (gameplay, wide shots, camera
          moving everywhere, slides, no clear subject) -> show the full frame over a
          blurred background, so no context is lost.
"""
from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path

import numpy as np

from .ffmpeg import ffmpeg_exe

SAMPLE_FPS = 4.0
SMALL_W = 320

# Decision thresholds (tuned on talks, interviews and gameplay).
MIN_FACE_SHARE = 0.35      # faces in at least this share of samples to trust them
STEADY_FACE = 0.70         # share of face sightings near the median for a fixed crop
PAN_COVERAGE = 0.62        # mean share of interest the best window captures to allow panning
PAN_MIN_INFORMATIVE = 0.4  # share of samples that must carry a usable signal
MAX_PAN_SPEED = 0.35       # max pan speed, in crop-widths per second


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


def sample_frames(source: Path, start: float, dur: float) -> np.ndarray:
    """Grayscale frames (n, h, SMALL_W) sampled at SAMPLE_FPS."""
    W, H = video_size(str(source))
    h = int(round(SMALL_W * H / W / 2) * 2)
    proc = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
         "-i", str(source), "-vf", f"fps={SAMPLE_FPS},scale={SMALL_W}:{h},format=gray", "-f", "rawvideo", "-"],
        capture_output=True)
    buf = np.frombuffer(proc.stdout, dtype=np.uint8)
    n = len(buf) // (SMALL_W * h)
    return buf[: n * SMALL_W * h].reshape(n, h, SMALL_W)


def interest_profiles(frames: np.ndarray) -> tuple[np.ndarray, list[float | None]]:
    """Per-sample column interest (n, SMALL_W) and the main face centre per sample (0-1 or None)."""
    n, h, w = frames.shape
    det = _detector()
    min_face = max(20, h // 12)
    cols = np.arange(w)
    prof = np.zeros((n, w), dtype=np.float32)
    faces: list[float | None] = []
    prev = None
    for k, f in enumerate(frames):
        # Motion: pixels that changed noticeably since the previous sample.
        if prev is not None:
            diff = np.abs(f.astype(np.int16) - prev.astype(np.int16)) > 18
            m = diff.sum(axis=0).astype(np.float32)
            if m.sum() > 0:
                prof[k] += m / m.sum()
        prev = f
        # Faces: a Gaussian bump over the largest face, weighted above motion.
        found = det.detectMultiScale(f, scaleFactor=1.15, minNeighbors=5, minSize=(min_face, min_face))
        if len(found):
            x, y, fw, fh = max(found, key=lambda r: r[2] * r[3])
            cx = x + fw / 2
            faces.append(cx / w)
            bump = np.exp(-0.5 * ((cols - cx) / max(fw * 0.6, 4)) ** 2)
            prof[k] += 2.0 * bump / bump.sum()
        else:
            faces.append(None)
    return prof, faces


def best_windows(prof: np.ndarray, win: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each sample: best window centre (px), coverage (0-1), informative flag."""
    n, w = prof.shape
    cum = np.concatenate([np.zeros((n, 1), dtype=np.float32), np.cumsum(prof, axis=1)], axis=1)
    sums = cum[:, win:] - cum[:, : w - win + 1]          # (n, w-win+1)
    starts = sums.argmax(axis=1)
    total = prof.sum(axis=1)
    informative = total > 1e-6
    coverage = np.where(informative, sums.max(axis=1) / np.maximum(total, 1e-6), 0.0)
    centres = starts + win / 2
    return centres, coverage, informative


def smooth_path(centres: np.ndarray, informative: np.ndarray, win: int, w: int) -> np.ndarray:
    """Fill gaps, remove jitter, and cap the pan speed. Returns window centres in px."""
    c = centres.astype(float).copy()
    # Fill uninformative samples with the last known (or first known) position.
    known = np.where(informative)[0]
    if len(known) == 0:
        return np.full_like(c, w / 2)
    c[: known[0]] = c[known[0]]
    for k in range(1, len(c)):
        if not informative[k]:
            c[k] = c[k - 1]
    # Median then moving average (~1.5 s) to steady the camera.
    k_med = 5
    pad = np.pad(c, k_med // 2, mode="edge")
    c = np.array([np.median(pad[i:i + k_med]) for i in range(len(c))])
    k_avg = max(3, int(SAMPLE_FPS * 1.5) | 1)
    pad = np.pad(c, k_avg // 2, mode="edge")
    c = np.convolve(pad, np.ones(k_avg) / k_avg, mode="valid")
    # Speed limit.
    step = MAX_PAN_SPEED * win / SAMPLE_FPS
    for k in range(1, len(c)):
        c[k] = c[k - 1] + np.clip(c[k] - c[k - 1], -step, step)
    return np.clip(c, win / 2, w - win / 2)


def choose_framing(source: Path, start: float, dur: float) -> dict:
    """Decide framing for one clip.

    Returns {"mode": "crop"|"pan"|"blur", "x": centre 0-1 (crop), "path": [(t, centre 0-1), ...] (pan),
             "coverage": float}
    """
    W, H = video_size(str(source))
    if W / H < 1.0:  # already vertical: nothing to reframe
        return {"mode": "crop", "x": 0.5, "coverage": 1.0}
    frames = sample_frames(source, start, dur)
    n = len(frames)
    if n < 3:
        return {"mode": "blur", "x": 0.5, "coverage": 0.0}
    h, w = frames.shape[1:]
    win = int(round(h * 9 / 16))  # 9:16 window width in the small frame
    prof, faces = interest_profiles(frames)

    # 1. One steady speaker -> fixed crop on the face.
    xs = np.array([f for f in faces if f is not None])
    if len(xs) >= MIN_FACE_SHARE * n:
        med = float(np.median(xs))
        if float(np.mean(np.abs(xs - med) < 0.12)) >= STEADY_FACE:
            return {"mode": "crop", "x": med, "coverage": 1.0}

    # 2. Does the action fit in the window? If so, follow it.
    centres, coverage, informative = best_windows(prof, win)
    share_info = float(informative.mean())
    mean_cov = float(coverage[informative].mean()) if informative.any() else 0.0
    if share_info >= PAN_MIN_INFORMATIVE and mean_cov >= PAN_COVERAGE:
        path = smooth_path(centres, informative, win, w)
        if float(np.ptp(path)) < 0.08 * w:  # barely moves: hold a fixed crop instead
            return {"mode": "crop", "x": float(np.median(path)) / w, "coverage": mean_cov}
        times = np.arange(n) / SAMPLE_FPS
        return {"mode": "pan", "path": [(float(t), float(c) / w) for t, c in zip(times, path)],
                "coverage": mean_cov}

    # 3. Action wider than the window (or no clear subject) -> keep the whole frame.
    return {"mode": "blur", "x": 0.5, "coverage": mean_cov}


def crop_x(source: Path, x_centre: float, out_w: int = 1080, out_h: int = 1920) -> int:
    """Left edge of an out_w-wide crop, after scaling the source to out_h tall."""
    W, H = video_size(str(source))
    scaled_w = int(round(W * out_h / H / 2) * 2)
    x = int(x_centre * scaled_w - out_w / 2)
    return max(0, min(scaled_w - out_w, x))


def pan_commands(source: Path, path: list[tuple[float, float]], dur: float, fps: float = 30.0,
                 out_w: int = 1080, out_h: int = 1920) -> str:
    """ffmpeg sendcmd script moving crop@pan's x smoothly along `path` (linear between samples)."""
    ts = np.array([p[0] for p in path])
    cs = np.array([p[1] for p in path])
    lines = []
    last = None
    for k in range(int(dur * fps) + 1):
        t = k / fps
        x = crop_x(source, float(np.interp(t, ts, cs)), out_w, out_h)
        if x != last:
            lines.append(f"{t:.3f} crop@pan x {x};")
            last = x
    return "\n".join(lines) + "\n"
