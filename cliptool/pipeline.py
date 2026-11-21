"""The full link -> clips pipeline, shared by the CLI and the local website."""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import candidates as cand_mod
from . import fetch, rank, render, report, signals, transcribe
from .config import load_config
from .ffmpeg import read_audio


@dataclass
class Options:
    url: str
    count: int | None = None
    min_s: float | None = None
    max_s: float | None = None
    aspect: str = "vertical"
    fit: str = "auto"
    captions: bool = True
    caption_style: str = "highlight"
    hook_intro: bool = False          # play the best 3-5 s of each clip first, then the clip
    speed: float = 1.0                # 0.5-2x playback speed for every clip (captions stay in sync)
    preset: str | None = None         # one of your saved presets: its whole look goes on every clip
    ai: bool = False
    model: str | None = None
    lang: str = "en"
    whisper: bool = False
    whisper_model: str = "small"
    dry_run: bool = False
    out: Path = field(default_factory=lambda: Path("output"))
    work: Path = field(default_factory=lambda: Path("work"))
    cookies_from_browser: str | None = None
    config: Path | None = None


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def key_lock(key: str) -> threading.RLock:
    """One lock per key (a run folder, a clip, a video id) so parallel jobs don't trip over each other."""
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _run_key(run_dir: Path) -> str:
    return "run:" + str(Path(run_dir).resolve()).lower()


class Cancelled(Exception):
    pass


