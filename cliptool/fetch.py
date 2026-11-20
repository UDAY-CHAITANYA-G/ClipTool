"""Stage 1: metadata, heatmap, chapters, captions and video via yt-dlp."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .cache import Work
from .ffmpeg import ffmpeg_exe

ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/)([A-Za-z0-9_-]{11})")


def video_id_from_url(url: str) -> str:
    m = ID_RE.search(url)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url):
        return url
    raise ValueError(f"Not a YouTube video URL: {url}")


def _base_opts(cookies_from_browser: str | None) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "ffmpeg_location": ffmpeg_exe(),
    }
    if shutil.which("node"):
        opts["js_runtimes"] = {"node": {}}
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = (cookies_from_browser,)
    return opts


def fetch_info(url: str, work_root: Path, cookies_from_browser: str | None = None) -> tuple[dict, Work]:
    """Return trimmed metadata (cached in info.json) and the video's Work folder."""
    import yt_dlp

    vid = video_id_from_url(url)
    work = Work(work_root, vid)
    if work.has("info.json"):
        return work.load("info.json"), work

    with yt_dlp.YoutubeDL(_base_opts(cookies_from_browser)) as ydl:
        info = ydl.extract_info(url, download=False)

    keep = {
        "id": info.get("id"),
        "title": info.get("title"),
        "channel": info.get("channel") or info.get("uploader"),
        "duration": info.get("duration"),
        "webpage_url": info.get("webpage_url") or url,
        "chapters": info.get("chapters") or [],
        "heatmap": info.get("heatmap") or [],
        "subtitles": sorted((info.get("subtitles") or {}).keys()),
        "automatic_captions": sorted((info.get("automatic_captions") or {}).keys()),
        "language": info.get("language"),
    }
    work.save("info.json", keep)
    return keep, work


def _pick_caption_lang(info: dict, lang: str) -> tuple[str | None, bool]:
    """Pick a caption track: manual first, then auto. Returns (lang_code, is_auto)."""
    def match(codes):
        for c in codes:
            if c == lang:
                return c
        for c in codes:
            if c.split("-")[0] == lang and "translate" not in c:
                return c
        return None

    m = match(info["subtitles"])
    if m:
        return m, False
    # Auto captions: prefer the original-language track ("en-orig") over translations.
    orig = [c for c in info["automatic_captions"] if c.endswith("-orig")]
    a = match([c.removesuffix("-orig") for c in orig])
    if a:
        return a + "-orig", True
    a = match(info["automatic_captions"])
    return (a, True) if a else (None, False)


def download_captions(url: str, info: dict, work: Work, lang: str, cookies_from_browser: str | None = None) -> Path | None:
    if work.has("captions.json3"):
        return work.path("captions.json3")
    code, is_auto = _pick_caption_lang(info, lang)
    if not code:
        return None
    import yt_dlp

    opts = _base_opts(cookies_from_browser) | {
        "skip_download": True,
        "writesubtitles": not is_auto,
        "writeautomaticsub": is_auto,
        "subtitleslangs": [code],
        "subtitlesformat": "json3",
        "outtmpl": str(work.path("captions")),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    for p in work.dir.glob("captions*.json3"):
        p.replace(work.path("captions.json3"))
        return work.path("captions.json3")
    return None


def download_video(url: str, work: Work, cookies_from_browser: str | None = None, max_height: int = 1080) -> Path:
    for p in work.dir.glob("source.*"):
        if p.suffix in (".mp4", ".mkv", ".webm") and p.stat().st_size > 0:
            return p
    import yt_dlp

    opts = _base_opts(cookies_from_browser) | {
        "format": f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/b[height<={max_height}][ext=mp4]/bv*[height<={max_height}]+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": str(work.path("source.%(ext)s")),
        "quiet": False,
        "noprogress": False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    for p in work.dir.glob("source.*"):
        if p.suffix in (".mp4", ".mkv", ".webm"):
            return p
    raise RuntimeError("Download finished but no source video was found.")
