"""clips.json, index.html and the terminal table."""
from __future__ import annotations

import html
import json
from pathlib import Path

from .candidates import window_text

# Per-clip edit settings kept in clips.json (see render.py for what each one does).
EDIT_KEYS = ("caption_text", "style", "aspect", "fit", "facecam", "cam_share", "cam_pos", "game_x", "game_y",
             "game_zoom", "crop_x", "crop_y", "zoom", "filters", "speed", "volume", "mute", "fade_in",
             "fade_out", "texts", "images", "progress_bar", "cover_t", "quality")


def _fmt(t: float) -> str:
    m, s = divmod(int(t), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def clip_records(info: dict, clips: list[dict], sentences: list[dict]) -> list[dict]:
    vid = info["id"]
    recs = []
    for rank, c in enumerate(clips, 1):
        recs.append({
            "rank": rank,
            "title": c["title"],
            "hook": c["hook"],
            "start": round(c["start"], 2),
            "end": round(c["end"], 2),
            "duration": round(c["end"] - c["start"], 1),
            "score": round(c["score"], 3),
            "signals": c["signals"],
            "reason": c["reason"],
            "file": c.get("file"),
            "thumbnail": c.get("thumbnail"),
            "framing": c.get("framing"),
            "source_url": f"https://youtu.be/{vid}?t={int(c['start'])}",
            "transcript": c.get("caption_text") or window_text(sentences, c),
            **{k: c.get(k) for k in EDIT_KEYS},
            "captions": c.get("captions", True),
            "edited": bool(c.get("edited")),
            "i": c["i"], "j": c["j"],
        })
    return recs


def write_json(out_dir: Path, info: dict, recs: list[dict], settings: dict) -> Path:
    p = out_dir / "clips.json"
    data = {"video": {k: info.get(k) for k in ("id", "title", "channel", "duration", "webpage_url")},
            "settings": settings, "clips": recs}
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def write_html(out_dir: Path, info: dict, recs: list[dict]) -> Path:
    e = html.escape
    cards = []
    for r in recs:
        sig = " · ".join(f"{k} {v:.2f}" for k, v in r["signals"].items() if v is not None)
        media = (f'<video controls preload="metadata" poster="{e(r["thumbnail"] or "")}" src="{e(r["file"])}"></video>'
                 if r.get("file") else '<div class="nofile">Not rendered (dry run)</div>')
        cards.append(f"""
<article class="card">
  {media}
  <div class="body">
    <div class="top"><span class="rank">#{r['rank']}</span><span class="score">{r['score']:.2f}</span></div>
    <div class="bar"><span style="width:{max(0, min(1, r['score'])) * 100:.0f}%"></span></div>
    <h2>{e(r['title'])}</h2>
    <p class="hook">{e(r['hook'])}</p>
    <p class="reason">{e(r['reason'])}</p>
    <p class="meta"><a href="{e(r['source_url'])}" target="_blank" rel="noopener">{_fmt(r['start'])}–{_fmt(r['end'])}</a> · {r['duration']:.0f}s</p>
    <p class="sig">{e(sig)}</p>
    <details><summary>Transcript</summary><p>{e(r['transcript'])}</p></details>
    <div class="actions">
      <button onclick="navigator.clipboard.writeText({e(json.dumps(r['title']))})">Copy title</button>
      {f'<a class="btn" href="{e(r["file"])}" download>Download</a>' if r.get('file') else ''}
    </div>
  </div>
</article>""")
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clips · {e(info.get('title') or '')}</title>
<style>
:root{{--bg:#f6f6f4;--card:#fff;--ink:#1d1d1b;--quiet:#6b6b66;--line:#e2e2dc;--accent:#d9480f}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--card:#1f1f1d;--ink:#eeeeea;--quiet:#a3a39c;--line:#33332f;--accent:#ff8a4c}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}}
header{{padding:28px 16px 8px;max-width:1200px;margin:auto}}h1{{font-size:22px;margin:0 0 4px}}header p{{margin:0;color:var(--quiet)}}
main{{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:18px;padding:16px;max-width:1200px;margin:auto}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;display:flex;flex-direction:column}}
video{{width:100%;aspect-ratio:9/16;background:#000;display:block;object-fit:contain}}
.nofile{{aspect-ratio:9/16;display:grid;place-items:center;color:var(--quiet);border-bottom:1px solid var(--line)}}
.body{{padding:12px 14px 14px}}.top{{display:flex;justify-content:space-between;font-weight:600}}.rank{{color:var(--accent)}}
.bar{{height:4px;background:var(--line);border-radius:2px;margin:6px 0 10px}}.bar span{{display:block;height:100%;background:var(--accent);border-radius:2px}}
h2{{font-size:16px;margin:0 0 6px}}.hook{{margin:0 0 6px;font-style:italic}}.reason,.meta,.sig{{margin:0 0 6px;color:var(--quiet);font-size:13px}}
a{{color:var(--accent)}}details{{font-size:13px;color:var(--quiet)}}
.actions{{display:flex;gap:8px;margin-top:10px}}button,.btn{{font:inherit;font-size:13px;padding:6px 10px;border-radius:8px;border:1px solid var(--line);background:transparent;color:var(--ink);cursor:pointer;text-decoration:none}}
</style></head><body>
<header><h1>{e(info.get('title') or '')}</h1><p>{e(info.get('channel') or '')} · {len(recs)} clips · <a href="{e(info.get('webpage_url') or '')}" target="_blank" rel="noopener">source video</a></p></header>
<main>{''.join(cards)}</main>
</body></html>"""
    p = out_dir / "index.html"
    p.write_text(page, encoding="utf-8")
    return p


def print_table(recs: list[dict]) -> None:
    print()
    print(f"{'#':>2}  {'score':>5}  {'time':>15}  {'len':>4}  title")
    print("-" * 80)
    for r in recs:
        t = f"{_fmt(r['start'])}-{_fmt(r['end'])}"
        print(f"{r['rank']:>2}  {r['score']:>5.2f}  {t:>15}  {r['duration']:>3.0f}s  {r['title']}")
    print()
