"""Load config.toml merged over built-in defaults."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

DEFAULTS: dict = {
    "weights": {
        "heatmap": 0.35, "audio": 0.20, "hooks": 0.30, "pacing": 0.15,
        "chapter_cross_penalty": 0.10, "ai_blend": 0.5,
    },
    "clips": {
        "count": 5, "min_seconds": 20, "max_seconds": 60,
        "max_overlap": 0.3, "finalists_for_ai": 30,
    },
    "captions": {
        "font": "Arial Black", "size": 78, "words_per_line": 3,
        "color": "#FFFFFF", "highlight": "#FFD400", "outline": 5, "margin_bottom": 520,
    },
    "encode": {"crf": 20, "preset": "medium", "loudness_lufs": -14},
    "ai": {"model": "claude-opus-5-5", "effort": "medium"},
}


def _merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load_config(path: Path | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    candidates = [path] if path else [Path.cwd() / "config.toml", Path(__file__).resolve().parent.parent / "config.toml"]
    for p in candidates:
        if p and p.is_file():
            with open(p, "rb") as f:
                _merge(cfg, tomllib.load(f))
            break
    return cfg