def run(o: Options, log: Callable[[str], None] = print, stage: Callable[[str, float], None] | None = None,
        cancelled: Callable[[], bool] = lambda: False) -> dict:
    """Run the pipeline. `stage(name, progress 0-1)` reports progress. Returns a result dict."""
    def at(name: str, p: float):
        if cancelled():
            raise Cancelled()
        log(f"\n==> {name}")
        if stage:
            stage(name, p)

    cfg = load_config(o.config)
    cc = cfg["clips"]
    count = o.count or cc["count"]
    min_s = o.min_s or cc["min_seconds"]
    max_s = o.max_s or cc["max_seconds"]
    if min_s >= max_s:
        raise ValueError("Minimum length must be smaller than maximum length.")
    model = o.model or cfg["ai"]["model"]
    t0 = time.time()

    at("Fetching video info", 0.02)
    info, work = fetch.fetch_info(o.url, o.work, o.cookies_from_browser)
    log(f"  {info['title']}  ({info['channel']}, {int(info['duration'] or 0) // 60} min)")
    log(f"  heatmap: {'yes' if info['heatmap'] else 'no'} · chapters: {len(info['chapters'])}")

    captions = None
    if not o.whisper:
        at("Getting captions", 0.06)
        captions = fetch.download_captions(o.url, info, work, o.lang, o.cookies_from_browser)
        log(f"  {'found' if captions else 'none - will transcribe locally'}")

    at("Downloading video", 0.10)
    source = fetch.download_video(o.url, work, o.cookies_from_browser)
    log(f"  {source}")

    at("Building transcript", 0.35)
    tr = transcribe.get_transcript(work, captions, lambda: source, o.lang, o.whisper_model, force_whisper=o.whisper)
    sentences = tr["sentences"]
    log(f"  {len(sentences)} sentences from {tr['source']}")

    at("Finding and scoring candidates", 0.45)
    cands = cand_mod.make_candidates(sentences, min_s, max_s, info["chapters"])
    if not cands:
        raise ValueError(f"No {min_s:.0f}-{max_s:.0f}s windows found; try a smaller minimum length.")
    samples = read_audio(source, 8000)
    signals.score_candidates(cands, sentences, info, samples, 8000, cfg["weights"], tr.get("cues"))
    n_final = cc["finalists_for_ai"] if o.ai else count
    finalists = rank.suppress(cands, cc["max_overlap"], max(n_final, count))
    log(f"  {len(cands)} candidates -> {len(finalists)} finalists")

    clips = None
    if o.ai:
        at(f"Ranking with Claude ({model})", 0.52)
        try:
            from . import llm

            picks = llm.rank_with_claude(info, finalists, sentences, count, min_s, max_s, model, cfg["ai"]["effort"])
            clips = rank.merge_claude(picks, finalists, sentences, min_s, max_s, cfg["weights"]["ai_blend"], count)
        except Exception as e:
            log(f"  warning: {e}\n  falling back to offline ranking")
    if not clips:
        clips = rank.local_only(finalists, sentences, count)

    out_dir = o.out / f"{render.slugify(info['title'], 50)}_{info['id']}"
    out_dir.mkdir(parents=True, exist_ok=True)
    if not o.dry_run:
        import shutil

        shutil.rmtree(out_dir / "clips", ignore_errors=True)  # drop clips from an earlier run
        from .assets import default_layers_for_clip

        default_imgs = default_layers_for_clip()
        if default_imgs:
            log(f"  adding {len(default_imgs)} default logo(s) to every clip")
        if o.hook_intro:
            log("  finding a 3-5 s hook for each clip")
        speed = min(2.0, max(0.5, float(o.speed or 1.0)))
        for c in clips:
            c["style"] = {"preset": o.caption_style}
            if abs(speed - 1.0) > 1e-3:
                c["speed"] = speed
            if o.hook_intro:
                h = signals.find_hook(c, sentences, info, source, tr.get("cues"))
                if h:
                    c["hook_intro"] = {"on": True, "start": h["start"], "end": h["end"], "text": h["text"]}
            if default_imgs:
                c["images"] = [dict(i) for i in default_imgs]
            c["aspect"], c["fit"], c["captions"] = o.aspect, o.fit, o.captions
        if o.preset:
            from . import userpresets

            ps = userpresets.get(o.preset)
            if ps is None:
                log(f"  preset '{o.preset}' not found - using the form's settings")
            else:
                log(f"  applying your preset '{o.preset}': {userpresets.summary(ps)}")
                for c in clips:
                    c.update(clean_changes(userpresets.changes_for_clip(ps, c, sentences, info, source),
                                           info.get("duration") or 0))
        # Render the clips side by side (render.RENDER_SLOTS caps how many encode at once overall).
        from concurrent.futures import ThreadPoolExecutor, as_completed

        at(f"Rendering {len(clips)} clips ({min(len(clips), render.RENDER_WORKERS)} at a time)", 0.55)
        done = 0
        with ThreadPoolExecutor(max_workers=max(1, min(len(clips), render.RENDER_WORKERS)),
                                thread_name_prefix="cliptool-render") as pool:
            futs = {pool.submit(render.render_clip, source, c, sentences, out_dir, k, c["aspect"], c["fit"],
                                c["captions"], cfg): c for k, c in enumerate(clips, 1)}
            try:
                for f in as_completed(futs):
                    c = futs[f]
                    c.update(f.result())
                    done += 1
                    log(f"  rendered: {c['title']}")
                    at(f"Rendered {done} of {len(clips)} clips", 0.55 + 0.43 * done / len(clips))
            except BaseException:
                for f in futs:
                    f.cancel()
                raise

    recs = report.clip_records(info, clips, sentences)
    settings = {"count": count, "min_seconds": min_s, "max_seconds": max_s,
                "ai": bool(clips and clips[0].get("claude") is not None), "model": model if o.ai else None,
                "aspect": o.aspect, "fit": o.fit, "captions": o.captions, "transcript_source": tr["source"],
                "work_dir": str(work.dir.resolve()), "rendered": not o.dry_run}
    report.write_json(out_dir, info, recs, settings)
    page = report.write_html(out_dir, info, recs)
    if stage:
        stage("Done", 1.0)
    return {"out_dir": out_dir, "page": page, "clips": recs, "info": info,
            "seconds": round(time.time() - t0, 1), "settings": settings}


# ---- editing one clip -------------------------------------------------------

EDITABLE_STYLE = {"preset", "position", "scale", "color", "highlight", "font", "uppercase", "words_per_line",
                  "bold", "italic", "mode", "outline", "outline_color", "shadow", "shadow_color", "shadow_opacity",
                  "spacing", "background", "band_color", "band_opacity"}


