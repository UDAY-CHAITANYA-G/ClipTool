"""cliptool command line.

  cliptool <url> [options]          find, cut and format the best clips (fully offline ranking)
  cliptool serve                    local website at http://127.0.0.1:8765
  cliptool render <output folder>   re-render a previous run (new aspect, captions...)
  cliptool doctor                   check ffmpeg, yt-dlp and optional extras
  cliptool clean                    delete the work/ cache
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import load_config

SUBCOMMANDS = {"render", "doctor", "clean", "run", "serve"}
FITS = ["auto", "crop", "blur", "black", "stack"]


def _add_render_opts(p: argparse.ArgumentParser) -> None:
    p.add_argument("--aspect", choices=["vertical", "portrait", "square", "original"], default="vertical",
                   help="vertical 9:16 (default), portrait 4:5, square 1:1 or original 16:9")
    p.add_argument("--fit", choices=FITS, default="auto",
                   help="vertical layout: auto (follow the action; default), crop (centre), blur / black "
                        "(full 16:9 frame on a blurred / black background) or stack (facecam on top, 1:1 game below)")
    p.add_argument("--captions", action=argparse.BooleanOptionalAction, default=True,
                   help="burn word-timed captions (default on)")
    p.add_argument("--config", type=Path, help="path to config.toml")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cliptool", description="Find the best clips in a YouTube video.")
    p.add_argument("--version", action="version", version=f"cliptool {__version__}")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="find, cut and format clips from a YouTube URL")
    r.add_argument("url")
    r.add_argument("--count", type=int, help="number of clips (default 5)")
    r.add_argument("--min", dest="min_s", type=float, help="minimum clip length in seconds (default 20)")
    r.add_argument("--max", dest="max_s", type=float, help="maximum clip length in seconds (default 60)")
    r.add_argument("--ai", action="store_true",
                   help="optional: also ask Claude to rank and title clips (needs an Anthropic API key)")
    r.add_argument("--model", help="Claude model when --ai is used (default claude-opus-5-5)")
    r.add_argument("--style", default="highlight", help="caption style: highlight, box, single, neon, minimal, subtitle, vice")
    r.add_argument("--lang", default="en", help="caption/transcription language (default en)")
    r.add_argument("--whisper", action="store_true", help="force local transcription with faster-whisper")
    r.add_argument("--whisper-model", default="small", help="faster-whisper model size (default small)")
    r.add_argument("--dry-run", action="store_true", help="score and list clips without rendering video")
    r.add_argument("--out", type=Path, default=Path("output"), help="output folder (default output/)")
    r.add_argument("--work", type=Path, default=Path("work"), help="cache folder (default work/)")
    r.add_argument("--cookies-from-browser", help="e.g. chrome, edge, firefox (for age-restricted videos)")
    _add_render_opts(r)

    s = sub.add_parser("serve", help="run the local website")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--open", action="store_true", help="open the browser")

    rr = sub.add_parser("render", help="re-render clips of a previous run")
    rr.add_argument("folder", type=Path)
    _add_render_opts(rr)

    sub.add_parser("doctor", help="check the setup")
    c = sub.add_parser("clean", help="delete downloaded videos (work/); --all also deletes every clip (output/)")
    c.add_argument("--all", action="store_true", help="also delete all clips in output/")
    c.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    c.add_argument("--work", type=Path, default=Path("work"))
    c.add_argument("--out", type=Path, default=Path("output"))
    return p


def cmd_run(a: argparse.Namespace) -> int:
    from . import pipeline, report

    o = pipeline.Options(
        url=a.url, count=a.count, min_s=a.min_s, max_s=a.max_s, aspect=a.aspect, fit=a.fit,
        captions=a.captions, caption_style=a.style, ai=a.ai, model=a.model, lang=a.lang, whisper=a.whisper,
        whisper_model=a.whisper_model, dry_run=a.dry_run, out=a.out, work=a.work,
        cookies_from_browser=a.cookies_from_browser, config=a.config)
    res = pipeline.run(o, log=lambda m: print(m, flush=True))
    report.print_table(res["clips"])
    print(f"Done in {res['seconds']:.0f}s.")
    print(f"Clip list: {res['page'].resolve()}")
    return 0


def cmd_serve(a: argparse.Namespace) -> int:
    from .web import serve

    serve(a.host, a.port, open_browser=a.open)
    return 0


def cmd_render(a: argparse.Namespace) -> int:
    from . import render, report
    from .cache import Work

    data = json.loads((a.folder / "clips.json").read_text(encoding="utf-8"))
    work_dir = Path(data["settings"]["work_dir"])
    work = Work(work_dir.parent, work_dir.name)
    source = next((p for p in work.dir.glob("source.*") if p.suffix in (".mp4", ".mkv", ".webm")), None)
    if not source:
        print("error: source video is gone from the work cache; run `cliptool <url>` again.", file=sys.stderr)
        return 1
    sentences = work.load("transcript.json")["sentences"]
    info = json.loads((work.dir / "info.json").read_text(encoding="utf-8"))
    cfg = load_config(a.config)
    old = a.folder / "clips"
    if old.exists():
        shutil.rmtree(old)
    clips = []
    for r in data["clips"]:
        c = dict(r)
        print(f"  [{r['rank']}/{len(data['clips'])}] {r['title']}", flush=True)
        c.update(render.render_clip(source, c, sentences, a.folder, r["rank"], a.aspect, a.fit, a.captions, cfg))
        clips.append(c)
    recs = report.clip_records(info, clips, sentences)
    data["settings"].update({"aspect": a.aspect, "fit": a.fit, "captions": a.captions, "rendered": True})
    report.write_json(a.folder, info, recs, data["settings"])
    page = report.write_html(a.folder, info, recs)
    print(f"Clip list: {page.resolve()}")
    return 0


def cmd_doctor(_: argparse.Namespace) -> int:
    ok = True
    try:
        from .ffmpeg import ffmpeg_exe
        print(f"ffmpeg      : {ffmpeg_exe()}")
    except Exception as e:
        ok = False
        print(f"ffmpeg      : MISSING ({e})")
    try:
        import yt_dlp
        print(f"yt-dlp      : {yt_dlp.version.__version__}")
    except ImportError:
        ok = False
        print("yt-dlp      : MISSING")
    print(f"node (JS)   : {shutil.which('node') or 'not found (YouTube may need a JS runtime)'}")
    try:
        import cv2
        has = Path(cv2.data.haarcascades, "haarcascade_frontalface_default.xml").exists()
        print(f"opencv      : {cv2.__version__}{'' if has else ' (face model MISSING - auto framing falls back to blur)'}")
    except ImportError:
        ok = False
        print("opencv      : MISSING (needed for face-aware framing)")
    try:
        import faster_whisper  # noqa: F401
        print("whisper     : installed")
    except ImportError:
        print("whisper     : not installed (optional; only for videos without captions)")
    print("AI ranking  : off by default (optional --ai needs an Anthropic API key)")
    return 0 if ok else 1


def cmd_clean(a: argparse.Namespace) -> int:
    targets = [p for p in ([a.work] + ([a.out] if a.all else [])) if p.exists()]
    if not targets:
        print("Nothing to clean.")
        return 0
    size = sum(f.stat().st_size for t in targets for f in t.rglob("*") if f.is_file())
    what = " and ".join(str(t.resolve()) for t in targets)
    if not a.yes:
        ans = input(f"Permanently delete {what} ({size / 1e6:.0f} MB)? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("Cancelled.")
            return 0
    for t in targets:
        shutil.rmtree(t)
    print(f"Deleted {what} ({size / 1e6:.0f} MB freed).")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] not in SUBCOMMANDS and not argv[0].startswith("-"):
        argv.insert(0, "run")
    parser = build_parser()
    a = parser.parse_args(argv)
    if not a.cmd:
        parser.print_help()
        return 0
    handlers = {"run": cmd_run, "serve": cmd_serve, "render": cmd_render, "doctor": cmd_doctor, "clean": cmd_clean}
    try:
        return handlers[a.cmd](a)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as e:
        print(f"\nerror: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
