"""Locate ffmpeg and run it."""
from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

TOOLS_DIR = Path.home() / ".cliptool" / "bin"


@lru_cache(maxsize=1)
def ffmpeg_exe() -> str:
    """System ffmpeg if on PATH, else the binary bundled with imageio-ffmpeg.

    The bundled binary has a versioned file name, which yt-dlp does not
    recognise, so it is copied once to ~/.cliptool/bin/ffmpeg(.exe).
    """
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg

    src = Path(imageio_ffmpeg.get_ffmpeg_exe())
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    dst = TOOLS_DIR / ("ffmpeg" + src.suffix)
    if not dst.exists() or dst.stat().st_size != src.stat().st_size:
        shutil.copy2(src, dst)
    return str(dst)


def run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args]
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}):\n{proc.stderr.strip()[-2000:]}")
    return proc


def read_audio(path: Path, rate: int = 8000):
    """Decode a file's audio to mono float32 numpy array at `rate` Hz."""
    import numpy as np

    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-i", str(path),
           "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg audio decode failed: {proc.stderr.decode(errors='replace')[-1000:]}")
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def read_audio_range(path: Path, start: float, dur: float, rate: int = 8000):
    """Mono float32 audio of just [start, start+dur) - fast, for scoring parts of one clip."""
    import numpy as np

    cmd = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, start):.3f}", "-t", f"{dur:.3f}",
           "-i", str(path), "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    proc = subprocess.run(cmd, capture_output=True)
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def has_audio(path: Path) -> bool:
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return "Audio:" in proc.stderr
