"""Favorite folders: your own collections of clips, saved as real folders you can open in Explorer.

Each folder (inside the Favorites folder, default ./favorites) holds copies of the clip videos and
thumbnails plus favorites.json with each clip's title, upload description and where it came from, so a
saved clip stays even if its run is deleted later.
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path

from .assets import folder

MANIFEST = "favorites.json"
_LOCK = threading.RLock()
BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def root() -> Path:
    p = folder("favorites")
    p.mkdir(parents=True, exist_ok=True)
    return p


def clean_name(name: str) -> str:
    """A safe Windows folder name, or ValueError."""
    n = " ".join(BAD.sub(" ", str(name or "")).split()).strip(" .")[:60]
    if not n:
        raise ValueError("Give the folder a name.")
    if n.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        raise ValueError("That name is reserved by Windows; pick another.")
    return n


def _dir(name: str) -> Path:
    d = root() / clean_name(name)
    if not d.is_dir():
        raise FileNotFoundError(f"No folder named {name!r}.")
    return d


def _load(d: Path) -> list[dict]:
    try:
        items = json.loads((d / MANIFEST).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        items = []
    return [i for i in items if (d / i.get("file", "")).exists()]  # drop entries whose video was deleted by hand


def _save(d: Path, items: list[dict]) -> None:
    tmp = d / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(d / MANIFEST)


def _size(d: Path) -> int:
    return sum(f.stat().st_size for f in d.iterdir() if f.is_file())


def list_folders() -> list[dict]:
    with _LOCK:
        out = []
        for d in sorted((p for p in root().iterdir() if p.is_dir()), key=lambda p: p.name.lower()):
            items = _load(d)
            out.append({"name": d.name, "count": len(items), "size_mb": round(_size(d) / 2 ** 20, 1),
                        "cover": next((f"{d.name}/{i['thumb']}" for i in reversed(items) if i.get("thumb")), None),
                        "updated": max([i.get("added", 0) for i in items] or [d.stat().st_mtime])})
        return out


def create(name: str) -> dict:
    with _LOCK:
        n = clean_name(name)
        d = root() / n
        if d.exists():
            raise ValueError(f"A folder named “{n}” already exists.")
        d.mkdir(parents=True)
        _save(d, [])
        return {"name": n}


def rename(name: str, new: str) -> dict:
    with _LOCK:
        d, n = _dir(name), clean_name(new)
        if n == d.name:
            return {"name": n}
        t = root() / n
        if t.exists() and t.resolve() != d.resolve():
            raise ValueError(f"A folder named “{n}” already exists.")
        d.rename(t)
        return {"name": n}


def delete(name: str) -> dict:
    with _LOCK:
        d = _dir(name)
        count = len(_load(d))
        shutil.rmtree(d)
        return {"deleted": d.name, "clips": count}


def items(name: str) -> dict:
    with _LOCK:
        d = _dir(name)
        its = sorted(_load(d), key=lambda i: i.get("added", 0), reverse=True)
        return {"name": d.name, "path": str(d), "items": its}


def add(name: str, run_dir: Path, ranks: list[int]) -> dict:
    """Copy clips of a run into a folder (a clip already in the folder is replaced by its newest render)."""
    from . import assets, description
    from .cache import Work
    from .pipeline import resolve_work_dir

    data = json.loads((run_dir / "clips.json").read_text(encoding="utf-8"))
    wd = resolve_work_dir(data)
    work = Work(wd.parent, wd.name)
    info = work.load("info.json") if work.has("info.json") else dict(data["video"])
    dsettings = assets.load_settings()["description"]
    want = set(int(r) for r in ranks)
    with _LOCK:
        d = _dir(name)
        current = _load(d)
        added, replaced, skipped = [], 0, []
        for c in data["clips"]:
            if c["rank"] not in want:
                continue
            src = run_dir / (c.get("file") or "")
            if not c.get("file") or not src.exists():
                skipped.append(c["rank"])
                continue
            vid = (data["video"].get("id") or run_dir.name)[:11]
            key = f"{vid}_{c['rank']:02d}"
            stem = f"{key}_{src.stem.split('_', 1)[-1]}"[:90]
            old = next((i for i in current if i.get("key") == key), None)
            if old:
                for f in (old.get("file"), old.get("thumb")):
                    if f:
                        try:
                            (d / f).unlink(missing_ok=True)
                        except OSError:
                            pass
                current.remove(old)
                replaced += 1
            shutil.copy2(src, d / f"{stem}.mp4")
            thumb = None
            tsrc = run_dir / (c.get("thumbnail") or "")
            if c.get("thumbnail") and tsrc.exists():
                shutil.copy2(tsrc, d / f"{stem}.jpg")
                thumb = f"{stem}.jpg"
            item = {"key": key, "file": f"{stem}.mp4", "thumb": thumb, "title": c.get("title", ""),
                    "description": description.for_clip(info, c, dsettings),
                    "video_title": data["video"].get("title"), "channel": data["video"].get("channel"),
                    "source_url": f"https://youtu.be/{vid}?t={int(c['start'])}" if data["video"].get("id") else None,
                    "run": run_dir.name, "rank": c["rank"], "start": c["start"], "end": c["end"],
                    "size_mb": round((d / f"{stem}.mp4").stat().st_size / 2 ** 20, 1), "added": time.time()}
            current.append(item)
            added.append(c["rank"])
        _save(d, current)
        return {"name": d.name, "added": added, "replaced": replaced, "skipped": skipped, "count": len(current)}


def remove(name: str, key: str) -> dict:
    with _LOCK:
        d = _dir(name)
        current = _load(d)
        it = next((i for i in current if i.get("key") == key), None)
        if not it:
            raise FileNotFoundError("That clip isn't in this folder.")
        for f in (it.get("file"), it.get("thumb")):
            if f:
                (d / f).unlink(missing_ok=True)
        current.remove(it)
        _save(d, current)
        return {"removed": key, "count": len(current)}


def file_path(rel: str) -> Path | None:
    """A file inside the Favorites folder (for playing / downloading), or None."""
    base = root().resolve()
    p = (base / rel).resolve()
    return p if p.is_file() and base in p.parents and p.suffix.lower() in (".mp4", ".jpg") else None


def export_rows(name: str) -> list[list]:
    """Rows for the Download list (same columns as a run's list)."""
    its = items(name)["items"]
    return [[n, i["title"], i["description"], i["file"], round(float(i["end"]) - float(i["start"]), 1)]
            for n, i in enumerate(sorted(its, key=lambda i: i.get("added", 0)), start=1)]
