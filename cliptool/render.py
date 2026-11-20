"""Stage 5: cut, frame, caption, loudness-normalise and encode each clip."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

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


# Caption text styles. Each preset sets defaults; the editor can override colours, font, size,
# position, words per line and uppercase per clip. Keep in sync with PRESETS in static/index.html.
PRESETS: dict[str, dict] = {
    "highlight": {"label": "Bold highlight", "font": "Arial Black", "size": 78, "color": "#FFFFFF",
                  "highlight": "#FFD400", "uppercase": True, "words_per_line": 3, "outline": 5, "mode": "color"},
    "box":       {"label": "Word box", "font": "Arial Black", "size": 74, "color": "#FFFFFF",
                  "highlight": "#7C3AED", "uppercase": True, "words_per_line": 3, "outline": 4, "mode": "box"},
    "single":    {"label": "One word pop", "font": "Impact", "size": 120, "color": "#FFFFFF",
                  "highlight": "#22D3EE", "uppercase": True, "words_per_line": 1, "outline": 7, "mode": "pop"},
    "neon":      {"label": "Neon glow", "font": "Arial Black", "size": 76, "color": "#F0ABFC",
                  "highlight": "#39FF14", "uppercase": True, "words_per_line": 3, "outline": 3, "mode": "glow"},
    "minimal":   {"label": "Minimal", "font": "Segoe UI Semibold", "size": 64, "color": "#FFFFFF",
                  "highlight": "#FFFFFF", "uppercase": False, "words_per_line": 5, "outline": 2, "mode": "plain"},
    "subtitle":  {"label": "Classic subtitle", "font": "Arial", "size": 56, "color": "#FFFFFF",
                  "highlight": "#FFFFFF", "uppercase": False, "words_per_line": 7, "outline": 0, "mode": "band"},
    # GTA-themed: Vice City sunset - hot pink text, orange active word, deep purple outline, bold italic.
    "vice":      {"label": "Vice City (GTA)", "font": "Impact", "size": 92, "color": "#FF4FA3",
                  "highlight": "#FFB23F", "uppercase": True, "words_per_line": 2, "outline": 6, "mode": "color",
                  "italic": True, "outline_color": "#2B0B45", "shadow_color": "#7A1FA2"},
}
POSITIONS = ("bottom", "middle", "top")


def caption_style(cfg_captions: dict, override: dict | None) -> dict:
    """config.toml captions -> preset defaults -> per-clip overrides."""
    style = dict(cfg_captions)
    style.setdefault("preset", "highlight")
    style.update({k: v for k, v in (override or {}).items() if v is not None})
    preset = PRESETS.get(style["preset"], PRESETS["highlight"])
    merged = {**preset, "position": "bottom", "scale": 1.0, "margin_bottom": cfg_captions.get("margin_bottom", 520)}
    # Only per-clip overrides beat the preset; config.toml values apply to the default preset only.
    if style["preset"] == "highlight" and not override:
        merged.update({k: cfg_captions[k] for k in ("font", "size", "color", "highlight", "words_per_line", "outline")
                       if k in cfg_captions})
    merged.update({k: v for k, v in (override or {}).items() if v is not None and k != "preset"})
    merged["preset"] = style["preset"] if style["preset"] in PRESETS else "highlight"
    return merged


def build_ass(words: list[dict], clip_start: float, clip_end: float, size: tuple[int, int], cap: dict, aspect: str) -> str:
    """Word-timed captions as an ASS script, in the style `cap` (see caption_style)."""
    cap = caption_style({}, None) | cap if "mode" not in cap else cap
    W, H = size
    aspect_scale = {"vertical": 1.0, "square": 0.8, "original": 0.75}[aspect]
    font_size = int(float(cap["size"]) * aspect_scale * float(cap.get("scale", 1.0)))
    pos = cap.get("position", "bottom")
    align = {"bottom": 2, "middle": 5, "top": 8}.get(pos, 2)
    margin_v = {"bottom": {"vertical": int(cap.get("margin_bottom", 520)), "square": 140, "original": 80}[aspect],
                "middle": 0,
                "top": {"vertical": 300, "square": 120, "original": 70}[aspect]}.get(pos, 0)
    base, hi = _ass_color(cap["color"]), _ass_color(cap["highlight"])
    mode = cap.get("mode", "color")
    outline = int(cap.get("outline", 4))
    border_style, back, shadow = 1, "&H80000000", 2
    if mode == "band":
        border_style, back, outline, shadow = 3, "&H60000000", 14, 0  # translucent box behind each line
    if mode == "glow":
        shadow = 0
    out_col = _ass_color(cap["highlight"]) if mode == "glow" else _ass_color(cap.get("outline_color", "#000000"))
    if cap.get("shadow_color"):
        back, shadow = _ass_color(cap["shadow_color"]), 6  # solid offset shadow (Vice City look)
    italic = -1 if cap.get("italic") else 0
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{cap['font']},{font_size},{base},{base},{out_col},{back},-1,{italic},0,0,100,100,0,0,{border_style},{outline},{shadow},{align},70,70,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    n = max(1, int(cap["words_per_line"]))
    upper = bool(cap.get("uppercase", True))
    words = [w for w in words if clip_start - 0.05 <= w["s"] < clip_end]
    lines = []
    for g in range(0, len(words), n):
        group = words[g:g + n]
        toks = [(_clean(w["w"]).upper() if upper else _clean(w["w"])) for w in group]
        if mode in ("plain", "band"):
            # Whole line at once, no per-word highlight.
            st = group[0]["s"] - clip_start
            en = (words[g + n]["s"] if g + n < len(words) else min(group[-1]["e"] + 0.4, clip_end)) - clip_start
            en = min(en, clip_end - clip_start)
            if en > st:
                lines.append(f"Dialogue: 0,{_ts(st)},{_ts(en)},Cap,,0,0,0,,{' '.join(toks)}")
            continue
        for k, w in enumerate(group):
            st = w["s"] - clip_start
            en = (group[k + 1]["s"] if k + 1 < len(group) else
                  (words[g + n]["s"] if g + n < len(words) else min(group[-1]["e"] + 0.4, clip_end))) - clip_start
            en = min(en, clip_end - clip_start)
            if en <= st:
                continue
            if mode == "pop":
                text = f"{{\\c{hi}\\fscx125\\fscy125\\t(0,90,\\fscx100\\fscy100)}}{toks[k]}"
            elif mode == "box":
                # Thick outline in the highlight colour reads as a box behind the active word.
                text = " ".join(f"{{\\3c{hi}\\bord{outline + 14}\\shad0}}{t}{{\\3c&H00000000&\\bord{outline}\\shad2}}"
                                if x == k else t for x, t in enumerate(toks))
            else:  # color, glow
                text = " ".join(f"{{\\c{hi}}}{t}{{\\c{base}}}" if x == k else t for x, t in enumerate(toks))
            if mode == "glow":
                # Layer 0: soft coloured halo (transparent fill, blurred thick outline). Layer 1: crisp text.
                lines.append(f"Dialogue: 0,{_ts(st)},{_ts(en)},Cap,,0,0,0,,{{\\1a&HFF&\\bord{outline + 8}\\blur10}}{text}")
                lines.append(f"Dialogue: 1,{_ts(st)},{_ts(en)},Cap,,0,0,0,,{{\\bord2\\3c&H00101010&}}{text}")
            else:
                lines.append(f"Dialogue: 0,{_ts(st)},{_ts(en)},Cap,,0,0,0,,{text}")
    return header + "\n".join(lines) + "\n"


def words_between(sentences: list[dict], start: float, end: float) -> list[dict]:
    return [w for s in sentences if s["end"] > start - 1 and s["start"] < end + 1 for w in s["words"]
            if start - 0.05 <= w["s"] < end and w["w"] not in (">>", "&gt;&gt;")]


def align_text(words: list[dict], text: str) -> list[dict]:
    """Put edited caption text onto the original word timings.

    Same word count -> 1:1. Otherwise the new words are spread over the same time
    span in proportion to their length, so captions stay in sync with speech.
    """
    new = text.split()
    if not words or not new:
        return [] if not new else words
    if len(new) == len(words):
        return [{**w, "w": t} for w, t in zip(words, new)]
    t0, t1 = words[0]["s"], words[-1]["e"]
    weights = [len(t) + 2 for t in new]
    total = sum(weights)
    out, acc = [], 0.0
    for t, wt in zip(new, weights):
        s = t0 + (t1 - t0) * acc / total
        acc += wt
        out.append({"w": t, "s": round(s, 3), "e": round(t0 + (t1 - t0) * acc / total, 3)})
    return out


FITS = ("auto", "crop", "blur", "black", "stack")

# Look presets for the editor's filter panel (values match the sliders).
FILTER_PRESETS: dict[str, dict] = {
    "none":      {"label": "None", "brightness": 0, "contrast": 1, "saturation": 1, "warmth": 0, "sharpen": 0, "vignette": False},
    "vivid":     {"label": "Vivid", "brightness": 0.02, "contrast": 1.12, "saturation": 1.4, "warmth": 0, "sharpen": 0.6, "vignette": False},
    "cinematic": {"label": "Cinematic", "brightness": -0.03, "contrast": 1.18, "saturation": 0.85, "warmth": 0.25, "sharpen": 0.3, "vignette": True},
    "warm":      {"label": "Warm", "brightness": 0.02, "contrast": 1.05, "saturation": 1.1, "warmth": 0.5, "sharpen": 0, "vignette": False},
    "cool":      {"label": "Cool", "brightness": 0, "contrast": 1.05, "saturation": 1.0, "warmth": -0.5, "sharpen": 0, "vignette": False},
    "bw":        {"label": "Black & white", "brightness": 0, "contrast": 1.2, "saturation": 0, "warmth": 0, "sharpen": 0.3, "vignette": True},
    "vice":      {"label": "Vice sunset", "brightness": 0.03, "contrast": 1.1, "saturation": 1.35, "warmth": 0.35, "sharpen": 0.4, "vignette": True},
}
FILTER_LIMITS = {"brightness": (-0.3, 0.3), "contrast": (0.5, 1.8), "saturation": (0.0, 2.5),
                 "warmth": (-1.0, 1.0), "sharpen": (0.0, 1.5)}

# Top-right corner, the most common facecam spot; the editor lets you move it.
DEFAULT_FACECAM = {"x": 0.70, "y": 0.04, "w": 0.28, "h": 0.30}


def filter_chain(f: dict | None) -> str:
    """ffmpeg filters for the look sliders; '' when everything is neutral."""
    if not f:
        return ""
    parts = []
    b, c, s = float(f.get("brightness", 0)), float(f.get("contrast", 1)), float(f.get("saturation", 1))
    if abs(b) > 1e-3 or abs(c - 1) > 1e-3 or abs(s - 1) > 1e-3:
        parts.append(f"eq=brightness={b:.3f}:contrast={c:.3f}:saturation={s:.3f}")
    w = float(f.get("warmth", 0))
    if abs(w) > 1e-3:
        parts.append(f"colorbalance=rs={0.12 * w:.3f}:gs={0.02 * w:.3f}:bs={-0.12 * w:.3f}"
                     f":rm={0.08 * w:.3f}:bm={-0.08 * w:.3f}")
    sh = float(f.get("sharpen", 0))
    if sh > 1e-3:
        parts.append(f"unsharp=5:5:{sh:.2f}:5:5:0")
    if f.get("vignette"):
        parts.append("vignette=angle=PI/4.5")
    return ",".join(parts)


def _layout(aspect: str, fit: str, x: int | None, source: Path | None, clip: dict) -> str:
    """Filtergraph from [0:v] to [base]: the frame laid out at the output size, before look and captions."""
    W, H = SIZES[aspect]
    if aspect == "vertical" and fit == "stack" and source is not None:
        from .framing import video_size

        sw, sh = video_size(str(source))
        fc = clip.get("facecam") or DEFAULT_FACECAM
        game_h = W  # 1:1 game area at the bottom
        top = H - game_h  # facecam band on top
        scaled_w = int(round(sw * game_h / sh / 2) * 2)
        gx = max(0, min(scaled_w - W, int(float(clip.get("game_x", 0.5)) * scaled_w - W / 2)))
        return (f"[0:v]split=2[cam][game];"
                f"[cam]crop=iw*{fc['w']:.4f}:ih*{fc['h']:.4f}:iw*{fc['x']:.4f}:ih*{fc['y']:.4f},"
                f"scale={W}:{top}:force_original_aspect_ratio=increase,crop={W}:{top}[top];"
                f"[game]scale=-2:{game_h},crop={W}:{game_h}:{gx}:0[bot];"
                f"[top][bot]vstack=2,setsar=1[base]")
    if aspect == "vertical" and fit == "pan":
        # pan.cmd (written next to the clip) moves the crop window frame by frame.
        return f"[0:v]scale=-2:{H},sendcmd=f=pan.cmd,crop@pan={W}:{H}:{x or 0}:0,setsar=1[base]"
    if aspect == "vertical" and fit == "crop" and x is not None:
        return f"[0:v]scale=-2:{H},crop={W}:{H}:{x}:0,setsar=1[base]"
    if aspect == "original":
        return f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,setsar=1[base]"
    if fit == "black":
        return (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[base]")
    if fit in ("blur", "stack"):
        return (f"[0:v]split=2[a][b];"
                f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:3,eq=brightness=-0.08[bg];"
                f"[b]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base]")
    return f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[base]"


def _video_filter(aspect: str, fit: str, captions: bool, x: int | None = None,
                  source: Path | None = None, clip: dict | None = None) -> str:
    graph = _layout(aspect, fit, x, source, clip or {})
    tail = [p for p in (filter_chain((clip or {}).get("filters")), "ass=subs.ass" if captions else "") if p]
    return graph + ";[base]" + (",".join(tail) if tail else "null") + "[v]"


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
        words = words_between(sentences, clip["start"], clip["end"])
        if clip.get("caption_text"):
            words = align_text(words, clip["caption_text"])
        style = caption_style(cfg["captions"], clip.get("style"))
        (clips_dir / "subs.ass").write_text(
            build_ass(words, start, start + dur, SIZES[aspect], style, aspect), encoding="utf-8")

    x = None
    used_fit = fit
    if aspect != "vertical" and fit == "stack":
        used_fit = "blur"
    if aspect == "vertical" and fit == "stack" and not clip.get("facecam"):
        from .framing import detect_facecam

        found = detect_facecam(source, start, dur)
        if found:
            clip["facecam"] = found
        else:
            used_fit = "blur"  # no facecam to put on top: keep the full frame instead
    if aspect == "vertical" and fit == "auto":
        from .framing import choose_framing, crop_x, pan_commands

        plan = choose_framing(source, start, dur)
        used_fit = plan["mode"]
        if used_fit == "crop":
            x = crop_x(source, plan["x"])
        elif used_fit == "pan":
            (clips_dir / "pan.cmd").write_text(pan_commands(source, plan["path"], dur), encoding="utf-8")
            x = crop_x(source, plan["path"][0][1])
    elif aspect == "vertical" and fit == "crop":
        from .framing import crop_x

        x = crop_x(source, float(clip.get("crop_x", 0.5)))

    enc = cfg["encode"]
    ffmpeg_run([
        "-ss", f"{start:.3f}", "-i", str(source.resolve()), "-t", f"{dur:.3f}",
        "-filter_complex", _video_filter(aspect, used_fit, captions, x, source, clip),
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
            "framing": used_fit if aspect == "vertical" else aspect, "facecam": clip.get("facecam")}