def load_run(run_dir: Path) -> tuple[dict, dict, list[dict], Path]:
    """clips.json, video info, transcript sentences and source video of a run."""
    import json

    from .cache import Work

    data = json.loads((run_dir / "clips.json").read_text(encoding="utf-8"))
    work_dir = Path(data["settings"]["work_dir"])
    work = Work(work_dir.parent, work_dir.name)
    source = next((p for p in work.dir.glob("source.*") if p.suffix in (".mp4", ".mkv", ".webm")), None)
    if not source:
        raise FileNotFoundError("The downloaded video for this run is gone (cleared?). Run the link again to edit.")
    info = work.load("info.json")
    sentences = transcribe.cached_transcript(work)["sentences"]
    return data, info, sentences, source


HEX = re.compile(r"#[0-9A-Fa-f]{6}")


def clean_texts(texts) -> list[dict]:
    """Validate text overlays (max 12)."""
    if not isinstance(texts, list):
        raise ValueError("Text overlays must be a list.")
    if len(texts) > 12:
        raise ValueError("At most 12 text boxes per clip.")
    out = []
    for t in texts:
        if not isinstance(t, dict):
            raise ValueError("Bad text box.")
        text = str(t.get("text", "")).strip()[:300]
        if not text:
            continue
        item = {
            "id": re.sub(r"[^a-z0-9]", "", str(t.get("id", "")))[:12] or f"t{len(out)}",
            "text": text,
            "x": min(1.0, max(0.0, float(t.get("x", 0.5)))), "y": min(1.0, max(0.0, float(t.get("y", 0.15)))),
            "size": int(min(300, max(16, float(t.get("size", 72))))),
            "font": t.get("font") if t.get("font") in render.FONTS else "Arial Black",
            "bold": bool(t.get("bold", True)), "italic": bool(t.get("italic", False)),
            "outline": int(min(12, max(0, float(t.get("outline", 4))))), "shadow": bool(t.get("shadow", True)),
            "box": bool(t.get("box", False)), "box_opacity": min(1.0, max(0.1, float(t.get("box_opacity", 0.85)))),
            "start": max(0.0, float(t.get("start", 0))), "end": max(0.1, float(t.get("end", 3))),
            "anim": t.get("anim") if t.get("anim") in ("none", "fade", "pop") else "fade",
        }
        for col, default in (("color", "#FFFFFF"), ("outline_color", "#000000"), ("box_color", "#000000")):
            val = str(t.get(col, default))
            if not HEX.fullmatch(val):
                raise ValueError("Colours must look like #RRGGBB.")
            item[col] = val
        if item["end"] <= item["start"]:
            raise ValueError(f'Text "{text[:20]}" must end after it starts.')
        out.append(item)
    return out


