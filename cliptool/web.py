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
import queue
import threading
import time
import traceback
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import assets, favorites, pipeline, render, userpresets
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


def _active() -> list[dict]:
    return [j for j in JOBS.values() if j["status"] in ("running", "queued")]


def _busy() -> bool:
    """A job is running or waiting in the queue."""
    return any(j["status"] in ("running", "queued") for j in JOBS.values())


# Several jobs run side by side (new videos, edits, your own clips). Clip files are protected by
# per-run / per-clip locks in pipeline, and render.RENDER_SLOTS caps how many ffmpeg encodes run at
# once so the computer stays usable. Extra jobs wait in this queue. Override with CLIPTOOL_JOBS=n.
JOB_WORKERS = max(1, int(__import__("os").environ.get("CLIPTOOL_JOBS") or render.RENDER_WORKERS + 1))
JOB_QUEUE: "queue.Queue[tuple[dict, object]]" = queue.Queue()


def _worker() -> None:
    while True:
        job, work = JOB_QUEUE.get()
        try:
            if job["cancel"]:
                job["status"], job["stage"] = "cancelled", "Cancelled"
                continue
            job["status"] = "running"
            job["stage"] = job.pop("start_stage", job["stage"])
            _run_job(job, work)
        finally:
            JOB_QUEUE.task_done()


def _queue_stage(job: dict) -> str:
    """Live 'waiting' text for a queued job: how many are ahead of it."""
    if job.get("wait_msg"):
        return job["wait_msg"]
    queued = [j for j in JOBS.values() if j["status"] == "queued"]
    ahead = queued.index(job) if job in queued else 0
    return f"Waiting for a free slot ({ahead} ahead)" if ahead else "Waiting for a free slot (next)"


def _merged_changes(job: dict) -> dict:
    """All saves folded into one edit job, applied in the order they were made."""
    out: dict = {}
    for prep in job.get("preps") or []:
        out.update(prep())
    return out


def _edit_work(run: Path, rank: int, job: dict):
    """Render one clip. While another render of the same clip is still going this job stays 'waiting',
    so newer saves of that clip are folded into it (see _enqueue) instead of rendering again."""
    def work(log, stage, cancelled):
        lock = pipeline.key_lock(f"{pipeline._run_key(run)}#{rank}")
        if not lock.acquire(blocking=False):
            with JOBS_LOCK:
                job["status"], job["wait_msg"] = "queued", f"Waiting for clip #{rank}'s current render"
            lock.acquire()
        try:
            with JOBS_LOCK:
                job["sealed"] = True  # from here on new saves get their own job
                job.pop("wait_msg", None)
                job["status"], job["stage"] = "running", job.get("start_stage") or job["stage"]
            if job["cancel"]:
                raise pipeline.Cancelled()
            return pipeline.edit_clip(run, rank, _merged_changes(job), log=log, stage=stage)
        finally:
            lock.release()
    return work


def _enqueue(job: dict, work) -> dict:
    """Queue a job. A new edit of a clip that already has an edit waiting is folded into that one
    (one render with both sets of changes, newest wins) instead of rendering the clip twice."""
    if job.get("kind") == "edit":
        twin = next((j for j in JOBS.values() if j["status"] == "queued" and j.get("kind") == "edit" and not j["cancel"]
                     and not j.get("sealed")
                     and j.get("folder") == job.get("folder") and j.get("rank") == job.get("rank")), None)
        if twin:
            twin["preps"].extend(job["preps"])
            twin["start_stage"] = f"Re-rendering clip #{job.get('rank')} (with your latest changes)"
            return twin
    job.setdefault("created", time.time())
    job["start_stage"] = job["stage"]
    job["status"] = "queued"
    if len(_active()) >= JOB_WORKERS:
        job["stage"] = "Waiting for a free slot"
    JOBS[job["id"]] = job
    JOB_QUEUE.put((job, work))
    return job


