"""Local website: paste a link, watch progress, play and download clips.

Standard library only. Binds to 127.0.0.1 by default; one job runs at a time.

  GET  /                         the app page
  POST /api/jobs                 start a job {url, count, min, max, aspect, fit, captions}
  GET  /api/jobs/<id>            job status, stage, progress, log tail, result
  POST /api/jobs/<id>/cancel     stop after the current step
  GET  /api/library              previous runs found in output/
  GET  /api/library/<folder>     one run's clips.json
  GET  /files/<folder>/<path>    clip files (with HTTP Range support for video seeking)
  DELETE /api/library/<folder>   delete one run (its clips + cached download)
  DELETE /api/library            delete all runs and the whole work/ cache
"""
from __future__ import annotations

import json
import mimetypes
import re
import threading
import time
import traceback
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import assets, pipeline, render
from .fetch import video_id_from_url

STATIC = Path(__file__).parent / "static"
OUTPUT = Path("output").resolve()   # finished clips (set in Settings > Storage)
WORK = Path("work").resolve()       # downloaded videos + transcripts cache


def refresh_paths() -> None:
    """Point the server at the folders chosen in Settings (or the defaults)."""
    global OUTPUT, WORK
    OUTPUT, WORK = assets.folder("output"), assets.folder("work")
    OUTPUT.mkdir(parents=True, exist_ok=True)


def _paths_payload() -> dict:
    s = assets.load_settings()["paths"]
    return {"output": str(OUTPUT), "work": str(WORK),
            "output_mb": round(assets.folder_size(OUTPUT) / 1e6, 1), "work_mb": round(assets.folder_size(WORK) / 1e6, 1),
            "custom": {"output": bool(s.get("output")), "work": bool(s.get("work"))},
            "defaults": {k: str(Path(v).resolve()) for k, v in assets.DEFAULT_PATHS.items()},
            "watermarks": str(assets.WATERMARKS)}

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _running_job() -> dict | None:
    return next((j for j in JOBS.values() if j["status"] == "running"), None)


def _run_job(job: dict, work) -> None:
    """Run `work(log, stage, cancelled) -> result dict` in this thread, recording progress on `job`."""
    def log(msg: str):
        for line in str(msg).splitlines() or [""]:
            job["log"].append(line)
        del job["log"][:-300]
        print(msg, flush=True)

    def stage(name: str, p: float):
        job["stage"], job["progress"] = name, round(p, 3)

    t0 = time.time()
    try:
        job["result"] = work(log, stage, lambda: job["cancel"])
        job["result"]["seconds"] = round(time.time() - t0, 1)
        job["status"] = "done"
    except pipeline.Cancelled:
        job["status"], job["stage"] = "cancelled", "Cancelled"
    except Exception as e:  # report any failure to the page
        log(traceback.format_exc(limit=3))
        job["status"], job["error"] = "error", str(e) or e.__class__.__name__


def _library() -> list[dict]:
    items = []
    if not OUTPUT.exists():
        return items
    for f in OUTPUT.glob("*/clips.json"):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        clips = d.get("clips", [])
        items.append({
            "folder": f.parent.name, "title": d["video"].get("title"), "channel": d["video"].get("channel"),
            "count": len(clips), "rendered": d.get("settings", {}).get("rendered", True),
            "thumbnail": next((c["thumbnail"] for c in clips if c.get("thumbnail")), None),
            "mtime": f.stat().st_mtime,
        })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def _run_dir(folder: str) -> Path | None:
    """An output run folder by name, only if it sits directly inside output/."""
    p = (OUTPUT / folder).resolve()
    return p if p.parent == OUTPUT and p.is_dir() else None