def clean_changes(changes: dict, duration: float) -> dict:
    """Validate an edit request from the website. Raises ValueError with a readable message."""
    out: dict = {}
    if "start" in changes or "end" in changes:
        start, end = float(changes["start"]), float(changes["end"])
        if start < 0 or (duration and end > duration + 0.5):
            raise ValueError("Start and end must be inside the video.")
        if end - start < 1.0:
            raise ValueError("A clip must be at least 1 second long.")
        if end - start > 180:
            raise ValueError("A clip can be at most 180 seconds long.")
        out["start"], out["end"] = round(start, 3), round(end, 3)
    for key, limit in (("title", 100), ("hook", 140)):
        if key in changes:
            val = str(changes[key]).strip()
            if not val:
                raise ValueError(f"The {key} can't be empty.")
            out[key] = val[:limit]
    if "caption_text" in changes:
        val = str(changes["caption_text"] or "").strip()
        out["caption_text"] = val or None
    if "caption_range" in changes:
        r = changes["caption_range"]
        out["caption_range"] = [round(float(r[0]), 2), round(float(r[1]), 2)] if r and len(r) == 2 and float(r[1]) > float(r[0]) else None
    if "caption_offset" in changes:
        out["caption_offset"] = round(min(5.0, max(-5.0, float(changes["caption_offset"] or 0))), 2)
    if "captions" in changes:
        out["captions"] = bool(changes["captions"])
    if "aspect" in changes:
        if changes["aspect"] not in render.SIZES:
            raise ValueError("Unknown format.")
        out["aspect"] = changes["aspect"]
    if "fit" in changes:
        if changes["fit"] not in render.FITS:
            raise ValueError("Unknown layout.")
        out["fit"] = changes["fit"]
    if "facecam" in changes:
        fc = changes["facecam"]
        if fc is None:
            out["facecam"] = None
        else:
            try:
                fc = {k: float(fc[k]) for k in ("x", "y", "w", "h")}
            except (KeyError, TypeError, ValueError):
                raise ValueError("Facecam box needs x, y, w, h.") from None
            if not (0 <= fc["x"] < 1 and 0 <= fc["y"] < 1 and 0.02 <= fc["w"] <= 1 and 0.02 <= fc["h"] <= 1
                    and fc["x"] + fc["w"] <= 1.001 and fc["y"] + fc["h"] <= 1.001):
                raise ValueError("Facecam box must be inside the video.")
            out["facecam"] = {k: round(v, 4) for k, v in fc.items()}
    # Numeric settings: (key, low, high). None means "not set" and is kept as None.
    for key, lo, hi in (("game_x", 0, 1), ("game_y", 0, 1), ("crop_x", 0, 1), ("crop_y", 0, 1),
                        ("zoom", 1, 3), ("game_zoom", 1, 3), ("cam_share", 0.15, 0.7), ("speed", 0.5, 2),
                        ("volume", 0, 2), ("fade_in", 0, 3), ("fade_out", 0, 3), ("cover_t", 0, 600)):
        if key in changes:
            v = changes[key]
            out[key] = None if v is None else round(min(hi, max(lo, float(v))), 4)
    if "cam_pos" in changes:
        if changes["cam_pos"] not in ("top", "bottom"):
            raise ValueError("Facecam position must be top or bottom.")
        out["cam_pos"] = changes["cam_pos"]
    if "mute" in changes:
        out["mute"] = bool(changes["mute"])
    if "quality" in changes:
        if changes["quality"] not in render.QUALITY:
            raise ValueError("Unknown export quality.")
        out["quality"] = changes["quality"]
    if "progress_bar" in changes:
        pb = changes["progress_bar"] or {}
        col = str(pb.get("color", "#FFD400"))
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", col):
            raise ValueError("Colours must look like #RRGGBB.")
        out["progress_bar"] = {"on": bool(pb.get("on")), "color": col,
                               "position": "top" if pb.get("position") == "top" else "bottom"}
    if "texts" in changes:
        out["texts"] = clean_texts(changes["texts"])
    if "speed_zones" in changes:
        zs = changes["speed_zones"] or []
        if not isinstance(zs, list) or len(zs) > 30:
            raise ValueError("At most 30 speed zones per clip.")
        clean_z = []
        for z in sorted(zs, key=lambda z: float(z.get("start", 0))):
            za, zb, sp = float(z["start"]), float(z["end"]), float(z["speed"])
            if zb - za < 0.2:
                continue
            if not 0.25 <= sp <= 4.0:
                raise ValueError("Zone speeds must be between 0.25x and 4x.")
            if clean_z and za < clean_z[-1]["end"]:
                za = clean_z[-1]["end"]  # no overlaps: later zones start where the previous ends
                if zb - za < 0.2:
                    continue
            clean_z.append({"start": round(za, 2), "end": round(zb, 2), "speed": round(sp, 3)})
        out["speed_zones"] = clean_z
    if "description" in changes:
        d = str(changes["description"] or "").strip()
        if len(d) > 5000:
            raise ValueError("Descriptions can be at most 5000 characters (YouTube's limit).")
        out["description"] = d or None
    if "hook_intro" in changes:
        h = changes["hook_intro"] or {}
        if not h.get("on"):
            out["hook_intro"] = {"on": False, "start": h.get("start"), "end": h.get("end"), "text": h.get("text")}
        else:
            hs, he = float(h["start"]), float(h["end"])
            if hs < 0 or (duration and he > duration + 0.5):
                raise ValueError("The hook must be inside the video.")
            if not 1.0 <= he - hs <= 10.0:
                raise ValueError("A hook intro must be 1-10 seconds long.")
            out["hook_intro"] = {"on": True, "start": round(hs, 2), "end": round(he, 2),
                                 "text": str(h.get("text") or "")[:300]}
    if "images" in changes:
        from .assets import MAX_IMAGES_PER_CLIP, clean_layer

        imgs = changes["images"] or []
        if not isinstance(imgs, list) or len(imgs) > MAX_IMAGES_PER_CLIP:
            raise ValueError(f"At most {MAX_IMAGES_PER_CLIP} images per clip.")
        out["images"] = [clean_layer(i) for i in imgs]
    if "filters" in changes:
        f = changes["filters"] or {}
        clean = {}
        for k, (lo, hi) in render.FILTER_LIMITS.items():
            if k in f:
                clean[k] = round(min(hi, max(lo, float(f[k]))), 3)
        clean["vignette"] = bool(f.get("vignette"))
        out["filters"] = clean
    if "style" in changes:
        st = {k: v for k, v in (changes["style"] or {}).items() if k in EDITABLE_STYLE}
        if st.get("preset") not in render.PRESETS:
            raise ValueError("Unknown caption style.")
        if "position" in st and st["position"] not in render.POSITIONS:
            raise ValueError("Unknown caption position.")
        for col in ("color", "highlight"):
            if col in st and not re.fullmatch(r"#[0-9A-Fa-f]{6}", str(st[col])):
                raise ValueError("Colours must look like #RRGGBB.")
        if "scale" in st:
            st["scale"] = min(1.8, max(0.5, float(st["scale"])))
        if "words_per_line" in st:
            st["words_per_line"] = min(8, max(1, int(st["words_per_line"])))
        if "uppercase" in st:
            st["uppercase"] = bool(st["uppercase"])
        if "font" in st:
            st["font"] = re.sub(r"[^A-Za-z0-9 \-]", "", str(st["font"]))[:40] or None
        for col in ("outline_color", "shadow_color", "band_color"):
            if col in st and not HEX.fullmatch(str(st[col])):
                raise ValueError("Colours must look like #RRGGBB.")
        if "mode" in st and st["mode"] not in render.EFFECTS:
            raise ValueError("Unknown highlight effect.")
        for key, lo, hi in (("outline", 0, 12), ("shadow", 0, 12), ("spacing", -2, 20)):
            if key in st:
                st[key] = int(min(hi, max(lo, float(st[key]))))
        for key in ("shadow_opacity", "band_opacity"):
            if key in st:
                st[key] = round(min(1.0, max(0.0, float(st[key]))), 2)
        for key in ("bold", "italic", "background"):
            if key in st:
                st[key] = bool(st[key])
        out["style"] = st
    return out