def _run_job(job: dict, work) -> None:
    """Run `work(log, stage, cancelled) -> result dict` in this thread, recording progress on `job`."""
    def log(msg: str):
        for line in str(msg).splitlines() or [""]:
            job["log"].append(line)
        del job["log"][:-300]
        print(msg, flush=True)

    def stage(name: str, p: float):
        job["stage"], job["progress"] = name, round(p, 3)

    t0 = job["started"] = time.time()
    try:
        job["result"] = work(log, stage, lambda: job["cancel"])
        job["result"]["seconds"] = round(time.time() - t0, 1)
        job["status"] = "done"
    except pipeline.Cancelled:
        job["status"], job["stage"] = "cancelled", "Cancelled"
    except Exception as e:  # report any failure to the page
        log(traceback.format_exc(limit=3))
        job["status"], job["error"] = "error", str(e) or e.__class__.__name__
    finally:
        job["finished"] = time.time()


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
    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") or {}

    def _favs_route(self, path: str, method: str) -> bool:
        """Favorite folders: list / create / rename / delete, view, add clips, remove a clip, download list."""
        try:
            if path == "/api/favs":
                if method == "GET":
                    self._json({"folders": favorites.list_folders(), "root": str(favorites.root())})
                elif method == "POST":
                    self._json(favorites.create(self._body().get("name")), HTTPStatus.CREATED)
                else:
                    self._error("not allowed", HTTPStatus.METHOD_NOT_ALLOWED)
                return True
            m = re.fullmatch(r"/api/favs/([^/]+)/export\.(xlsx|csv)", path)
            if m and method == "GET":
                from . import export

                table = favorites.export_rows(m.group(1))
                body = export.to_xlsx(table, m.group(1)) if m.group(2) == "xlsx" else export.to_csv(table)
                ctype = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if m.group(2) == "xlsx"
                         else "text/csv; charset=utf-8")
                from .render import slugify

                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Content-Disposition", f'attachment; filename="{slugify(m.group(1)) or "favorites"}_clips.{m.group(2)}"')
                self.end_headers()
                self.wfile.write(body)
                return True
            m = re.fullmatch(r"/api/favs/([^/]+)/items/([A-Za-z0-9_-]+)", path)
            if m and method == "DELETE":
                self._json(favorites.remove(m.group(1), m.group(2)))
                return True
            m = re.fullmatch(r"/api/favs/([^/]+)/add", path)
            if m and method == "POST":
                body = self._body()
                run = _run_dir(str(body.get("folder") or ""))
                if not run:
                    self._error("That run is gone.", HTTPStatus.NOT_FOUND)
                    return True
                name = m.group(1)
                if body.get("create") and name not in {f["name"] for f in favorites.list_folders()}:
                    name = favorites.create(name)["name"]
                self._json(favorites.add(name, run, [int(r) for r in body.get("ranks") or []]))
                return True
            m = re.fullmatch(r"/api/favs/([^/]+)/rename", path)
            if m and method == "POST":
                self._json(favorites.rename(m.group(1), self._body().get("to")))
                return True
            m = re.fullmatch(r"/api/favs/([^/]+)", path)
            if m and method == "GET":
                self._json(favorites.items(m.group(1)))
                return True
            if m and method == "DELETE":
                self._json(favorites.delete(m.group(1)))
                return True
            self._error("not found", HTTPStatus.NOT_FOUND)
        except FileNotFoundError as e:
            self._error(str(e), HTTPStatus.NOT_FOUND)
        except PermissionError:
            self._error("A video in that folder is open (playing). Close it and try again.", HTTPStatus.CONFLICT)
        except (ValueError, TypeError) as e:
            self._error(str(e) or "invalid request")
        return True

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
        if path.startswith("/api/favs"):
            return self._favs_route(path, method)
        if path == "/api/user-presets" and method == "GET":
            self._json({"presets": userpresets.list_all(), "last": userpresets.LAST})
            return True
        if path == "/api/user-presets" and method == "POST":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                self._json(userpresets.save(body.get("name"), body.get("settings") or {}), HTTPStatus.CREATED)
            except (ValueError, TypeError, KeyError) as e:
                self._error(str(e) or "invalid preset")
            return True
        m3 = re.fullmatch(r"/api/user-presets/(.+)", path)
        if m3 and method == "DELETE":
            if userpresets.delete(m3.group(1)):
                self._json({"deleted": m3.group(1)})
            else:
                self._error("no such preset", HTTPStatus.NOT_FOUND)
            return True
        if path == "/api/paths" and method == "GET":
            self._json(_paths_payload())
            return True
        if path == "/api/paths" and method == "POST":
            if _busy():
                self._error("Wait for the rendering jobs to finish before changing folders.", HTTPStatus.CONFLICT)
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
            body = json.loads(self.rfile.read(n) or b"{}") or {}
            which = body.get("which")
            target = {"output": OUTPUT, "work": WORK, "watermarks": assets.WATERMARKS}.get(which)
            if which == "favorites":
                target = favorites.root()
            elif which == "fav":
                try:
                    target = favorites.root() / favorites.clean_name(body.get("name"))
                except ValueError:
                    target = None
            if not target:
                self._error("unknown folder")
                return True
            target.mkdir(parents=True, exist_ok=True)
            _open_folder(target)
            self._json({"opened": str(target)})
            return True
        if path == "/api/description" and method == "GET":
            from .description import DEFAULT_TEMPLATE

            self._json({**assets.load_settings()["description"], "default_template": DEFAULT_TEMPLATE})
            return True
        if path == "/api/description" and method == "POST":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                s = assets.load_settings()
                for k, limit in (("template", 5000), ("my_channel", 100), ("contact", 200), ("hashtags", 500)):
                    if k in body:
                        s["description"][k] = str(body[k] or "")[:limit]
                assets.save_settings(s)
                self._json(s["description"])
            except (ValueError, TypeError) as e:
                self._error(str(e) or "invalid description settings")
            return True
        if path == "/api/description/preview" and method == "POST":
            from . import description

            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            settings = {**assets.load_settings()["description"], **{k: str(body.get(k) or "") for k in
                                                                   ("template", "my_channel", "contact", "hashtags") if k in body}}
            run = _run_dir(str(body.get("folder") or ""))
            info, clip = {"title": "Original video title", "channel": "Original Channel", "id": "VIDEOID",
                          "channel_handle": "@channel"}, {"title": "Your clip title", "start": 61, "end": 92}
            if run:
                try:
                    data, info, _, _ = pipeline.load_run(run)
                    clip = data["clips"][0]
                except (FileNotFoundError, IndexError, KeyError):
                    pass
            self._json({"text": description.render(settings.get("template") or description.DEFAULT_TEMPLATE,
                                                   description.context(info, {**clip, "description": None}, settings))})
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
            out = {k: v for k, v in job.items() if k != "cancel"}
            if job["status"] == "queued":
                out["stage"] = _queue_stage(job)
            return self._json(out)
        if path == "/api/jobs":
            running = _running_job()
            queued = [j["id"] for j in JOBS.values() if j["status"] == "queued"]
            latest = queued[-1] if queued else (running["id"] if running else None)
            now = time.time()
            shown = [j for j in JOBS.values() if j["status"] in ("running", "queued") or now - j.get("finished", 0) < 15]
            return self._json({"running": running["id"] if running else None, "queued": queued, "latest": latest,
                               "workers": JOB_WORKERS, "renders": render.RENDER_WORKERS,
                               "jobs": [_job_summary(j) for j in shown]})
        if path == "/api/presets":
            return self._json({"presets": render.PRESETS, "preset_groups": render.PRESET_GROUPS, "positions": render.POSITIONS,
                               "filters": render.FILTER_PRESETS, "filter_limits": render.FILTER_LIMITS,
                               "default_facecam": render.DEFAULT_FACECAM, "default_cam_share": render.DEFAULT_CAM_SHARE,
                               "aspects": {k: {"label": v["label"], "size": v["size"], "cap_scale": v["cap_scale"],
                                               "bottom": v["bottom"], "top": v["top"]} for k, v in render.ASPECTS.items()},
                               "fonts": render.FONTS, "quality": list(render.QUALITY), "effects": render.EFFECTS})
        m = re.fullmatch(r"/api/runs/([^/]+)/clips/(\d+)/description", path)
        if m:
            run = _run_dir(m.group(1))
            if not run:
                return self._error("not found", HTTPStatus.NOT_FOUND)
            from . import description
            from .cache import Work

            data = json.loads((run / "clips.json").read_text(encoding="utf-8"))
            clip = next((c for c in data["clips"] if c["rank"] == int(m.group(2))), None)
            if not clip:
                return self._error("not found", HTTPStatus.NOT_FOUND)
            wd = pipeline.resolve_work_dir(data)
            work = Work(wd.parent, wd.name)
            info = work.load("info.json") if work.has("info.json") else dict(data["video"])
            if work.has("info.json"):
                info = description.ensure_channel_details(info, work)
            settings = assets.load_settings()["description"]
            generated = description.for_clip(info, {**clip, "description": None}, settings)
            return self._json({"text": description.for_clip(info, clip, settings), "generated": generated,
                               "custom": bool(clip.get("description"))})
        m = re.fullmatch(r"/api/runs/([^/]+)/export\.(xlsx|csv)", path)
        if m:
            run = _run_dir(m.group(1))
            if not run or not (run / "clips.json").exists():
                return self._error("not found", HTTPStatus.NOT_FOUND)
            from . import export
            from .render import slugify

            title, table = export.rows(run)
            if m.group(2) == "xlsx":
                body = export.to_xlsx(table)
                ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            else:
                body, ctype = export.to_csv(table), "text/csv; charset=utf-8"
            name = f"{slugify(title)[:60] or 'clips'}_clips.{m.group(2)}"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        m = re.fullmatch(r"/api/runs/([^/]+)/source-info", path)
        if m:
            run = _run_dir(m.group(1))
            if not run:
                return self._error("not found", HTTPStatus.NOT_FOUND)
            try:
                return self._json(pipeline.source_info(run))
            except FileNotFoundError as e:
                return self._error(str(e), HTTPStatus.GONE)
        m = re.fullmatch(r"/api/runs/([^/]+)/(overview|overview-wave)", path)
        if m:
            run = _run_dir(m.group(1))
            if not run:
                return self._error("not found", HTTPStatus.NOT_FOUND)
            try:
                data, info, sentences, source = pipeline.load_run(run)
            except FileNotFoundError as e:
                return self._error(str(e), HTTPStatus.GONE)
            if m.group(2) == "overview":
                return self._json({
                    "video": {k: info.get(k) for k in ("id", "title", "channel", "duration", "webpage_url")},
                    "heatmap": info.get("heatmap") or [],
                    "clips": [{"rank": c["rank"], "start": c["start"], "end": c["end"], "title": c["title"],
                               "manual": bool(c.get("manual"))} for c in data["clips"]],
                    "sentences": [{"start": round(x["start"], 2), "end": round(x["end"], 2), "text": x["text"]} for x in sentences],
                    "source_url": f"/runsrc/{run.name}", "folder": run.name,
                })
            from .cache import Work
            from .signals import waveform

            wd = pipeline.resolve_work_dir(data)
            work = Work(wd.parent, wd.name)
            if work.has("overview_wave.json"):
                return self._json(work.load("overview_wave.json"))
            dur = float(info.get("duration") or 0)
            if dur <= 0:
                return self._error("unknown duration")
            from .ffmpeg import read_audio_range
            import numpy as np

            samples = read_audio_range(source, 0, dur, 1000)  # 1 kHz is plenty for an overview
            buckets = 2000
            n = max(1, len(samples) // buckets)
            rms = np.sqrt(np.mean(samples[: n * buckets].reshape(-1, n) ** 2, axis=1)) if len(samples) >= buckets else np.zeros(buckets)
            db = 20 * np.log10(rms + 1e-5)
            lo, hi = np.percentile(db, 5), db.max()
            out = {"lo": 0.0, "hi": dur, "peaks": [round(float(v), 3) for v in np.clip((db - lo) / max(1e-6, hi - lo), 0, 1)]}
            work.save("overview_wave.json", out)
            return self._json(out)
        m = re.fullmatch(r"/api/runs/([^/]+)/clips/(\d+)/(waveform|silences)", path)
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
            from urllib.parse import parse_qs

            from . import signals

            q = parse_qs(urlparse(self.path).query)
            if m.group(3) == "waveform":
                lo = max(0.0, float(q.get("lo", [clip["start"] - 30])[0]))
                hi = min(float(info.get("duration") or 1e9), float(q.get("hi", [clip["end"] + 30])[0]))
                if hi - lo <= 0.5 or hi - lo > 600:
                    return self._error("bad range")
                return self._json({"lo": lo, "hi": hi, "peaks": signals.waveform(source, lo, hi, 800)})
            a = float(q.get("start", [clip["start"]])[0])
            b = float(q.get("end", [clip["end"]])[0])
            return self._json({"zones": signals.silent_parts({"start": a, "end": b}, sentences, source)})
        m = re.fullmatch(r"/api/runs/([^/]+)/clips/(\d+)/hook", path)
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
            from .signals import find_hook
            from .cache import Work

            wd = pipeline.resolve_work_dir(data)
            cues = Work(wd.parent, wd.name).load("transcript.json").get("cues")
            return self._json({"hook": find_hook(clip, sentences, info, source, cues)})
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
        m = re.fullmatch(r"/favfile/(.+)", path)
        if m:
            p = favorites.file_path(m.group(1))
            return self._file(p, download="download=1" in (url.query or "")) if p else self._error("not found", HTTPStatus.NOT_FOUND)
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
            jid = uuid.uuid4().hex[:12]
            job = {"id": jid, "kind": "run", "url": url, "status": "queued", "stage": "Starting", "progress": 0.0,
                   "log": [], "result": None, "error": None, "cancel": False}
            style = body.get("style", "highlight")
            if style not in render.PRESETS:
                style = "highlight"
            opts = pipeline.Options(url=url, count=count, min_s=min_s, max_s=max_s, aspect=aspect, fit=fit,
                                    captions=bool(body.get("captions", True)), caption_style=style,
                                    hook_intro=bool(body.get("hook_intro")),
                                    speed=min(2.0, max(0.5, float(body.get("speed") or 1.0))),
                                    preset=(str(body.get("preset")) if body.get("preset") else None),
                                    out=OUTPUT, work=WORK)

            def work(log, stage, cancelled):
                # The same video twice at once would share its download: let the second one wait.
                with pipeline.key_lock("video:" + (video_id_from_url(url) or url)):
                    res = pipeline.run(opts, log=log, stage=stage, cancelled=cancelled)
                job["title"] = res["info"]["title"]
                return {"folder": res["out_dir"].name, "title": res["info"]["title"], "clips": res["clips"]}

            with JOBS_LOCK:
                _enqueue(job, work)
            return self._json({"id": jid, "status": job["status"], "stage": job["stage"]}, HTTPStatus.ACCEPTED)
        m = re.fullmatch(r"/api/runs/([^/]+)/fast-source", path)
        if m:
            run = _run_dir(m.group(1))
            if not run:
                return self._error("not found", HTTPStatus.NOT_FOUND)
            with JOBS_LOCK:
                if any(j.get("folder") == run.name for j in _active()):
                    return self._error("Clips of this video are rendering; try again when they finish.", HTTPStatus.CONFLICT)
                jid = uuid.uuid4().hex[:12]
                job = {"id": jid, "kind": "source", "folder": run.name, "status": "queued",
                       "stage": "Getting the faster (H.264) video", "progress": 0.02, "log": [], "result": None,
                       "error": None, "cancel": False}
                _enqueue(job, lambda log, stage, cancelled: pipeline.make_source_fast(run, log=log, stage=stage))
            return self._json({"id": jid, "status": job["status"], "stage": job["stage"]}, HTTPStatus.ACCEPTED)
        m = re.fullmatch(r"/api/runs/([^/]+)/apply-preset", path)
        if m:
            run = _run_dir(m.group(1))
            if not run:
                return self._error("not found", HTTPStatus.NOT_FOUND)
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                ps = userpresets.get(str(body.get("name") or ""))
                if ps is None:
                    raise ValueError("That preset doesn't exist any more.")
                data, info, sentences, source = pipeline.load_run(run)
                want = set(int(r) for r in body.get("ranks") or [c["rank"] for c in data["clips"]])
            except FileNotFoundError as e:
                return self._error(str(e), HTTPStatus.GONE)
            except (ValueError, TypeError) as e:
                return self._error(str(e) or "invalid request")
            ids = []
            for c in data["clips"]:
                if c["rank"] not in want:
                    continue
                rank_no = c["rank"]
                jid = uuid.uuid4().hex[:12]
                job = {"id": jid, "kind": "edit", "folder": run.name, "rank": rank_no, "status": "queued",
                       "stage": f"Applying preset to clip #{rank_no}", "progress": 0.05, "log": [], "result": None,
                       "error": None, "cancel": False,
                       "preps": [lambda c=c: userpresets.changes_for_clip(ps, c, sentences, info, source)]}
                with JOBS_LOCK:
                    job = _enqueue(job, _edit_work(run, rank_no, job))
                if job["id"] not in ids:
                    ids.append(job["id"])
            return self._json({"jobs": ids}, HTTPStatus.ACCEPTED)
        m = re.fullmatch(r"/api/runs/([^/]+)/clips", path)
        if m:
            run = _run_dir(m.group(1))
            if not run:
                return self._error("not found", HTTPStatus.NOT_FOUND)
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                a, b = float(body["start"]), float(body["end"])
                if not 3.0 <= b - a <= 180.0:
                    raise ValueError("A clip must be 3-180 seconds long.")
            except (ValueError, TypeError, KeyError) as e:
                return self._error(str(e) or "Choose a start and an end.")
            title = str(body.get("title") or "")
            jid = uuid.uuid4().hex[:12]
            job = {"id": jid, "kind": "add", "folder": run.name, "status": "queued", "stage": "Making your clip",
                   "progress": 0.05, "log": [], "result": None, "error": None, "cancel": False}

            def work(log, stage, cancelled):
                return pipeline.add_clip(run, a, b, title, log=log, stage=stage)

            with JOBS_LOCK:
                _enqueue(job, work)
            return self._json({"id": jid, "status": job["status"], "stage": job["stage"]}, HTTPStatus.ACCEPTED)
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
            jid = uuid.uuid4().hex[:12]
            job = {"id": jid, "kind": "edit", "folder": run.name, "rank": rank, "status": "queued",
                   "stage": f"Re-rendering clip #{rank}", "progress": 0.05, "log": [], "result": None,
                   "error": None, "cancel": False, "preps": [lambda: changes]}
            with JOBS_LOCK:
                job = _enqueue(job, _edit_work(run, rank, job))
            return self._json({"id": job["id"], "status": job["status"], "stage": job["stage"]}, HTTPStatus.ACCEPTED)
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
            m = re.fullmatch(r"/api/runs/([^/]+)/clips/(\d+)", path)
            if m and any(j.get("folder") == m.group(1) and j.get("rank") in (None, int(m.group(2)))
                         and j.get("kind") in ("edit", "add") for j in _active()):
                return self._error("This clip is still rendering; wait for it to finish or cancel it first.",
                                   HTTPStatus.CONFLICT)
            if not m and _busy():
                return self._error("Videos are rendering; wait for them to finish or cancel them first.", HTTPStatus.CONFLICT)
            try:
                if m:
                    run = _run_dir(m.group(1))
                    if not run:
                        return self._error("not found", HTTPStatus.NOT_FOUND)
                    try:
                        return self._json(pipeline.delete_clip(run, int(m.group(2))))
                    except ValueError as e:
                        return self._error(str(e), HTTPStatus.NOT_FOUND)
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


def _job_summary(j: dict) -> dict:
    kind = j.get("kind", "run")
    if kind == "edit":
        label = f"Clip #{j.get('rank')}"
    elif kind == "source":
        label = "Faster video (H.264)"
    elif kind == "add":
        label = "Your new clip"
    else:
        label = j.get("title") or "New video"
    res = j.get("result") or {}
    return {"id": j["id"], "kind": kind, "label": label, "status": j["status"],
            "stage": _queue_stage(j) if j["status"] == "queued" else j["stage"],
            "progress": j.get("progress", 0), "error": j.get("error"), "url": j.get("url"),
            "folder": res.get("folder") or j.get("folder"), "rank": j.get("rank") or res.get("rank"),
            "seconds": round(((j.get("finished") or time.time()) - j["started"]), 1) if j.get("started") else None,
            "log_tail": j["log"][-1] if j.get("log") else ""}


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = False) -> None:
    refresh_paths()
    for n in range(JOB_WORKERS):
        threading.Thread(target=_worker, daemon=True, name=f"cliptool-jobs-{n}").start()
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