def _delete_run(run: Path) -> dict:
    """Delete one run's clips and, if no other run uses it, its cached download in work/."""
    import shutil

    freed = 0
    cache = None
    try:
        d = json.loads((run / "clips.json").read_text(encoding="utf-8"))
        wd = Path(d.get("settings", {}).get("work_dir", "")).resolve()
        if wd.parent == WORK and wd.is_dir():
            others = [f for f in OUTPUT.glob(f"*_{wd.name}") if f.resolve() != run]
            cache = None if others else wd
    except Exception:
        pass
    for target in (run, cache):
        if target and target.exists():
            freed += sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
            shutil.rmtree(target)
    return {"deleted": run.name, "freed_mb": round(freed / 1e6, 1)}


def _clear_all() -> dict:
    import shutil

    freed, n = 0, 0
    for base in (OUTPUT, WORK):
        if not base.exists():
            continue
        for child in base.iterdir():
            freed += sum(f.stat().st_size for f in child.rglob("*") if f.is_file()) if child.is_dir() else child.stat().st_size
            if base == OUTPUT and child.is_dir():
                n += 1
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    return {"deleted_runs": n, "freed_mb": round(freed / 1e6, 1)}


def _safe_join(base: Path, rel: str) -> Path | None:
    p = (base / rel).resolve()
    try:
        p.relative_to(base)
    except ValueError:
        return None
    return p if p.is_file() else None