def _remove_when_free(p: Path, tries: int = 5) -> None:
    """Delete an old clip file; if Windows still has it open (a player, Explorer), retry in the background."""
    import threading

    def attempt(n: int) -> None:
        try:
            p.unlink(missing_ok=True)
        except PermissionError:
            if n > 0:
                threading.Timer(20, attempt, args=(n - 1,)).start()

    attempt(tries)


def edit_clip(run_dir: Path, rank: int, changes: dict, log: Callable[[str], None] = print,
              stage: Callable[[str, float], None] | None = None) -> dict:
    """Apply edits to one clip of a run and re-render just that clip."""
    with key_lock(f"{_run_key(run_dir)}#{rank}"):
        return _edit_clip(run_dir, rank, changes, log, stage)


def _edit_clip(run_dir, rank, changes, log, stage) -> dict:
    data, info, sentences, source = load_run(run_dir)
    clips = data["clips"]
    clip = next((c for c in clips if c["rank"] == rank), None)
    if not clip:
        raise ValueError(f"Clip #{rank} not found.")
    ch = clean_changes(changes, info.get("duration") or 0)
    clip.update(ch)
    # Keep the sentence span in step with the (possibly trimmed) times.
    idx = [k for k, s in enumerate(sentences) if s["end"] > clip["start"] and s["start"] < clip["end"]]
    if idx:
        clip["i"], clip["j"] = idx[0], idx[-1]
    clip["edited"] = True

    settings = data["settings"]
    aspect = clip.get("aspect") or settings.get("aspect", "vertical")
    fit = clip.get("fit") or settings.get("fit", "auto")
    captions = clip.get("captions", settings.get("captions", True))
    cfg = load_config(None)

    if stage:
        stage(f"Re-rendering clip #{rank}", 0.2)
    log(f"Re-rendering #{rank}: {clip['title']} ({clip['start']:.1f}-{clip['end']:.1f}s, {aspect}, {fit})")
    clips_dir = run_dir / "clips"
    old_files = [p for p in clips_dir.glob(f"{rank:02d}_*") if p.suffix in (".mp4", ".jpg")]
    version = format(int(time.time() * 10) % 36 ** 4, "x")[-4:]  # fresh name: never blocked by an open old file
    clip.update(render.render_clip(source, clip, sentences, run_dir, rank, aspect, fit, captions, cfg, version))
    keep = {Path(clip["file"]).name, Path(clip["thumbnail"]).name}
    for old in old_files:
        if old.name not in keep:
            _remove_when_free(old)
    clip["aspect"], clip["fit"], clip["captions"] = aspect, fit, captions

    # Other clips of this run may have been saved while this one rendered: merge into the latest file.
    with key_lock(_run_key(run_dir)):
        latest, _, _, _ = load_run(run_dir)
        clips = latest["clips"]
        k = next((n for n, c in enumerate(clips) if c["rank"] == rank), None)
        if k is None:
            for old in (run_dir / "clips").glob(f"{rank:02d}_*"):
                _remove_when_free(old)
            raise ValueError(f"Clip #{rank} was deleted while it rendered.")
        clips[k] = clip
        recs = report.clip_records(info, clips, sentences)
        report.write_json(run_dir, info, recs, latest["settings"])
        report.write_html(run_dir, info, recs)
    if stage:
        stage("Done", 1.0)
    log("Done.")
    return {"folder": run_dir.name, "rank": rank, "clip": next(r for r in recs if r["rank"] == rank)}


