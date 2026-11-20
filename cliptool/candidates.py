"""Stage 3: sentence-aligned windows between min and max seconds."""
from __future__ import annotations


def make_candidates(sentences: list[dict], min_s: float, max_s: float, chapters: list[dict] | None = None) -> list[dict]:
    """Every window that starts on a sentence start and ends on a sentence end.

    Returns [{i, j, start, end, duration, chapter_cross}] where i..j are
    inclusive sentence indexes.
    """
    bounds = sorted({c["start_time"] for c in (chapters or []) if c.get("start_time")})
    out = []
    n = len(sentences)
    for i in range(n):
        start = sentences[i]["start"]
        for j in range(i, n):
            end = sentences[j]["end"]
            dur = end - start
            if dur > max_s:
                break
            if dur >= min_s:
                cross = any(start + 1.0 < b < end - 1.0 for b in bounds)
                out.append({"i": i, "j": j, "start": start, "end": end,
                            "duration": round(dur, 3), "chapter_cross": cross})
    return out


def window_text(sentences: list[dict], c: dict) -> str:
    return " ".join(s["text"] for s in sentences[c["i"]: c["j"] + 1])


def window_words(sentences: list[dict], c: dict) -> list[dict]:
    return [w for s in sentences[c["i"]: c["j"] + 1] for w in s["words"]]