class Handler(BaseHTTPRequestHandler):
    server_version = "ClipTool"
    timeout = 20  # drop stalled connections so they don't keep clip files open (Windows file locks)

    def log_message(self, fmt, *args):  # keep the console for pipeline output
        pass

    # ---- helpers -------------------------------------------------------
    def _json(self, data, status=HTTPStatus.OK):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, msg, status=HTTPStatus.BAD_REQUEST):
        self._json({"error": msg}, status)

    def _file(self, path: Path, download: bool = False):
        size = path.stat().st_size
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        m = re.match(r"bytes=(\d*)-(\d*)$", rng or "")
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            if start > end:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if download:
            self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = f.read(min(1 << 16, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                pass  # browser stopped reading (seek / close) - normal for video

    # ---- routes --------------------------------------------------------
    # ---- watermark images + settings ------------------------------------
    def _assets_route(self, method: str) -> bool:
        """Handle /api/watermarks, /wm/<name> and /api/settings. Returns True if handled."""
        path = unquote(urlparse(self.path).path)
        if method == "GET" and path == "/api/watermarks":
            self._json({"images": assets.list_images(), "positions": assets.POSITIONS,
                        "max_per_clip": assets.MAX_IMAGES_PER_CLIP})
            return True
        m = re.fullmatch(r"/wm/([^/]+)", path)
        if method == "GET" and m:
            p = assets.path_of(m.group(1))
            self._file(p) if p else self._error("not found", HTTPStatus.NOT_FOUND)
            return True
        if method == "POST" and path == "/api/watermarks":
            n = int(self.headers.get("Content-Length") or 0)
            if n > assets.MAX_UPLOAD:
                self._error("Images must be 10 MB or smaller.", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return True
            try:
                self._json(assets.save_upload(self.rfile.read(n)), HTTPStatus.CREATED)
            except ValueError as e:
                self._error(str(e))
            return True
        if method == "DELETE" and m is None and path.startswith("/api/watermarks/"):
            name = path.rsplit("/", 1)[-1]
            ok = assets.delete_image(name)
            self._json({"deleted": name}) if ok else self._error("not found", HTTPStatus.NOT_FOUND)
            return True
        m2 = re.fullmatch(r"/api/watermarks/([^/]+)/clean", path)
        if method == "POST" and m2:
            try:
                self._json(assets.clean_image(m2.group(1)), HTTPStatus.CREATED)
            except ValueError as e:
                self._error(str(e))
            return True
        if path == "/api/paths" and method == "GET":
            self._json(_paths_payload())
            return True
        if path == "/api/paths" and method == "POST":
            if _running_job():
                self._error("Wait for the running job to finish before changing folders.", HTTPStatus.CONFLICT)
                return True
            try:
                n = int(self.headers.get("Content-Length") or 0)
                assets.set_folders(json.loads(self.rfile.read(n) or b"{}"))
                refresh_paths()
                self._json(_paths_payload())
            except (ValueError, TypeError) as e:
                self._error(str(e) or "invalid folders")
            return True
        if path == "/api/fs" and method == "GET":
            from urllib.parse import parse_qs

            q = parse_qs(urlparse(self.path).query).get("path", [""])[0]
            try:
                self._json(assets.browse(q))
            except ValueError as e:
                self._error(str(e))
            return True
        if path == "/api/open" and method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            which = (json.loads(self.rfile.read(n) or b"{}") or {}).get("which")
            target = {"output": OUTPUT, "work": WORK, "watermarks": assets.WATERMARKS}.get(which)
            if not target:
                self._error("unknown folder")
                return True
            target.mkdir(parents=True, exist_ok=True)
            _open_folder(target)
            self._json({"opened": str(target)})
            return True
        if path == "/api/settings":
            if method == "GET":
                self._json(assets.load_settings())
                return True
            if method == "POST":
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n) or b"{}")
                    s = assets.load_settings()
                    for kind in assets.DEFAULTS:
                        if kind not in body:
                            continue
                        wm = body.get(kind) or {}
                        layer = assets.clean_layer({**wm, "end": None}, require_file=bool(wm.get("file")))
                        s[kind] = {k: layer[k] for k in ("file", "x", "y", "size", "opacity")} | {
                            "start": 0.0, "end": None, "auto": bool(wm.get("auto")) and layer["file"] is not None}
                    self._json(assets.save_settings(s))
                except (ValueError, TypeError) as e:
                    self._error(str(e) or "invalid settings")
                return True
        return False

    def do_GET(self):
        if self._assets_route("GET"):
            return
        url = urlparse(self.path)
        path = unquote(url.path)
        if path in ("/", "/index.html"):
            return self._file(STATIC / "index.html")
        if path.startswith("/static/"):
            p = _safe_join(STATIC.resolve(), path[len("/static/"):])
            return self._file(p) if p else self._error("not found", HTTPStatus.NOT_FOUND)
        if path == "/api/library":
            return self._json({"items": _library()})
        m = re.fullmatch(r"/api/library/([^/]+)", path)
        if m:
            p = _safe_join(OUTPUT, f"{m.group(1)}/clips.json")
            if not p:
                return self._error("not found", HTTPStatus.NOT_FOUND)
            return self._json(json.loads(p.read_text(encoding="utf-8")))
        m = re.fullmatch(r"/api/jobs/([a-f0-9]+)", path)
        if m:
            job = JOBS.get(m.group(1))
            if not job:
                return self._error("no such job", HTTPStatus.NOT_FOUND)
            return self._json({k: v for k, v in job.items() if k != "cancel"})
        if path == "/api/jobs":
            running = _running_job()
            return self._json({"running": running["id"] if running else None})
        if path == "/api/presets":
            return self._json({"presets": render.PRESETS, "preset_groups": render.PRESET_GROUPS, "positions": render.POSITIONS,
                               "filters": render.FILTER_PRESETS, "filter_limits": render.FILTER_LIMITS,
                               "default_facecam": render.DEFAULT_FACECAM, "default_cam_share": render.DEFAULT_CAM_SHARE,
                               "aspects": {k: {"label": v["label"], "size": v["size"], "cap_scale": v["cap_scale"],
                                               "bottom": v["bottom"], "top": v["top"]} for k, v in render.ASPECTS.items()},
                               "fonts": render.FONTS, "quality": list(render.QUALITY), "effects": render.EFFECTS})
        m = re.fullmatch(r"/api/runs/([^/]+)/clips/(\d+)/facecam", path)
        if m:
            run = _run_dir(m.group(1))
            if not run:
                return self._error("not found", HTTPStatus.NOT_FOUND)
            try:
                data, info, sentences, source = pipeline.load_run(run)
            except FileNotFoundError as e:
                return self._error(str(e), HTTPStatus.GONE)
            clip = next((c for c in data["clips"] if c["rank"] == int(m.group(2))), None)
            if not clip:
                return self._error("not found", HTTPStatus.NOT_FOUND)
            from .framing import detect_facecam

            return self._json({"facecam": detect_facecam(source, clip["start"], clip["end"] - clip["start"])})
        m = re.fullmatch(r"/api/runs/([^/]+)/clips/(\d+)/words", path)
        if m:
            run = _run_dir(m.group(1))
            if not run:
                return self._error("not found", HTTPStatus.NOT_FOUND)
            try:
                data, info, sentences, source = pipeline.load_run(run)
            except FileNotFoundError as e:
                return self._error(str(e), HTTPStatus.GONE)
            clip = next((c for c in data["clips"] if c["rank"] == int(m.group(2))), None)
            if not clip:
                return self._error("not found", HTTPStatus.NOT_FOUND)
            pad = 30.0  # room to extend the clip while trimming
            a, b = max(0.0, clip["start"] - pad), clip["end"] + pad
            words = [w for s in sentences if s["end"] > a and s["start"] < b for w in s["words"]
                     if a <= w["s"] < b and w["w"] not in (">>", "&gt;&gt;")]
            bounds = sorted({round(s["start"], 3) for s in sentences if a <= s["start"] < b} |
                            {round(s["end"], 3) for s in sentences if a < s["end"] <= b})
            return self._json({"words": words, "sentence_bounds": bounds, "video_id": info["id"],
                               "source_url": f"/runsrc/{run.name}",
                               "duration": info.get("duration"), "settings": data["settings"]})
        m = re.fullmatch(r"/runsrc/([^/]+)", path)
        if m:
            run = _run_dir(m.group(1))
            try:
                src = pipeline.load_run(run)[3] if run else None
            except FileNotFoundError:
                src = None
            return self._file(src) if src else self._error("source video not found", HTTPStatus.NOT_FOUND)
        m = re.fullmatch(r"/source/([A-Za-z0-9_-]{11})", path)
        if m:
            p = next((q for q in (WORK / m.group(1)).glob("source.*")
                      if q.suffix in (".mp4", ".webm", ".mkv")), None) if (WORK / m.group(1)).is_dir() else None
            return self._file(p) if p else self._error("not found", HTTPStatus.NOT_FOUND)
        m = re.fullmatch(r"/files/(.+)", path)
        if m:
            p = _safe_join(OUTPUT, m.group(1))
            if not p:
                return self._error("not found", HTTPStatus.NOT_FOUND)
            return self._file(p, download="download=1" in (url.query or ""))
        self._error("not found", HTTPStatus.NOT_FOUND)

    def do_POST(self):
        if self._assets_route("POST"):
            return
        path = urlparse(self.path).path
        if path == "/api/jobs":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._error("invalid JSON")
            url = str(body.get("url", "")).strip()
            try:
                video_id_from_url(url)
            except ValueError as e:
                return self._error(str(e))
            try:
                count = int(body.get("count") or 5)
                min_s = float(body.get("min") or 20)
                max_s = float(body.get("max") or 60)
            except (TypeError, ValueError):
                return self._error("count, min and max must be numbers")
            if not (1 <= count <= 20 and 5 <= min_s < max_s <= 180):
                return self._error("Use 1-20 clips and a length range between 5 and 180 seconds (min < max).")
            aspect = body.get("aspect", "vertical")
            fit = body.get("fit", "auto")
            if aspect not in render.SIZES or fit not in render.FITS:
                return self._error("bad aspect or framing")
            with JOBS_LOCK:
                if _running_job():
                    return self._error("A job is already running; wait for it or cancel it.", HTTPStatus.CONFLICT)
                jid = uuid.uuid4().hex[:12]
                job = {"id": jid, "url": url, "status": "running", "stage": "Starting", "progress": 0.0,
                       "log": [], "result": None, "error": None, "cancel": False}
                JOBS[jid] = job
            style = body.get("style", "highlight")
            if style not in render.PRESETS:
                style = "highlight"
            opts = pipeline.Options(url=url, count=count, min_s=min_s, max_s=max_s, aspect=aspect, fit=fit,
                                    captions=bool(body.get("captions", True)), caption_style=style,
                                    out=OUTPUT, work=WORK)

            def work(log, stage, cancelled):
                res = pipeline.run(opts, log=log, stage=stage, cancelled=cancelled)
                return {"folder": res["out_dir"].name, "title": res["info"]["title"], "clips": res["clips"]}

            threading.Thread(target=_run_job, args=(job, work), daemon=True).start()
            return self._json({"id": jid}, HTTPStatus.ACCEPTED)
        m = re.fullmatch(r"/api/runs/([^/]+)/clips/(\d+)/edit", path)
        if m:
            run = _run_dir(m.group(1))
            if not run:
                return self._error("not found", HTTPStatus.NOT_FOUND)
            rank = int(m.group(2))
            try:
                n = int(self.headers.get("Content-Length") or 0)
                changes = json.loads(self.rfile.read(n) or b"{}")
                data, info, _, _ = pipeline.load_run(run)
                pipeline.clean_changes(changes, info.get("duration") or 0)  # validate before queuing
            except FileNotFoundError as e:
                return self._error(str(e), HTTPStatus.GONE)
            except (ValueError, TypeError, KeyError) as e:
                return self._error(str(e) or "invalid edit")
            with JOBS_LOCK:
                if _running_job():
                    return self._error("A job is already running; wait for it or cancel it.", HTTPStatus.CONFLICT)
                jid = uuid.uuid4().hex[:12]
                job = {"id": jid, "kind": "edit", "folder": run.name, "rank": rank, "status": "running",
                       "stage": f"Re-rendering clip #{rank}", "progress": 0.05, "log": [], "result": None,
                       "error": None, "cancel": False}
                JOBS[jid] = job

            def work(log, stage, cancelled):
                return pipeline.edit_clip(run, rank, changes, log=log, stage=stage)

            threading.Thread(target=_run_job, args=(job, work), daemon=True).start()
            return self._json({"id": jid}, HTTPStatus.ACCEPTED)
        m = re.fullmatch(r"/api/jobs/([a-f0-9]+)/cancel", path)
        if m:
            job = JOBS.get(m.group(1))
            if not job:
                return self._error("no such job", HTTPStatus.NOT_FOUND)
            job["cancel"] = True
            return self._json({"ok": True})
        self._error("not found", HTTPStatus.NOT_FOUND)

    def do_DELETE(self):
        if self._assets_route("DELETE"):
            return
        path = unquote(urlparse(self.path).path)
        with JOBS_LOCK:
            if _running_job():
                return self._error("A job is running; wait for it to finish or cancel it first.", HTTPStatus.CONFLICT)
            try:
                if path == "/api/library":
                    return self._json(_clear_all())
                m = re.fullmatch(r"/api/library/([^/]+)", path)
                if m:
                    run = _run_dir(m.group(1))
                    if not run:
                        return self._error("not found", HTTPStatus.NOT_FOUND)
                    return self._json(_delete_run(run))
            except PermissionError:
                return self._error("A file is still open (playing video or another program). Close it and try again.",
                                   HTTPStatus.CONFLICT)
        self._error("not found", HTTPStatus.NOT_FOUND)


def _open_folder(p: Path) -> None:
    """Show a folder in Explorer / Finder / the file manager."""
    import os
    import subprocess
    import sys

    if os.name == "nt":
        os.startfile(str(p))  # noqa: S606 - local folder chosen from a fixed list
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p)])


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> None:
    refresh_paths()
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}"
    print(f"ClipTool is running at {url}  (Ctrl+C to stop)", flush=True)
    print(f"Clips are saved in {OUTPUT}", flush=True)
    print(f"Downloads are cached in {WORK}", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