# ---- faster source -------------------------------------------------------------------------

def source_info(run_dir: Path) -> dict:
    from .ffmpeg import SLOW_CODECS, video_codec

    _, _, _, source = load_run(run_dir)
    codec = video_codec(source)
    return {"codec": codec, "slow": codec in SLOW_CODECS, "size_mb": round(source.stat().st_size / 2 ** 20, 1)}


def make_source_fast(run_dir: Path, log: Callable[[str], None] = print,
                     stage: Callable[[str, float], None] | None = None) -> dict:
    """Swap a run's AV1 download for the H.264 version (same timing, decodes far faster)."""
    from .cache import Work
    from .ffmpeg import SLOW_CODECS, video_codec

    data, info, _, source = load_run(run_dir)
    if video_codec(source) not in SLOW_CODECS:
        return {"folder": run_dir.name, "codec": video_codec(source), "changed": False}
    wd = Path(data["settings"]["work_dir"])
    work = Work(wd.parent, wd.name)
    url = info.get("webpage_url") or f"https://www.youtube.com/watch?v={info['id']}"
    if stage:
        stage("Downloading the H.264 version (renders get much faster)", 0.1)
    log(f"Re-downloading {info.get('title')} as H.264 ...")
    new = fetch.redownload_h264(url, work, log=log)
    codec = video_codec(new)
    dur_new = _duration(new)
    if codec in SLOW_CODECS or abs(dur_new - float(info.get("duration") or dur_new)) > 2.0:
        new.unlink(missing_ok=True)
        raise RuntimeError("The new download doesn't match the original video; kept the old one.")
    if stage:
        stage("Swapping in the faster video", 0.95)
    with key_lock("video:" + str(info.get("id"))):
        old = source.with_name("source_av1_old" + source.suffix)
        for _ in range(400):  # up to ~10 min: renders still reading the old file finish first
            try:
                source.replace(old)
                break
            except PermissionError:  # open by a render or the page's player: wait for it
                time.sleep(1.5)
        else:
            new.unlink(missing_ok=True)
            raise RuntimeError("The video is open somewhere (full-video view or a player). Close it and try again.")
        target = source.with_suffix(".mp4")
        for _ in range(60):
            try:
                new.replace(target)
                break
            except PermissionError:
                time.sleep(2)
        else:
            old.replace(source)  # put the original back
            raise RuntimeError("Couldn't swap in the new video (a file is locked - is OneDrive syncing?).")
        _remove_when_free(old)
    log(f"Done: source is now {codec} ({round(source.stat().st_size / 2 ** 20)} MB).")
    if stage:
        stage("Done", 1.0)
    return {"folder": run_dir.name, "codec": codec, "changed": True}


