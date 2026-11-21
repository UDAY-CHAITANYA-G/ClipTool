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
        "channel_url": info.get("channel_url") or info.get("uploader_url"),
        "channel_handle": info.get("uploader_id") if str(info.get("uploader_id") or "").startswith("@") else None,
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


def _format(max_height: int, h264_only: bool = False) -> str:
    h = max_height
    avc = f"bv*[height<={h}][vcodec^=avc1]+ba[ext=m4a]/b[height<={h}][vcodec^=avc1]"
    if h264_only:
        return avc
    return avc + f"/bv*[height<={h}][ext=mp4]+ba[ext=m4a]/b[height<={h}][ext=mp4]/bv*[height<={h}]+ba/b"


def redownload_h264(url: str, work: Work, cookies_from_browser: str | None = None, max_height: int = 1080,
                    log=print) -> Path:
    """Fetch the H.264 version of a video next to the current source; returns its path (not yet swapped in)."""
    import shutil
    import time

    import yt_dlp

    # Download outside the project: OneDrive (and virus scanners) lock fresh files in synced folders,
    # which breaks yt-dlp's renames of its .part files.
    tmp = Path.home() / ".cliptool" / "tmp" / work.dir.name
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    for p in work.dir.glob("source_h264*"):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass  # a locked leftover from an earlier try; harmless
    opts = _base_opts(cookies_from_browser) | {
        "format": _format(max_height, h264_only=True),
        "merge_output_format": "mp4",
        "outtmpl": str(tmp / "source_h264.%(ext)s"),
        "quiet": True, "noprogress": True,
        "logger": _Logger(log),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])
    got = next((p for p in tmp.glob("source_h264.mp4") if p.stat().st_size > 0), None)
    if not got:
        raise RuntimeError("YouTube has no H.264 version of this video at this size.")
    dest = work.path("source_h264.mp4")
    for _ in range(60):
        try:
            shutil.move(str(got), str(dest))
            break
        except PermissionError:
            time.sleep(2)
    else:
        raise RuntimeError("Couldn't move the new video into place (a file is locked - is OneDrive syncing?).")
    shutil.rmtree(tmp, ignore_errors=True)
    return dest


class _Logger:
    def __init__(self, log):
        self.log = log

    def debug(self, msg):
        if msg.startswith("[download]") and "%" in msg and not msg.endswith("100%"):
            return
        self.log(msg)

    def info(self, msg):
        self.log(msg)

    def warning(self, msg):
        self.log("warning: " + msg)

    def error(self, msg):
        self.log("error: " + msg)


def download_video(url: str, work: Work, cookies_from_browser: str | None = None, max_height: int = 1080) -> Path:
    for p in work.dir.glob("source.*"):
        if p.suffix in (".mp4", ".mkv", ".webm") and p.stat().st_size > 0:
            return p
    import yt_dlp

    opts = _base_opts(cookies_from_browser) | {
        # H.264 first: it decodes many times faster than AV1/VP9, and every render decodes the source.
        "format": _format(max_height),
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
