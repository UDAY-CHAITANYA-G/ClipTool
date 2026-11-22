"""Make one video from several pieces of stock footage saved in Favorites.

The pieces (each trimmed) are joined into a single 16:9 video. That video becomes a new run with one
clip covering all of it, so the full clip editor works on it: layout and ratio, captions (from the words
saved with each piece), logos, text, filters, speed, cut and split, hook, description...
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Callable

from . import favorites, report, transcribe
from .assets import folder, with_defaults
from .cache import Work
from .ffmpeg import has_audio, run as ffmpeg_run
from .render import slugify

W, H = 1920, 1080
MAX_PIECES = 40
MAX_SECONDS = 600


def _probe_fps(path: Path) -> float:
    import subprocess

    from .ffmpeg import ffmpeg_exe

    err = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace").stderr
    m = re.search(r"([\d.]+) fps", err)
    return float(m.group(1)) if m else 30.0


def clean_pieces(pieces: list[dict]) -> list[dict]:
    """[{fav, key, in, out}] -> validated pieces with their file, entry and length."""
    if not pieces:
        raise ValueError("Add at least one clip to the video.")
    if len(pieces) > MAX_PIECES:
        raise ValueError(f"At most {MAX_PIECES} clips in one video.")
    out, total = [], 0.0
    for p in pieces:
        path, item = favorites.stock(str(p["fav"]), str(p["key"]))
        length = float(item.get("duration") or (float(item["end"]) - float(item["start"])))
        a = max(0.0, float(p.get("in") or 0))
        b = min(length, float(p.get("out") if p.get("out") is not None else length))
        if b - a < 0.3:
            raise ValueError(f"“{item['title']}” is trimmed to almost nothing.")
        out.append({"fav": p["fav"], "key": p["key"], "in": round(a, 2), "out": round(b, 2), "path": path, "item": item})
        total += b - a
    if total > MAX_SECONDS:
        raise ValueError(f"The video would be {total / 60:.1f} min - keep it under {MAX_SECONDS // 60} min.")
    return out


def build(name: str, pieces: list[dict], aspect: str = "vertical", fit: str = "blur", style: str = "highlight",
          captions: bool = True, out_root: Path | None = None, work_root: Path | None = None,
          log: Callable[[str], None] = print, stage: Callable[[str, float], None] | None = None) -> dict:
    """Join the pieces into a new run (folder) with one editable clip, render it, return {"folder", "rank"}."""
    from .pipeline import edit_clip

    name = " ".join(str(name or "").split())[:80] or "My video"
    ps = clean_pieces(pieces)
    out_root = out_root or folder("output")
    work_root = work_root or folder("work")
    mid = "mt" + uuid.uuid4().hex[:9]
    work = Work(work_root, mid)
    run_dir = out_root / f"{slugify(name)[:50] or 'video'}_{mid}"
    run_dir.mkdir(parents=True)

    # 1) join: every piece scaled into 1920x1080 (letterboxed if needed), same frame rate, 48 kHz stereo audio
    fps = 60 if any(_probe_fps(p["path"]) >= 49 for p in ps) else 30
    if stage:
        stage(f"Joining {len(ps)} clips", 0.08)
    log(f"Joining {len(ps)} clips ({sum(p['out'] - p['in'] for p in ps):.1f} s) at {fps} fps ...")
    args, parts, labels = [], [], []
    for k, p in enumerate(ps):
        args += ["-i", str(p["path"])]
        d = p["out"] - p["in"]
        parts.append(f"[{k}:v]trim={p['in']:.3f}:{p['out']:.3f},setpts=PTS-STARTPTS,"
                     f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
                     f"fps={fps},format=yuv420p,setsar=1[v{k}]")
        if has_audio(p["path"]):
            parts.append(f"[{k}:a]atrim={p['in']:.3f}:{p['out']:.3f},aresample=48000,"
                         f"aformat=channel_layouts=stereo,asetpts=N/SR/TB[a{k}]")
        else:
            parts.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{d:.3f},asetpts=N/SR/TB[a{k}]")
        labels.append(f"[v{k}][a{k}]")
    graph = ";".join(parts) + f";{''.join(labels)}concat=n={len(ps)}:v=1:a=1[v][a];[a]asetpts=N/SR/TB[aout]"
    ffmpeg_run([*args, "-filter_complex", graph, "-map", "[v]", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", str(work.path("source.mp4"))])

    # 2) words of every piece on the joined timeline -> transcript (captions work as usual)
    words, t = [], 0.0
    for p in ps:
        for w in p["item"].get("words") or []:
            if p["in"] - 0.05 <= w["s"] < p["out"]:
                words.append({"w": w["w"], "s": round(t + w["s"] - p["in"], 3), "e": round(t + min(w["e"], p["out"]) - p["in"], 3)})
        t += p["out"] - p["in"]
    total = round(t, 2)
    sentences = transcribe.words_to_sentences(words) if words else [{"start": 0.0, "end": total, "text": "", "words": []}]
    work.save("transcript.json", {"source": "montage", "v": transcribe.TRANSCRIPT_VERSION, "sentences": sentences, "cues": []})

    first = ps[0]["item"]
    channels = list(dict.fromkeys(p["item"].get("channel") for p in ps if p["item"].get("channel")))
    vid = (first.get("key") or "")[:11]
    info = {"id": vid, "title": name, "channel": " & ".join(channels) or "", "duration": total,
            "webpage_url": (first.get("source_url") or "").split("?")[0] or None,
            "montage": True, "sources": [{"title": p["item"].get("video_title"), "url": p["item"].get("source_url")} for p in ps]}
    work.save("info.json", info)

    # 3) the run: one clip that covers the whole joined video
    clip = {"rank": 1, "start": 0.0, "end": total, "i": 0, "j": len(sentences) - 1, "title": name,
            "hook": (sentences[0]["text"] or name)[:140], "reason": f"Made in Favorites from {len(ps)} clips.",
            "score": 0.0, "signals": {}, "manual": True, "style": {"preset": style}, "aspect": aspect, "fit": fit,
            "captions": bool(captions), "images": with_defaults(None)}
    from . import titles

    clip = titles.add_title(clip, sentences, " ".join(filter(None, [p["item"].get("video_title") for p in ps]))[:300], aspect)
    settings = {"count": 1, "aspect": aspect, "fit": fit, "captions": bool(captions), "transcript_source": "montage",
                "work_dir": str(work.dir.resolve()), "rendered": True, "montage": True}
    report.write_json(run_dir, info, report.clip_records(info, [clip], sentences), settings)
    (run_dir / "montage.json").write_text(json.dumps({
        "name": name, "made": time.time(), "aspect": aspect, "fit": fit, "style": style, "captions": bool(captions),
        "pieces": [{"fav": p["fav"], "key": p["key"], "in": p["in"], "out": p["out"], "title": p["item"].get("title")}
                   for p in ps]}, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"Made '{name}' ({total:.1f} s). Rendering it ...")
    if stage:
        stage("Rendering your video", 0.5)
    res = edit_clip(run_dir, 1, {}, log=log, stage=None)
    if stage:
        stage("Done", 1.0)
    return {"folder": run_dir.name, "rank": 1, "seconds_long": total, "clip": res.get("clip")}
