"""Stage 5: lay out, filter, caption, overlay text, mix audio and encode each clip.

Everything a clip can be edited with lives on the clip dict (see EDIT_KEYS in pipeline.py):

  layout     fit (auto | crop | blur | black | stack), aspect (vertical | portrait | square | original),
             zoom, crop_x / crop_y, facecam {x,y,w,h}, cam_share, cam_pos (top | bottom),
             game_x / game_y / game_zoom
  timing     start / end (source seconds), speed (0.5-2x)
  look       filters {brightness, contrast, saturation, warmth, sharpen, vignette}
  text       style (caption style), captions on/off, caption_text, texts [overlay boxes]
  audio      volume, mute, fade_in / fade_out (also fade the picture)
  extras     progress_bar {on, color, position}, cover_t, quality (small | standard | high)
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from .ffmpeg import run as ffmpeg_run

# Output formats. cap_scale scales caption font sizes (presets are tuned for 1080x1920);
# bottom / top are caption margins in output pixels.
ASPECTS: dict[str, dict] = {
    "vertical": {"label": "9:16", "size": (1080, 1920), "cap_scale": 1.0, "bottom": 520, "top": 300},
    "portrait": {"label": "4:5", "size": (1080, 1350), "cap_scale": 0.9, "bottom": 250, "top": 170},
    "square":   {"label": "1:1", "size": (1080, 1080), "cap_scale": 0.8, "bottom": 140, "top": 120},
    "original": {"label": "16:9", "size": (1920, 1080), "cap_scale": 0.75, "bottom": 80, "top": 70},
}
SIZES = {k: v["size"] for k, v in ASPECTS.items()}
FITS = ("auto", "crop", "blur", "black", "stack")
QUALITY = {"small": 24, "standard": 20, "high": 17}
FONTS = ("Arial Black", "Impact", "Arial", "Segoe UI", "Segoe UI Semibold", "Segoe UI Black", "Bahnschrift", "Georgia",
         "Trebuchet MS", "Verdana", "Comic Sans MS", "Courier New")

# Top-right corner, the most common facecam spot; the editor lets you move it.
DEFAULT_FACECAM = {"x": 0.70, "y": 0.04, "w": 0.28, "h": 0.30}
DEFAULT_CAM_SHARE = 840 / 1920  # facecam band height as a share of the frame (stack layout)


def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:max_len].rstrip("-") or "clip"


def _ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ts(t: float) -> str:
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _clean(word: str) -> str:
    return re.sub(r"[{}\\]", "", word)


# ---------------------------------------------------------------------------------------------
# Captions
# ---------------------------------------------------------------------------------------------

# Caption text styles. Each preset sets defaults; the editor can override colours, font, size,
# position, words per line and uppercase per clip. The editor reads these from /api/presets.
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
    # ---- GTA 6 themes (group "gta"): each a different caption format ----
    # Vice City sunset: hot pink text, orange active word, deep purple outline, bold italic.
    "vice":      {"label": "Vice City", "group": "gta", "font": "Impact", "size": 92, "color": "#FF4FA3",
                  "highlight": "#FFB23F", "uppercase": True, "words_per_line": 2, "outline": 6, "mode": "color",
                  "italic": True, "outline_color": "#2B0B45", "shadow_color": "#7A1FA2"},
    # Wasted: one huge red word at a time, popping in, centred on screen.
    "wasted":    {"label": "Wasted", "group": "gta", "font": "Impact", "size": 150, "color": "#E0242F",
                  "highlight": "#E0242F", "uppercase": True, "words_per_line": 1, "outline": 8, "mode": "pop",
                  "outline_color": "#000000", "spacing": 3, "position": "middle"},
    # Mission Passed: gold, letter-spaced, hard black drop shadow, white active word.
    "mission":   {"label": "Mission Passed", "group": "gta", "font": "Arial Black", "size": 80, "color": "#F5C542",
                  "highlight": "#FFFFFF", "uppercase": True, "words_per_line": 3, "outline": 5, "mode": "color",
                  "outline_color": "#1A1206", "shadow_color": "#000000", "spacing": 2},
    # Leonida Neon: cyan text with a hot-pink neon glow on the active word.
    "leonida":   {"label": "Leonida Neon", "group": "gta", "font": "Arial Black", "size": 80, "color": "#2EF2FF",
                  "highlight": "#FF3EA5", "uppercase": True, "words_per_line": 3, "outline": 3, "mode": "glow"},
    # Heist Cash: white words, the spoken word on a money-green box.
    "heist":     {"label": "Heist Cash", "group": "gta", "font": "Impact", "size": 88, "color": "#FFFFFF",
                  "highlight": "#1FB45A", "uppercase": True, "words_per_line": 3, "outline": 5, "mode": "box"},
    # Wanted: italic white, gold active word, wide spacing, dark amber shadow.
    "wanted":    {"label": "Wanted", "group": "gta", "font": "Bahnschrift", "size": 84, "color": "#FFFFFF",
                  "highlight": "#FFC81E", "uppercase": True, "words_per_line": 2, "outline": 6, "mode": "color",
                  "italic": True, "outline_color": "#000000", "shadow_color": "#5A3E00", "spacing": 4},
    # Phone Text: in-game phone message - sentence case on a translucent indigo band, at the top.
    "phone":     {"label": "Phone Text", "group": "gta", "font": "Segoe UI", "size": 58, "color": "#FFFFFF",
                  "highlight": "#FFFFFF", "uppercase": False, "words_per_line": 6, "outline": 0, "mode": "band",
                  "band_color": "#1E1B4B", "position": "top"},
    # Ocean Drive: peach serif italic, pink active word, orange sunset offset shadow.
    "ocean":     {"label": "Ocean Drive", "group": "gta", "font": "Georgia", "size": 86, "color": "#FFD6A5",
                  "highlight": "#FF5E8A", "uppercase": False, "words_per_line": 2, "outline": 4, "mode": "color",
                  "italic": True, "outline_color": "#3B0A45", "shadow_color": "#FF8A00"},
}
PRESET_GROUPS = {"standard": "Standard", "gta": "GTA 6 themes"}
EFFECTS = ("color", "box", "pop", "glow", "plain")  # how the spoken word is shown ("plain" = no highlight)


def _style_defaults(p: dict) -> dict:
    """Spell out every adjustable caption field for a preset, so the editor can change any of them."""
    mode = p.get("mode", "color")
    band = mode == "band"
    d = {"bold": True, "italic": False, "outline_color": "#000000", "spacing": 0,
         "shadow": 0 if band or mode == "glow" else (6 if p.get("shadow_color") else 2),
         "shadow_color": "#000000", "shadow_opacity": 1.0 if p.get("shadow_color") else 0.5,
         "background": band, "band_color": "#000000", "band_opacity": 0.75 if p.get("band_color") else 0.62}
    out = {**d, **p}
    if band:
        out["mode"] = "plain"  # a band is "no highlight" + background on
    return out


PRESETS = {k: _style_defaults(v) for k, v in PRESETS.items()}
POSITIONS = ("bottom", "middle", "top")


def caption_style(cfg_captions: dict, override: dict | None) -> dict:
    """config.toml captions -> preset defaults -> per-clip overrides."""
    style = dict(cfg_captions)
    style.setdefault("preset", "highlight")
    style.update({k: v for k, v in (override or {}).items() if v is not None})
    preset = PRESETS.get(style["preset"], PRESETS["highlight"])
    merged = {**preset, "position": preset.get("position", "bottom"), "scale": 1.0}
    # Only per-clip overrides beat the preset; config.toml values apply to the default preset only.
    if style["preset"] == "highlight" and not override:
        merged.update({k: cfg_captions[k] for k in ("font", "size", "color", "highlight", "words_per_line", "outline")
                       if k in cfg_captions})
    merged.update({k: v for k, v in (override or {}).items() if v is not None and k != "preset"})
    merged["preset"] = style["preset"] if style["preset"] in PRESETS else "highlight"
    return merged


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{styles}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _alpha(opacity: float) -> int:
    return int(round((1 - min(1.0, max(0.0, float(opacity)))) * 255))


def _caption_style_line(cap: dict, aspect: str) -> str:
    a = ASPECTS[aspect]
    font_size = int(float(cap["size"]) * a["cap_scale"] * float(cap.get("scale", 1.0)))
    pos = cap.get("position", "bottom")
    align = {"bottom": 2, "middle": 5, "top": 8}.get(pos, 2)
    margin_v = {"bottom": a["bottom"], "middle": 0, "top": a["top"]}.get(pos, 0)
    base = _ass_color(cap["color"])
    mode = cap.get("mode", "color")
    if cap.get("background") or mode == "band":
        # Box behind each line: libass fills it with the back colour; outline is the box padding.
        border_style, outline, shadow = 3, 14, 0
        back = _ass_color(cap.get("band_color", "#000000"), alpha=_alpha(cap.get("band_opacity", 0.62)))
    else:
        border_style, outline, shadow = 1, int(cap.get("outline", 4)), int(cap.get("shadow", 2))
        back = _ass_color(cap.get("shadow_color", "#000000"), alpha=_alpha(cap.get("shadow_opacity", 0.5)))
    out_col = _ass_color(cap["highlight"]) if mode == "glow" else _ass_color(cap.get("outline_color", "#000000"))
    bold = -1 if cap.get("bold", True) else 0
    italic = -1 if cap.get("italic") else 0
    spacing = int(cap.get("spacing", 0))
    return (f"Style: Cap,{cap['font']},{font_size},{base},{base},{out_col},{back},{bold},{italic},0,0,100,100,{spacing},0,"
            f"{border_style},{outline},{shadow},{align},70,70,{margin_v},1")


def caption_events(words: list[dict], clip_start: float, clip_end: float, cap: dict, speed: float = 1.0) -> list[str]:
    """Dialogue lines for word-timed captions. Times are output seconds (source time / speed)."""
    hi, base = _ass_color(cap["highlight"]), _ass_color(cap["color"])
    mode = cap.get("mode", "color")
    outline = int(cap.get("outline", 4))
    oc, shad = _ass_color(cap.get("outline_color", "#000000")), int(cap.get("shadow", 2))
    n = max(1, int(cap["words_per_line"]))
    upper = bool(cap.get("uppercase", True))
    words = [w for w in words if clip_start - 0.05 <= w["s"] < clip_end]
    T = lambda t: (t - clip_start) / speed  # noqa: E731
    out_end = T(clip_end)
    lines = []
    for g in range(0, len(words), n):
        group = words[g:g + n]
        toks = [(_clean(w["w"]).upper() if upper else _clean(w["w"])) for w in group]
        group_end = words[g + n]["s"] if g + n < len(words) else min(group[-1]["e"] + 0.4, clip_end)
        if mode in ("plain", "band"):
            st, en = T(group[0]["s"]), min(T(group_end), out_end)
            if en > st:
                lines.append(f"Dialogue: 0,{_ts(st)},{_ts(en)},Cap,,0,0,0,,{' '.join(toks)}")
            continue
        for k, w in enumerate(group):
            st = T(w["s"])
            en = min(T(group[k + 1]["s"] if k + 1 < len(group) else group_end), out_end)
            if en <= st:
                continue
            if mode == "pop":
                text = f"{{\\c{hi}\\fscx125\\fscy125\\t(0,90,\\fscx100\\fscy100)}}{toks[k]}"
            elif mode == "box":
                # Thick outline in the highlight colour reads as a box behind the active word.
                text = " ".join(f"{{\\3c{hi}\\bord{outline + 14}\\shad0}}{t}{{\\3c{oc}\\bord{outline}\\shad{shad}}}"
                                if x == k else t for x, t in enumerate(toks))
            else:  # color, glow
                text = " ".join(f"{{\\c{hi}}}{t}{{\\c{base}}}" if x == k else t for x, t in enumerate(toks))
            if mode == "glow":
                # Layer 0: soft coloured halo (transparent fill, blurred thick outline). Layer 1: crisp text.
                lines.append(f"Dialogue: 0,{_ts(st)},{_ts(en)},Cap,,0,0,0,,{{\\1a&HFF&\\bord{outline + 8}\\blur10}}{text}")
                lines.append(f"Dialogue: 1,{_ts(st)},{_ts(en)},Cap,,0,0,0,,{{\\bord2\\3c&H00101010&}}{text}")
            else:
                lines.append(f"Dialogue: 0,{_ts(st)},{_ts(en)},Cap,,0,0,0,,{text}")
    return lines


def text_events(texts: list[dict], size: tuple[int, int], out_dur: float) -> list[str]:
    """Free text overlays: each at its own position, font, colours and time range (output seconds)."""
    W, H = size
    lines = []
    for t in texts or []:
        content = _clean(str(t.get("text", ""))).strip()
        if not content:
            continue
        content = content.replace("\r", "").replace("\n", "\\N")
        st = max(0.0, float(t.get("start", 0)))
        en = min(out_dur, float(t.get("end", out_dur)))
        if en <= st:
            continue
        x, y = float(t.get("x", 0.5)) * W, float(t.get("y", 0.15)) * H
        tags = [f"\\an5\\pos({x:.0f},{y:.0f})", f"\\fn{t.get('font', 'Arial Black')}",
                f"\\fs{int(t.get('size', 72))}", f"\\c{_ass_color(t.get('color', '#FFFFFF'))}",
                f"\\b{1 if t.get('bold', True) else 0}", f"\\i{1 if t.get('italic') else 0}"]
        style = "TxtBox" if t.get("box") else "Txt"
        if t.get("box"):
            alpha = int(round((1 - float(t.get("box_opacity", 0.85))) * 255))
            tags += [f"\\3c{_ass_color(t.get('box_color', '#000000'))}", f"\\3a&H{alpha:02X}&",
                     f"\\bord{max(6, int(t.get('size', 72)) // 5)}", "\\shad0"]
        else:
            tags += [f"\\3c{_ass_color(t.get('outline_color', '#000000'))}", f"\\bord{int(t.get('outline', 4))}",
                     f"\\shad{2 if t.get('shadow', True) else 0}"]
        anim = t.get("anim", "fade")
        if anim == "fade":
            tags.append("\\fad(200,200)")
        elif anim == "pop":
            tags.append("\\fscx40\\fscy40\\t(0,160,\\fscx100\\fscy100)\\fad(80,150)")
        lines.append(f"Dialogue: 5,{_ts(st)},{_ts(en)},{style},,0,0,0,,{{{''.join(tags)}}}{content}")
    return lines


def build_ass(words: list[dict], clip_start: float, clip_end: float, size: tuple[int, int], cap: dict,
              aspect: str, speed: float = 1.0, texts: list[dict] | None = None, captions: bool = True) -> str:
    """Captions (+ optional text overlays) as one ASS script sized to the output frame."""
    cap = caption_style({}, None) | cap if "mode" not in cap else cap
    W, H = size
    styles = [_caption_style_line(cap, aspect),
              "Style: Txt,Arial Black,72,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,5,0,0,0,1",
              "Style: TxtBox,Arial Black,72,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,3,12,0,5,0,0,0,1"]
    lines = caption_events(words, clip_start, clip_end, cap, speed) if captions else []
    lines += text_events(texts or [], size, (clip_end - clip_start) / speed)
    return ASS_HEADER.format(W=W, H=H, styles="\n".join(styles)) + "\n".join(lines) + "\n"


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


# ---------------------------------------------------------------------------------------------
# Look filters
# ---------------------------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------------------------

def _num(clip: dict, key: str, default: float) -> float:
    """clip[key] as a float; missing or None (stored "not set") means the default."""
    v = clip.get(key)
    return default if v is None else float(v)


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def window(sw: int, sh: int, aspect: float, zoom: float, cx: float, cy: float) -> tuple[int, int, int, int]:
    """Source crop (w, h, x, y) of the given aspect, zoomed in, centred on (cx, cy) and kept inside the frame."""
    if aspect <= sw / sh:
        ch = sh / max(zoom, 1.0)
        cw = ch * aspect
    else:
        cw = sw / max(zoom, 1.0)
        ch = cw / aspect
    cw, ch = min(_even(cw), sw - sw % 2), min(_even(ch), sh - sh % 2)
    x = int(min(max(0, cx * sw - cw / 2), sw - cw))
    y = int(min(max(0, cy * sh - ch / 2), sh - ch))
    return cw, ch, x, y


def _layout(aspect: str, fit: str, x: int | None, source: Path | None, clip: dict, vin: str = "{vin}") -> str:
    """Filtergraph from `vin` (default [0:v]) to [base]: the frame laid out at the output size."""
    W, H = SIZES[aspect]
    zoom = _num(clip, "zoom", 1.0)
    if fit == "stack" and source is not None:
        from .framing import video_size

        sw, sh = video_size(str(source))
        fc = clip.get("facecam") or DEFAULT_FACECAM
        cam_h = _even(H * _num(clip, "cam_share", DEFAULT_CAM_SHARE))
        game_h = H - cam_h
        gw, gh, gx, gy = window(sw, sh, W / game_h, _num(clip, "game_zoom", 1.0),
                                _num(clip, "game_x", 0.5), _num(clip, "game_y", 0.5))
        order = "[top][bot]" if (clip.get("cam_pos") or "top") == "top" else "[bot][top]"
        return (f"{vin}split=2[cam][game];"
                f"[cam]crop=iw*{fc['w']:.4f}:ih*{fc['h']:.4f}:iw*{fc['x']:.4f}:ih*{fc['y']:.4f},"
                f"scale={W}:{cam_h}:force_original_aspect_ratio=increase,crop={W}:{cam_h}[top];"
                f"[game]crop={gw}:{gh}:{gx}:{gy},scale={W}:{game_h}[bot];"
                f"{order}vstack=2,setsar=1[base]")
    if fit == "pan":
        # pan.cmd (written next to the clip) moves the crop window frame by frame.
        return f"{vin}scale=-2:{H},sendcmd=f=pan.cmd,crop@pan={W}:{H}:{x or 0}:0,setsar=1[base]"
    if fit == "crop" and source is not None:
        from .framing import video_size

        sw, sh = video_size(str(source))
        if x is not None and zoom <= 1.0 and clip.get("crop_x") is None:  # auto: face-centred crop
            return f"{vin}scale=-2:{H},crop={W}:{H}:{x}:0,setsar=1[base]"
        cw, ch, cx, cy = window(sw, sh, W / H, zoom, _num(clip, "crop_x", 0.5), _num(clip, "crop_y", 0.5))
        return f"{vin}crop={cw}:{ch}:{cx}:{cy},scale={W}:{H},setsar=1[base]"
    if fit in ("blur", "black") or aspect == "original":
        fg_w = _even(W * zoom)
        fg = f"scale={fg_w}:{_even(H * zoom)}:force_original_aspect_ratio=decrease"
        if fit == "black" or (aspect == "original" and fit != "blur"):
            return (f"color=c=black:s={W}x{H}[bg];{vin}{fg}[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1,setsar=1[base]")
        return (f"{vin}split=2[a][b];"
                f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:3,eq=brightness=-0.08[bg];"
                f"[b]{fg}[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[base]")
    return f"{vin}scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[base]"


def image_layers(clip: dict) -> list[dict]:
    """The clip's image overlays whose files still exist (max 3), each with its absolute path."""
    from .assets import MAX_IMAGES_PER_CLIP, path_of

    out = []
    for img in (clip.get("images") or [])[:MAX_IMAGES_PER_CLIP]:
        p = path_of(img.get("file"))
        if p:
            out.append({**img, "path": p})
    return out


