"""Stage 4a: local signals per candidate, each normalised to 0-1 within the video.

H heatmap  - YouTube "most replayed" curve over the window (+ early-peak bonus)
A audio    - share of the window louder than the video's 80th percentile
T hooks    - does the opening grab attention; filler words subtract
P pacing   - speech rate near the video median, clean ending, no dead air
"""
from __future__ import annotations

import math
import re

import numpy as np

from .candidates import window_words

HOOK_PHRASES = [
    "nobody tells you", "no one tells you", "here's why", "here is why", "the truth", "the secret",
    "the mistake", "biggest mistake", "the reason", "what happened", "let me tell you", "i'll never forget",
    "the problem is", "the key is", "the trick", "you need to", "you have to", "stop doing", "most people",
    "the real reason", "here's the thing", "here's what", "the one thing", "if you want", "imagine",
    "story", "turns out", "i realized", "game changer", "changed my life", "the best", "the worst",
]
STRONG_WORDS = {
    "never", "always", "secret", "mistake", "best", "worst", "crazy", "insane", "truth", "actually",
    "biggest", "huge", "shocking", "wrong", "wild", "everyone", "nobody", "million", "billion", "fail",
    "failed", "money", "free", "hate", "love", "dangerous", "illegal", "impossible", "only",
}
QUESTION_STARTS = ("what", "why", "how", "when", "who", "where", "did you", "have you", "do you",
                   "is it", "are you", "can you", "would you", "should you", "ever wonder")
BAD_STARTS = {"and", "so", "but", "um", "uh", "like", "yeah", "because", "or", "also", "then", "which", "that's",
              "anyway", "anyways", "plus", "except", "although", "though"}
# Openers that usually point back at something the viewer of a short has not seen.
DANGLING_STARTS = {"he", "she", "it", "they", "them", "him", "her", "that", "this", "those", "these", "there", "it's",
                   "he's", "she's", "they're", "that's"}
FILLERS = {"um", "uh", "erm", "hmm", "like", "basically", "literally"}
NUM_RE = re.compile(r"\d|\b(one|two|three|four|five|six|seven|eight|nine|ten|hundred|thousand|million|billion)\b")
WORD_RE = re.compile(r"[a-z0-9']+")


def _norm(x: np.ndarray) -> np.ndarray:
    lo, hi = float(np.min(x)), float(np.max(x))
    if hi - lo < 1e-9:
        return np.full_like(x, 0.5, dtype=float)
    return (x - lo) / (hi - lo)


def heatmap_scores(cands: list[dict], heatmap: list[dict], duration: float) -> np.ndarray | None:
    if not heatmap:
        return None
    secs = int(math.ceil(duration or heatmap[-1]["end_time"])) + 1
    curve = np.zeros(secs)
    for h in heatmap:
        a, b = int(h["start_time"]), max(int(h["start_time"]) + 1, int(math.ceil(h["end_time"])))
        curve[a:min(b, secs)] = h["value"]
    cum = np.concatenate([[0.0], np.cumsum(curve)])
    out = np.empty(len(cands))
    for k, c in enumerate(cands):
        a, b = int(c["start"]), max(int(c["start"]) + 1, int(c["end"]))
        b = min(b, secs)
        mean = (cum[b] - cum[a]) / max(1, b - a)
        early = curve[a:min(a + 5, secs)].max() if a < secs else 0.0
        out[k] = mean + 0.25 * early
    return out


def audio_scores(cands: list[dict], samples: np.ndarray, rate: int, hop_s: float = 0.5) -> np.ndarray:
    hop = int(rate * hop_s)
    n = len(samples) // hop
    if n == 0:
        return np.zeros(len(cands))
    frames = samples[: n * hop].reshape(n, hop)
    rms_db = 20 * np.log10(np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-6)
    loud = (rms_db > np.percentile(rms_db, 80)).astype(float)
    cum = np.concatenate([[0.0], np.cumsum(loud)])
    out = np.empty(len(cands))
    for k, c in enumerate(cands):
        a, b = int(c["start"] / hop_s), int(c["end"] / hop_s)
        a, b = min(a, n), min(max(b, a + 1), n)
        out[k] = (cum[b] - cum[a]) / max(1, b - a)
    return out


def hook_score_text(opening: str, all_words: list[str]) -> float:
    t = opening.lower().strip()
    toks = WORD_RE.findall(t)
    s = 0.0
    if "?" in t or t.startswith(QUESTION_STARTS):
        s += 0.3
    if NUM_RE.search(t):
        s += 0.15
    if any(p in t for p in HOOK_PHRASES):
        s += 0.4
    s += min(0.3, 0.1 * sum(1 for w in toks if w in STRONG_WORDS))
    if toks and toks[0] in BAD_STARTS:
        s -= 0.4
    elif toks and toks[0] in DANGLING_STARTS:
        s -= 0.2
    if all_words:
        filler_rate = sum(1 for w in all_words if w in FILLERS) / len(all_words)
        s -= 2.0 * filler_rate
    return s


