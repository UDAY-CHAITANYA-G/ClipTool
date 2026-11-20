"""Your own edit presets: a clip's whole look (layout, captions, logos, text, filters, audio...) saved by name.

Stored in assets/user_presets.json. A preset never holds anything tied to one clip's moment in the video
(start/end, edited caption words, speed zones, cover frame, description); the hook intro is kept only as
"on/off" and is found again for each clip it is applied to.
"""
from __future__ import annotations

import json
import threading
import time

from .assets import ASSETS

FILE = ASSETS / "user_presets.json"
LAST = "Last saved"
MAX_PRESETS = 50
_LOCK = threading.Lock()

# Keys a preset carries (same names as a clip edit).
KEYS = ("aspect", "fit", "facecam", "cam_share", "cam_pos", "game_x", "game_y", "game_zoom", "zoom", "crop_x",
        "crop_y", "speed", "filters", "captions", "caption_offset", "style", "texts", "images", "volume", "mute",
        "fade_in", "fade_out", "progress_bar", "quality")


def clean(settings: dict) -> dict:
    """Keep the preset keys and validate them like an edit (raises ValueError)."""
    from .pipeline import clean_changes

    src = {k: settings[k] for k in KEYS if k in settings}
    out = clean_changes(src, 0)
    for k in ("captions", "mute"):
        if k in src:
            out[k] = bool(src[k])
    if "aspect" in src and "aspect" not in out:
        out["aspect"] = src["aspect"]
    if "fit" in src and "fit" not in out:
        out["fit"] = src["fit"]
    out["hook_intro_auto"] = bool(settings.get("hook_intro_auto") or (settings.get("hook_intro") or {}).get("on"))
    return out


def _load() -> dict:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _write(data: dict) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(FILE)


def summary(s: dict) -> str:
    """One readable line: '9:16 · Facecam + game · Vice City captions · 2 logos · Vivid'."""
    from .render import FILTER_PRESETS, PRESETS

    parts = [{"vertical": "9:16", "portrait": "4:5", "square": "1:1", "original": "16:9"}.get(s.get("aspect"), "")]
    parts.append({"auto": "Auto framing", "crop": "Crop", "blur": "Full + blur", "black": "Full + black",
                  "stack": "Facecam + game"}.get(s.get("fit"), ""))
    st = s.get("style") or {}
    if s.get("captions") is False:
        parts.append("no captions")
    elif st.get("preset"):
        parts.append(f"{(PRESETS.get(st['preset']) or {}).get('label', st['preset'])} captions")
    if s.get("images"):
        parts.append(f"{len(s['images'])} logo{'s' if len(s['images']) > 1 else ''}")
    if s.get("texts"):
        parts.append(f"{len(s['texts'])} text{'s' if len(s['texts']) > 1 else ''}")
    f = (s.get("filters") or {}).get("preset")
    if f and f != "none":
        parts.append((FILTER_PRESETS.get(f) or {}).get("label", f))
    if s.get("speed") and abs(float(s["speed"]) - 1) > 1e-3:
        parts.append(f"{float(s['speed']):g}×")
    if s.get("hook_intro_auto"):
        parts.append("hook intro")
    return " · ".join(p for p in parts if p)


def list_all() -> list[dict]:
    data = _load()
    items = [{"name": n, "saved": v.get("saved", 0), "summary": summary(v["settings"]), "settings": v["settings"]}
             for n, v in data.items()]
    items.sort(key=lambda x: (x["name"] != LAST, -x["saved"]))
    return items


def get(name: str) -> dict | None:
    v = _load().get(name)
    return v["settings"] if v else None


def save(name: str, settings: dict) -> dict:
    name = " ".join(str(name or "").split())[:40]
    if not name:
        raise ValueError("Give the preset a name.")
    s = clean(settings)
    with _LOCK:
        data = _load()
        if name not in data and len(data) >= MAX_PRESETS:
            raise ValueError(f"You can keep up to {MAX_PRESETS} presets; delete one first.")
        data[name] = {"settings": s, "saved": time.time()}
        _write(data)
    return {"name": name, "summary": summary(s), "settings": s}


def delete(name: str) -> bool:
    with _LOCK:
        data = _load()
        if name not in data:
            return False
        del data[name]
        _write(data)
    return True


def changes_for_clip(settings: dict, clip: dict, sentences: list[dict], info: dict, source) -> dict:
    """Turn a preset into an edit for one clip (fits text/logo timings to the clip, finds its hook)."""
    ch = {k: v for k, v in settings.items() if k in KEYS}
    length = float(clip["end"]) - float(clip["start"])
    if ch.get("texts"):
        ch["texts"] = [{**t, "start": min(float(t.get("start") or 0), max(0.0, length - 0.5)),
                        "end": min(float(t.get("end") or length), length)} for t in ch["texts"]]
    if ch.get("fit") == "stack" and not ch.get("facecam"):
        ch.pop("facecam", None)
    if settings.get("hook_intro_auto"):
        h = (clip.get("hook_intro") or {})
        if h.get("start") is None:
            from .signals import find_hook

            h = find_hook(clip, sentences, info, source, None)
            if h:
                ch["hook_intro"] = {"on": True, "start": h["start"], "end": h["end"], "text": h["text"]}
        else:
            ch["hook_intro"] = {**h, "on": True}
    elif "hook_intro_auto" in settings:
        ch["hook_intro"] = {"on": False}
    return ch