def _video_filter(aspect: str, fit: str, captions: bool, x: int | None = None,
                  source: Path | None = None, clip: dict | None = None, out_dur: float | None = None,
                  subs: bool | None = None, pre: str = "", vin: str = "[0:v]", img_input: int = 1,
                  shift: float = 0.0) -> str:
    """Full filtergraph -> [v]: layout, look, speed, fades, progress bar, logos, captions/text.

    `pre` is prepended (e.g. the hook-intro concat producing `vin`); images are inputs img_input..;
    `shift` delays timed images by that many output seconds (the hook plays first)."""
    clip = clip or {}
    W, H = SIZES[aspect]
    graph = pre + _layout(aspect, fit, x, source, clip, vin)
    speed = _num(clip, "speed", 1.0)
    chain = [p for p in (filter_chain(clip.get("filters")),) if p]
    if abs(speed - 1.0) > 1e-3:
        chain.append(f"setpts=PTS/{speed:.4f}")
    fin, fout = _num(clip, "fade_in", 0), _num(clip, "fade_out", 0)
    if fin > 0:
        chain.append(f"fade=t=in:st=0:d={fin:.2f}")
    if fout > 0 and out_dur:
        chain.append(f"fade=t=out:st={max(0.0, out_dur - fout):.2f}:d={fout:.2f}")
    graph += ";[base]" + (",".join(chain) if chain else "null") + "[look]"
    last = "[look]"
    pb = clip.get("progress_bar") or {}
    if pb.get("on") and out_dur:
        bar_h = 14
        y = 0 if pb.get("position") == "top" else H - bar_h
        color = str(pb.get("color", "#FFD400")).lstrip("#")
        graph += (f";color=c=0x{color}:s={W}x{bar_h}:d={out_dur:.3f}[pb];"
                  f"{last}[pb]overlay=x='-W+W*t/{out_dur:.3f}':y={y}:shortest=1[pbv]")
        last = "[pbv]"
    # Image overlays (watermarks / logos): inputs 1..n, under the captions and text.
    for k, img in enumerate(image_layers(clip)):
        timed = img.get("end") is not None or float(img.get("start") or 0) > 0
        a = float(img.get("start") or 0) + (shift if timed else 0)
        b = min(float(img["end"]) + shift, out_dur or 1e9) if img.get("end") is not None else (out_dur or 1e9)
        w = _even(W * float(img.get("size", 0.18)))
        graph += (f";[{k + img_input}:v]format=rgba,scale={w}:-2,colorchannelmixer=aa={float(img.get('opacity', 0.85)):.2f}[wm{k}];"
                  f"{last}[wm{k}]overlay=x='{float(img['x']):.4f}*W-w/2':y='{float(img['y']):.4f}*H-h/2'"
                  f":enable='between(t,{a:.2f},{b:.2f})'[im{k}]")
        last = f"[im{k}]"
    use_subs = captions if subs is None else subs
    graph += f";{last}" + ("ass=subs.ass" if use_subs else "null") + "[v]"
    return graph