def hook_scores(cands: list[dict], sentences: list[dict]) -> np.ndarray:
    out = np.empty(len(cands))
    for k, c in enumerate(cands):
        opening = sentences[c["i"]]["text"]
        # Include the next sentence if the first is very short ("Okay.").
        if len(opening.split()) < 5 and c["j"] > c["i"]:
            opening += " " + sentences[c["i"] + 1]["text"]
        words = [w for s in sentences[c["i"]: c["j"] + 1] for w in WORD_RE.findall(s["text"].lower())]
        out[k] = hook_score_text(opening, words)
    return out


def pacing_scores(cands: list[dict], sentences: list[dict]) -> np.ndarray:
    rates = [len(s["words"]) / max(0.5, s["end"] - s["start"]) for s in sentences]
    med = float(np.median(rates)) if rates else 2.5
    out = np.empty(len(cands))
    for k, c in enumerate(cands):
        words = window_words(sentences, c)
        wps = len(words) / max(1.0, c["duration"])
        s = math.exp(-4 * ((wps - med) / max(med, 0.5)) ** 2)
        if re.search(r"[.?!]['\")]*$", sentences[c["j"]]["text"]):
            s += 0.2
        gaps = [words[x + 1]["s"] - words[x]["e"] for x in range(len(words) - 1)]
        if gaps and max(gaps) > 2.0:
            s -= 0.3
        out[k] = s
    return out


def reaction_counts(cands: list[dict], cues: list[dict]) -> np.ndarray:
    """Audience reactions (laughter, applause) inside the window or just after it."""
    times = np.array(sorted(c["t"] for c in cues if c.get("reaction")))
    out = np.zeros(len(cands))
    if len(times) == 0:
        return out
    for k, c in enumerate(cands):
        out[k] = np.searchsorted(times, c["end"] + 3.0) - np.searchsorted(times, c["start"])
    return out


def score_candidates(cands, sentences, info, samples, rate, weights, cues=None) -> None:
    """Adds `signals` and `local` (0-1 composite) to each candidate in place."""
    if not cands:
        return
    H = heatmap_scores(cands, info.get("heatmap") or [], info.get("duration") or 0)
    A = audio_scores(cands, samples, rate) if samples is not None else np.zeros(len(cands))
    R = reaction_counts(cands, cues or [])
    A = A + 0.35 * np.minimum(R, 3)  # captioned laughter/applause counts as audio energy
    for k, c in enumerate(cands):
        c["reactions"] = int(R[k])
    T = hook_scores(cands, sentences)
    P = pacing_scores(cands, sentences)

    w = {k: float(weights[k]) for k in ("heatmap", "audio", "hooks", "pacing")}
    if H is None:
        rest = w["audio"] + w["hooks"] + w["pacing"]
        scale = (rest + w["heatmap"]) / rest
        w = {"heatmap": 0.0, "audio": w["audio"] * scale, "hooks": w["hooks"] * scale, "pacing": w["pacing"] * scale}
        Hn = np.zeros(len(cands))
    else:
        Hn = _norm(H)
    An, Tn, Pn = _norm(A), _norm(T), _norm(P)
    pen = float(weights.get("chapter_cross_penalty", 0.1))
    for k, c in enumerate(cands):
        c["signals"] = {
            "heatmap": round(float(Hn[k]), 3) if H is not None else None,
            "audio": round(float(An[k]), 3),
            "hooks": round(float(Tn[k]), 3),
            "pacing": round(float(Pn[k]), 3),
        }
        local = (w["heatmap"] * Hn[k] + w["audio"] * An[k] + w["hooks"] * Tn[k] + w["pacing"] * Pn[k]
                 - (pen if c["chapter_cross"] else 0.0))
        c["local"] = round(float(local), 4)


# ---- hook intro: the most gripping 3-5 s inside a clip, to play first ------------------------

SENT_END_RE = re.compile(r"[.?!]['\")]*$")