def _duration(path: Path) -> float:
    import re
    import subprocess

    from .ffmpeg import ffmpeg_exe

    err = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


# ---- clips you pick yourself in the full video ------------------------------------------

def add_clip(run_dir: Path, start: float, end: float, title: str | None = None,
             log: Callable[[str], None] = print, stage: Callable[[str, float], None] | None = None) -> dict:
    """Add a clip you chose in the full-video view to a run, with the run's usual look, and render it."""
    data, info, sentences, source = load_run(run_dir)
    duration = info.get("duration") or 0
    start, end = round(float(start), 2), round(float(end), 2)
    if start < 0 or (duration and end > duration + 0.5):
        raise ValueError("The clip must be inside the video.")
    if not 3.0 <= end - start <= 180.0:
        raise ValueError("A clip must be 3-180 seconds long.")
    idx = [k for k, s in enumerate(sentences) if s["end"] > start and s["start"] < end] or [0]
    probe = {"start": start, "end": end, "i": idx[0], "j": idx[-1]}
    lock = key_lock(_run_key(run_dir))
    lock.acquire()
    try:
        data, info, sentences, source = load_run(run_dir)
        rank_no = _append_manual_clip(run_dir, data, info, sentences, start, end, title, idx, probe)
    finally:
        lock.release()
    return edit_clip(run_dir, rank_no, {}, log=log, stage=stage)


def _append_manual_clip(run_dir, data, info, sentences, start, end, title, idx, probe) -> int:
    from .assets import with_defaults

    clips = data["clips"]
    first = clips[0] if clips else {}
    rank_no = max([c["rank"] for c in clips], default=0) + 1
    clip = {
        "rank": rank_no, "start": start, "end": end, "i": idx[0], "j": idx[-1],
        "title": (title or "").strip()[:100] or rank.local_title(sentences, probe),
        "hook": rank.local_hook(sentences, probe), "reason": "Picked by you in the full video.",
        "score": 0.0, "signals": {}, "manual": True,
        "style": dict(first.get("style") or {"preset": "highlight"}),
        "aspect": first.get("aspect") or data["settings"].get("aspect", "vertical"),
        "fit": first.get("fit") or data["settings"].get("fit", "auto"),
        "captions": first.get("captions", True), "images": with_defaults(None),
    }
    clips.append(clip)
    report.write_json(run_dir, info, report.clip_records(info, clips, sentences), data["settings"])
    print(f"Added clip #{rank_no}: {clip['title']} ({start:.1f}-{end:.1f}s)", flush=True)
    return rank_no


def delete_clip(run_dir: Path, rank_no: int) -> dict:
    """Remove one clip (its files and its entry) from a run."""
    with key_lock(_run_key(run_dir)):
        return _delete_clip(run_dir, rank_no)


def _delete_clip(run_dir: Path, rank_no: int) -> dict:
    import json

    data = json.loads((run_dir / "clips.json").read_text(encoding="utf-8"))
    if not any(c["rank"] == rank_no for c in data["clips"]):
        raise ValueError(f"Clip #{rank_no} not found.")
    data["clips"] = [c for c in data["clips"] if c["rank"] != rank_no]
    for f in (run_dir / "clips").glob(f"{rank_no:02d}_*"):
        _remove_when_free(f)
    (run_dir / "clips.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        _, info, _, _ = load_run(run_dir)
        report.write_html(run_dir, info, data["clips"])
    except FileNotFoundError:
        pass
    return {"deleted": rank_no, "left": len(data["clips"])}