def _audio_filter(clip: dict, out_dur: float, loudness: float) -> str:
    speed = _num(clip, "speed", 1.0)
    parts = []
    if abs(speed - 1.0) > 1e-3:
        parts.append(f"atempo={speed:.4f}")
    if clip.get("mute"):
        parts.append("volume=0")
    else:
        parts.append(f"loudnorm=I={loudness}:TP=-1.5:LRA=11")
        vol = _num(clip, "volume", 1.0)
        if abs(vol - 1.0) > 1e-3:
            parts.append(f"volume={vol:.2f}")
    fin, fout = _num(clip, "fade_in", 0), _num(clip, "fade_out", 0)
    if fin > 0:
        parts.append(f"afade=t=in:st=0:d={fin:.2f}")
    if fout > 0:
        parts.append(f"afade=t=out:st={max(0.0, out_dur - fout):.2f}:d={fout:.2f}")
    parts.append("aresample=48000")
    return ",".join(parts)


def render_clip(source: Path, clip: dict, sentences: list[dict], out_dir: Path, rank: int,
                aspect: str, fit: str, captions: bool, cfg: dict, version: str = "") -> dict:
    """Render one clip. `version` is added to the file name (re-renders use a fresh name, so a copy
    still open in a player or Explorer never blocks the new render)."""
    clips_dir = out_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
    name = f"{rank:02d}_{slugify(clip['title'])}" + (f"_{version}" if version else "")
    mp4, jpg = clips_dir / f"{name}.mp4", clips_dir / f"{name}.jpg"
    speed = _num(clip, "speed", 1.0)
    start, dur = clip["start"], clip["end"] - clip["start"]
    # Small lead-in/out so the first and last words are not clipped.
    pre = min(0.15, start)
    start, dur = start - pre, dur + pre + 0.25

    # Hook intro: a 2-8 s teaser (source seconds hs..he) plays first, then the clip from its start.
    hook = clip.get("hook_intro") or {}
    hs, he = hook.get("start"), hook.get("end")
    use_hook = bool(hook.get("on")) and hs is not None and he is not None and 1.0 <= he - hs <= 10.0
    hd = (he - hs) if use_hook else 0.0
    hook_out = hd / speed
    out_dur = (hd + dur) / speed

    texts = clip.get("texts") or []
    subs = bool(captions or texts)
    if subs:
        words = words_between(sentences, clip["start"], clip["end"]) if captions else []
        if captions and clip.get("caption_text"):
            words = align_text(words, clip["caption_text"])
        if use_hook:
            # One timeline: teaser words at 0..hd, then the clip's words after it.
            teaser = words_between(sentences, hs, he) if captions else []
            words = ([{**w, "s": w["s"] - hs, "e": min(w["e"], he) - hs} for w in teaser] +
                     [{**w, "s": hd + w["s"] - start, "e": hd + w["e"] - start} for w in words])
            texts = [{**t, "start": float(t.get("start", 0)) + hook_out, "end": float(t.get("end", 0)) + hook_out}
                     for t in texts]
            t0, t1 = 0.0, hd + dur
        else:
            t0, t1 = start, start + dur
        style = caption_style(cfg["captions"], clip.get("style"))
        (clips_dir / "subs.ass").write_text(
            build_ass(words, t0, t1, SIZES[aspect], style, aspect, speed, texts, captions),
            encoding="utf-8")

    x = None
    used_fit = fit
    if fit == "stack" and not clip.get("facecam"):
        from .framing import detect_facecam

        found = detect_facecam(source, start, dur)
        if found:
            clip["facecam"] = found
        else:
            used_fit = "blur"  # no facecam to put on top: keep the full frame instead
    if aspect == "original" and fit in ("auto", "crop"):
        used_fit = "black"
    elif fit == "auto":
        from .framing import choose_framing, crop_x, pan_commands

        plan = choose_framing(source, start, dur) if aspect == "vertical" else {"mode": "blur"}
        used_fit = plan["mode"]
        if used_fit == "crop":
            x = crop_x(source, plan["x"])
        elif used_fit == "pan" and use_hook:
            # The pan path is timed to the clip alone; with a teaser in front, hold its middle position.
            used_fit = "crop"
            xs = sorted(c for _, c in plan["path"])
            x = crop_x(source, xs[len(xs) // 2])
        elif used_fit == "pan":
            (clips_dir / "pan.cmd").write_text(pan_commands(source, plan["path"], dur), encoding="utf-8")
            x = crop_x(source, plan["path"][0][1])

    enc = cfg["encode"]
    crf = QUALITY.get(clip.get("quality", "standard"), enc["crf"])
    src_inputs = ["-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(source.resolve())]
    if use_hook:
        from .ffmpeg import has_audio

        audio = has_audio(source)
        src_inputs = ["-ss", f"{hs:.3f}", "-t", f"{hd:.3f}", "-i", str(source.resolve())] + src_inputs
        pre_graph = ("[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[hv][ha];" if audio else "[0:v][1:v]concat=n=2:v=1:a=0[hv];")
        vgraph = _video_filter(aspect, used_fit, captions, x, source, clip, out_dur, subs,
                               pre=pre_graph, vin="[hv]", img_input=2, shift=hook_out)
        if audio:
            vgraph += f";[ha]{_audio_filter(clip, out_dur, enc['loudness_lufs'])}[aout]"
        audio_args = ["-map", "[v]"] + (["-map", "[aout]"] if audio else [])
    else:
        vgraph = _video_filter(aspect, used_fit, captions, x, source, clip, out_dur, subs)
        audio_args = ["-map", "[v]", "-map", "0:a:0?", "-af", _audio_filter(clip, out_dur, enc["loudness_lufs"])]
    ffmpeg_run([
        *src_inputs,
        *[arg for img in image_layers(clip) for arg in ("-i", str(img["path"]))],
        "-filter_complex", vgraph,
        *audio_args,
        "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-preset", str(enc["preset"]), "-crf", str(crf),
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-t", f"{out_dur:.3f}",
        mp4.name,
    ], cwd=clips_dir)
    cover = clip.get("cover_t")
    if cover is not None:
        ffmpeg_run(["-ss", f"{min(float(cover), max(0.0, out_dur - 0.1)):.3f}", "-i", mp4.name,
                    "-frames:v", "1", "-q:v", "3", jpg.name], cwd=clips_dir)
    else:
        ffmpeg_run(["-i", mp4.name, "-t", "3", "-vf", "thumbnail=40", "-frames:v", "1", "-q:v", "3", jpg.name],
                   cwd=clips_dir)
    (clips_dir / "subs.ass").unlink(missing_ok=True)
    (clips_dir / "pan.cmd").unlink(missing_ok=True)
    return {"file": f"clips/{mp4.name}", "thumbnail": f"clips/{jpg.name}",
            "framing": used_fit, "facecam": clip.get("facecam")}
