"""Per-video work folder: work/<video_id>/ holds each stage's result."""
from __future__ import annotations

import json
from pathlib import Path


class Work:
    def __init__(self, root: Path, video_id: str):
        self.dir = root / video_id
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        return self.dir / name

    def has(self, name: str) -> bool:
        p = self.path(name)
        return p.exists() and p.stat().st_size > 0

    def load(self, name: str):
        return json.loads(self.path(name).read_text(encoding="utf-8"))

    def save(self, name: str, data) -> Path:
        p = self.path(name)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return p
