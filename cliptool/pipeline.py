"""The full link -> clips pipeline, shared by the CLI and the local website."""
from __future__ import annotations

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
        for k, c in enumerate(clips, 1):
            at(f"Rendering clip {k} of {len(clips)}", 0.55 + 0.43 * (k - 1) / len(clips))
            log(f"  {c['title']}")
            c.update(render.render_clip(source, c, sentences, out_dir, k, o.aspect, o.fit, o.captions, cfg))

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
