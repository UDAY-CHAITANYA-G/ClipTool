"""Stage 5: cut, frame, caption, loudness-normalise and encode each clip."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .candidates import window_words
from .ffmpeg import run as ffmpeg_run

SIZES = {"vertical": (1080, 1920), "square": (1080, 1080), "original": (1920, 1080)}


def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:max_len].rstrip("-") or "clip"


def _ass_color(hex_rgb: str) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _clean(word: str) -> str:
    return re.sub(r"[{}\\]", "", word)


def build_ass(words: list[dict], clip_start: float, clip_end: float, size: tuple[int, int], cap: dict, aspect: str) -> str:
    W, H = size
    scale = {"vertical": 1.0, "square": 0.8, "original": 0.75}[aspect]
    font_size = int(cap["size"] * scale)
    margin_v = {"vertical": int(cap["margin_bottom"]), "square": 140, "original": 80}[aspect]
    base, hi = _ass_color(cap["color"]), _ass_color(cap["highlight"])
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{cap['font']},{font_size},{base},{base},&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{cap['outline']},2,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    n = max(1, int(cap["words_per_line"]))
    words = [w for w in words if clip_start - 0.05 <= w["s"] < clip_end]
    lines = []
    for g in range(0, len(words), n):
        group = words[g:g + n]
        toks = [_clean(w["w"]).upper() for w in group]
        for k, w in enumerate(group):
            st = w["s"] - clip_start
            en = (group[k + 1]["s"] if k + 1 < len(group) else
                  (words[g + n]["s"] if g + n < len(words) else min(group[-1]["e"] + 0.4, clip_end))) - clip_start
            en = min(en, clip_end - clip_start)
            if en <= st:
                continue
            text = " ".join(f"{{\\c{hi}}}{t}{{\\c{base}}}" if x == k else t for x, t in enumerate(toks))
            lines.append(f"Dialogue: 0,{_ts(st)},{_ts(en)},Cap,,0,0,0,,{text}")
    return header + "\n".join(lines) + "\n"


def _video_filter(aspect: str, fit: str, captions: bool, x: int | None = None) -> str:
    W, H = SIZES[aspect]
    if aspect == "vertical" and fit == "pan":
        # pan.cmd (written next to the clip) moves the crop window frame by frame.
        chain = f"[0:v]scale=-2:{H},sendcmd=f=pan.cmd,crop@pan={W}:{H}:{x or 0}:0,setsar=1"
    elif aspect == "vertical" and fit == "crop" and x is not None:
        chain = f"[0:v]scale=-2:{H},crop={W}:{H}:{x}:0,setsar=1"
    elif aspect == "original":
        chain = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    elif fit == "blur":
        chain = (f"[0:v]split=2[a][b];"
                 f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:3,eq=brightness=-0.08[bg];"
                 f"[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1")
    else:
        chain = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
    if captions:
        chain += ",ass=subs.ass"
    return chain + "[v]"


def render_clip(source: Path, clip: dict, sentences: list[dict], out_dir: Path, rank: int,
                aspect: str, fit: str, captions: bool, cfg: dict) -> dict:
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    name = f"{rank:02d}_{slugify(clip['title'])}"
    mp4, jpg = clips_dir / f"{name}.mp4", clips_dir / f"{name}.jpg"
    start, dur = clip["start"], clip["end"] - clip["start"]
    # Small lead-in/out so the first and last words are not clipped.
    pre = min(0.15, start)
    start, dur = start - pre, dur + pre + 0.25

    if captions:
        words = window_words(sentences, clip)
        (clips_dir / "subs.ass").write_text(
            build_ass(words, start, start + dur, SIZES[aspect], cfg["captions"], aspect), encoding="utf-8")

    x = None
    used_fit = fit
    if aspect == "vertical" and fit == "auto":
        from .framing import choose_framing, crop_x, pan_commands

        plan = choose_framing(source, start, dur)
        used_fit = plan["mode"]
        if used_fit == "crop":
            x = crop_x(source, plan["x"])
        elif used_fit == "pan":
            (clips_dir / "pan.cmd").write_text(pan_commands(source, plan["path"], dur), encoding="utf-8")
            x = crop_x(source, plan["path"][0][1])

    enc = cfg["encode"]
    ffmpeg_run([
        "-ss", f"{start:.3f}", "-i", str(source.resolve()), "-t", f"{dur:.3f}",
        "-filter_complex", _video_filter(aspect, used_fit, captions, x),
        "-map", "[v]", "-map", "0:a:0?",
        "-af", f"loudnorm=I={enc['loudness_lufs']}:TP=-1.5:LRA=11,aresample=48000",
        "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-preset", str(enc["preset"]), "-crf", str(enc["crf"]),
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
        mp4.name,
    ], cwd=clips_dir)
    ffmpeg_run(["-i", mp4.name, "-t", "3", "-vf", "thumbnail=40", "-frames:v", "1", "-q:v", "3", jpg.name], cwd=clips_dir)
    (clips_dir / "subs.ass").unlink(missing_ok=True)
    (clips_dir / "pan.cmd").unlink(missing_ok=True)
    return {"file": f"clips/{mp4.name}", "thumbnail": f"clips/{jpg.name}",
            "framing": used_fit if aspect == "vertical" else aspect}
