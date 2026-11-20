"""Stage 4b: overlap suppression, optional Claude pass, final ordering."""
from __future__ import annotations

import re


def overlap(a: dict, b: dict) -> float:
    inter = min(a["end"], b["end"]) - max(a["start"], b["start"])
    if inter <= 0:
        return 0.0
    return inter / min(a["end"] - a["start"], b["end"] - b["start"])


def suppress(cands: list[dict], max_overlap: float, limit: int) -> list[dict]:
    kept: list[dict] = []
    for c in sorted(cands, key=lambda c: c["local"], reverse=True):
        if all(overlap(c, k) < max_overlap for k in kept):
            kept.append(c)
            if len(kept) >= limit:
                break
    for n, c in enumerate(kept):
        c["id"] = n
    return kept


def _snap(t: float, times: list[float]) -> float:
    return min(times, key=lambda x: abs(x - t))


LEAD_FILLER = re.compile(
    r"^(?:(?:so|and|but|um|uh|well|okay|ok|now|yeah|like|you know|i mean|right|anyway|basically|actually)[,.]?\s+)+",
    re.I)


def clean_line(text: str, limit: int) -> str:
    """Strip leading filler, tidy punctuation, cut at a word boundary within `limit` chars."""
    t = re.sub(r"\s+", " ", text).strip()
    t = LEAD_FILLER.sub("", t).strip(" ,;:-")
    t = re.sub(r"\s+([,.?!])", r"\1", t)
    if len(t) > limit:
        cut = t[: limit - 3]
        # Prefer ending at a clause break, else a word boundary.
        m = max(cut.rfind(", "), cut.rfind("; "), cut.rfind(" - "))
        t = (cut[:m] if m > limit * 0.5 else cut.rsplit(" ", 1)[0]).rstrip(" ,;:-") + "..."
    else:
        t = t.rstrip(" ,;:-")
        if t.endswith("."):
            t = t[:-1]
    return t[:1].upper() + t[1:]


def _sentence_texts(sentences: list[dict], c: dict) -> list[str]:
    return [s["text"] for s in sentences[c["i"]: c["j"] + 1]]


def local_title(sentences: list[dict], c: dict) -> str:
    """The clip's most quotable line: highest hook score, 5-16 words, early lines preferred."""
    from .signals import hook_score_text

    texts = _sentence_texts(sentences, c)
    best, best_score = texts[0], -1e9
    for k, sent in enumerate(texts):
        # A whole sentence, or a clause of it, can be the title; short complete lines read best.
        options = [sent] + [p for p in re.split(r"(?<=[,;:])\s+|\s+-\s+", sent) if p != sent]
        for t in options:
            clean = LEAD_FILLER.sub("", t).strip(" ,;:-")
            n = len(clean.split())
            if n < 4:
                continue
            s = hook_score_text(clean, []) - 0.05 * k
            s -= 0.4 if len(clean) > 60 else 0.0
            s -= 0.1 if t is not sent else 0.0  # slight preference for full sentences
            if s > best_score:
                best, best_score = clean, s
    return clean_line(best, 60)


def local_hook(sentences: list[dict], c: dict) -> str:
    texts = _sentence_texts(sentences, c)
    opening = texts[0]
    if len(opening.split()) < 5 and len(texts) > 1:
        opening += " " + texts[1]
    return clean_line(opening, 80)


def local_reason(c: dict, sentences: list[dict]) -> str:
    sig = c["signals"]
    bits = []
    if sig.get("heatmap") is not None and sig["heatmap"] >= 0.7:
        bits.append("one of the most-replayed parts of the video")
    if c.get("reactions"):
        n = c["reactions"]
        bits.append(f"gets {n} audience reaction{'s' if n > 1 else ''} (laughter/applause)")
    opening = sentences[c["i"]]["text"].lower()
    if sig.get("hooks", 0) >= 0.6:
        if "?" in opening:
            bits.append("opens with a question")
        elif re.search(r"\d", opening):
            bits.append("opens with a concrete number")
        else:
            bits.append("strong opening line")
    if sig.get("audio", 0) >= 0.7 and not c.get("reactions"):
        bits.append("high-energy delivery")
    if sig.get("pacing", 0) >= 0.7:
        bits.append("tight pacing and a clean ending")
    if not bits:
        bits.append("best balance of replay, energy, hook and pacing in this part of the video")
    text = "; ".join(bits)
    return text[:1].upper() + text[1:] + "."


def local_only(finalists: list[dict], sentences: list[dict], count: int) -> list[dict]:
    out = []
    for c in finalists[:count]:
        out.append({**c, "title": local_title(sentences, c), "hook": local_hook(sentences, c),
                    "reason": local_reason(c, sentences), "claude": None, "score": round(c["local"], 3)})
    return out


def merge_claude(picks: list[dict], finalists: list[dict], sentences: list[dict],
                 min_s: float, max_s: float, blend: float, count: int) -> list[dict]:
    by_id = {f["id"]: f for f in finalists}
    out: list[dict] = []
    for p in picks:
        f = by_id.get(p["candidate_id"])
        if not f:
            continue
        starts = [s["start"] for s in sentences[f["i"]: f["j"] + 1]]
        ends = [s["end"] for s in sentences[f["i"]: f["j"] + 1]]
        st, en = _snap(p["start"], starts), _snap(p["end"], ends)
        if not (min_s - 1 <= en - st <= max_s + 1) or en <= st:
            st, en = f["start"], f["end"]
        i = f["i"] + starts.index(st)
        j = f["i"] + ends.index(en)
        clip = {**f, "i": i, "j": j, "start": st, "end": en, "duration": round(en - st, 3),
                "title": p["title"].strip()[:80], "hook": p["hook"].strip(), "reason": p["reason"].strip(),
                "claude": max(1, min(10, int(p["score"]))) / 10}
        clip["signals"] = {**f["signals"], "claude": clip["claude"]}
        clip["score"] = round((1 - blend) * f["local"] + blend * clip["claude"], 3)
        if all(overlap(clip, o) < 0.05 for o in out):
            out.append(clip)
    out.sort(key=lambda c: c["score"], reverse=True)
    return out[:count]