def find_hook(clip: dict, sentences: list[dict], info: dict, source, cues=None,
              min_len: float = 3.0, max_len: float = 5.0, skip_start: float = 3.0) -> dict | None:
    """Pick a 3-5 s teaser from inside the clip (not its first seconds, which play anyway).

    Every window that starts on a word and ends on a word end is scored on:
      heatmap (most replayed) 0.25   audio energy / reactions 0.30
      words (question, number, strong claim; no filler start) 0.25
      clean ending 0.10   opens a thought (sentence start / after a pause) 0.15
    Returns {"start", "end", "text", "score"} in source seconds, or None.
    """
    from .ffmpeg import read_audio_range

    words = [w for s in sentences if s["end"] > clip["start"] and s["start"] < clip["end"] for w in s["words"]
             if clip["start"] + skip_start <= w["s"] and w["e"] <= clip["end"] and w["w"] not in (">>", "&gt;&gt;")]
    if len(words) < 3:
        return None
    rate, hop = 8000, 0.25
    samples = read_audio_range(source, clip["start"], clip["end"] - clip["start"] + 0.5, rate)
    n = len(samples) // int(rate * hop)
    if n:
        frames = samples[: n * int(rate * hop)].reshape(n, -1)
        db = 20 * np.log10(np.sqrt(np.mean(frames ** 2, axis=1)) + 1e-6)
        loud = (db > np.percentile(db, 75)).astype(float)
        cum = np.concatenate([[0.0], np.cumsum(loud)])
    heat = info.get("heatmap") or []
    reacts = sorted(c["t"] for c in (cues or []) if c.get("reaction"))

    def heat_at(a, b):
        vals = [h["value"] for h in heat if h["end_time"] > a and h["start_time"] < b]
        return max(vals) if vals else 0.0

    cands = []
    for i in range(len(words)):
        for j in range(i, len(words)):
            a, b = words[i]["s"], words[j]["e"]
            d = b - a
            if d > max_len:
                break
            if d < min_len:
                continue
            text = " ".join(w["w"] for w in words[i:j + 1])
            ai, bi = int((a - clip["start"]) / hop), int((b - clip["start"]) / hop)
            audio = (cum[min(bi, n)] - cum[min(ai, n)]) / max(1, bi - ai) if n else 0.0
            audio += 0.5 * min(2, sum(1 for t in reacts if a - 0.5 <= t <= b + 1.5))
            tokens = WORD_RE.findall(text.lower())
            words_s = hook_score_text(text, tokens)
            ending = 1.0 if SENT_END_RE.search(words[j]["w"]) else 0.0
            # A hook should open a thought: start of a sentence, or after a pause.
            prev = words[i - 1] if i else None
            opens = 1.0 if (prev is None or SENT_END_RE.search(prev["w"]) or words[i]["s"] - prev["e"] > 0.35) else 0.0
            if SENT_END_RE.search(words[i]["w"]) and j > i:
                opens = -1.0  # never start on the last word of a sentence
            cands.append([a, b, text, heat_at(a, b), audio, words_s, ending, opens])
    if not cands:
        return None
    arr = np.array([c[3:] for c in cands], dtype=float)
    norm = np.column_stack([_norm(arr[:, k]) for k in range(arr.shape[1])])
    score = norm @ np.array([0.25 if heat else 0.0, 0.30, 0.25, 0.10, 0.15])
    k = int(np.argmax(score))
    a, b, text = cands[k][:3]
    return {"start": round(a - 0.1, 2), "end": round(b + 0.15, 2), "text": text, "score": round(float(score[k]), 3)}



# ---- timeline waveform and silent parts --------------------------------------------------

def waveform(source, lo: float, hi: float, buckets: int = 800) -> list[float]:
    """Loudness per bucket over [lo, hi] (0-1, log scale) for drawing the timeline waveform."""
    from .ffmpeg import read_audio_range

    samples = read_audio_range(source, lo, hi - lo, 8000)
    if len(samples) < buckets:
        return [0.0] * buckets
    n = len(samples) // buckets
    rms = np.sqrt(np.mean(samples[: n * buckets].reshape(buckets, n) ** 2, axis=1))
    db = 20 * np.log10(rms + 1e-5)
    lo_db, hi_db = np.percentile(db, 5), db.max()
    return [round(float(v), 3) for v in np.clip((db - lo_db) / max(1e-6, hi_db - lo_db), 0, 1)]


def silent_parts(clip: dict, sentences: list[dict], source, min_gap: float = 1.0, speed: float = 2.0) -> list[dict]:
    """Stretches inside the clip with no speech and quiet audio - good candidates to speed up."""
    from .ffmpeg import read_audio_range

    a, b = clip["start"], clip["end"]
    words = sorted((w for s in sentences if s["end"] > a and s["start"] < b for w in s["words"]
                    if a - 1 <= w["s"] <= b + 1), key=lambda w: w["s"])
    gaps, cur = [], a
    for w in words:
        if w["s"] - cur >= min_gap:
            gaps.append((cur, w["s"]))
        cur = max(cur, w["e"])
    if b - cur >= min_gap:
        gaps.append((cur, b))
    if not gaps:
        return []
    rate, hop = 8000, 0.1
    samples = read_audio_range(source, a, b - a, rate)
    n = len(samples) // int(rate * hop)
    if not n:
        return []
    rms = np.sqrt(np.mean(samples[: n * int(rate * hop)].reshape(n, -1) ** 2, axis=1))
    quiet = np.percentile(rms, 45)
    zones = []
    for ga, gb in gaps:
        ga, gb = max(a, ga + 0.15), min(b, gb - 0.15)  # keep breaths around words
        if gb - ga < min_gap * 0.8:
            continue
        i, j = int((ga - a) / hop), int((gb - a) / hop)
        seg = rms[i:max(i + 1, j)]
        if len(seg) and float(np.median(seg)) <= quiet * 1.4:  # quiet, not a loud action beat
            zones.append({"start": round(ga, 2), "end": round(gb, 2), "speed": speed})
    return zones
