"""Upload descriptions for clips: credit the original channel, link the source, professional copyright note.

The template lives in assets/settings.json ("description") and can be edited in Settings > Description.
Placeholders (a line is left out when any of its placeholders is empty):

  {title}          the clip's title                {hook}          its opening line
  {channel}        original channel name           {channel_url}   original channel link
  {channel_handle} original channel @handle        {video_title}   original video title
  {video_url}      original video link             {timestamp_url} link to the exact moment
  {start} {end}    clip start / end in the video   {my_channel}    your channel name
  {contact}        how to reach you                {hashtags}      your hashtags
"""
from __future__ import annotations

import re

DEFAULT_TEMPLATE = """{title}

🎮 Credit: this short is a clip from {channel}'s video "{video_title}".
▶️ Watch the full video: {video_url}
⏱️ This moment ({start}–{end}): {timestamp_url}
📺 Subscribe to {channel}: {channel_url}
🙌 Streamer: {channel_handle}

All rights to the original gameplay, voice and commentary belong to {channel}. This clip is shared to highlight their content and send viewers to the full stream - please support the creator by watching the original video and subscribing.

📩 Copyright: if you are the owner of this content and would like it credited differently, edited or taken down, please contact me first at {contact} instead of filing a copyright claim. I respond quickly and will resolve it right away - credit, removal, or whatever you prefer.

Clipped and edited by {my_channel}.

{hashtags}"""

DEFAULTS = {"template": DEFAULT_TEMPLATE, "my_channel": "", "contact": "", "hashtags": "#Shorts #GTA6 #Gaming"}
MAX_LEN = 5000  # YouTube's description limit
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def _fmt(t: float) -> str:
    t = int(max(0, t))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def context(info: dict, clip: dict, settings: dict) -> dict:
    vid = info.get("id") or ""
    return {
        "title": clip.get("title", ""),
        "hook": clip.get("hook", ""),
        "channel": info.get("channel") or "",
        "channel_url": info.get("channel_url") or "",
        "channel_handle": info.get("channel_handle") or "",
        "video_title": info.get("title") or "",
        "video_url": info.get("webpage_url") or (f"https://www.youtube.com/watch?v={vid}" if vid else ""),
        "timestamp_url": f"https://youtu.be/{vid}?t={int(clip.get('start', 0))}" if vid else "",
        "start": _fmt(clip.get("start", 0)),
        "end": _fmt(clip.get("end", 0)),
        "my_channel": settings.get("my_channel", ""),
        "contact": settings.get("contact", ""),
        "hashtags": settings.get("hashtags", ""),
    }


def render(template: str, ctx: dict) -> str:
    """Fill placeholders; drop any line with an empty placeholder (no half-filled lines); tidy blank lines."""
    out = []
    for line in template.splitlines():
        keys = [k for k in PLACEHOLDER.findall(line) if k in ctx]
        if any(not str(ctx.get(k, "")).strip() for k in keys):
            continue
        out.append(PLACEHOLDER.sub(lambda m: str(ctx.get(m.group(1), m.group(0))), line))
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
    return text[:MAX_LEN]


def for_clip(info: dict, clip: dict, settings: dict) -> str:
    """The clip's own description if it was edited, otherwise the template filled in."""
    if clip.get("description"):
        return clip["description"]
    return render(settings.get("template") or DEFAULT_TEMPLATE, context(info, clip, settings))


def ensure_channel_details(info: dict, work) -> dict:
    """Older downloads didn't store the channel link / @handle: look them up once and cache them."""
    if info.get("channel_url") or info.get("_channel_checked"):
        return info
    try:
        import yt_dlp

        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True}) as ydl:
            full = ydl.extract_info(info.get("webpage_url") or f"https://www.youtube.com/watch?v={info['id']}",
                                    download=False, process=False)
        info["channel_url"] = full.get("channel_url") or full.get("uploader_url")
        uid = str(full.get("uploader_id") or "")
        info["channel_handle"] = uid if uid.startswith("@") else None
    except Exception:
        pass  # offline or blocked: descriptions just skip the link lines
    info["_channel_checked"] = True
    work.save("info.json", info)
    return info
