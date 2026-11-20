"""Stage 2: timed words -> sentences.

Sources, in order: YouTube captions (json3), then faster-whisper on the audio.
Output (transcript.json): {"source": ..., "sentences": [{start, end, text, words: [{w, s, e}]}]}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .cache import Work
from .ffmpeg import run as ffmpeg_run

SENT_END = re.compile(r"[.?!]['\")\]]*$")


def words_from_json3(path: Path) -> list[dict]:
    """Parse YouTube json3 captions into [{w, s, e}] with times in seconds.

    Auto captions carry a per-word offset (tOffsetMs); manual captions only
    carry per-line times, so words in a line are spread evenly over it.
    Auto captions also repeat rolling lines; events with aAppend or only a
    newline are skipped.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    events = [e for e in data.get("events", []) if e.get("segs")]
    words: list[dict] = []
    for i, ev in enumerate(events):
        if ev.get("aAppend"):
            continue
        t0 = ev.get("tStartMs", 0) / 1000
        dur = ev.get("dDurationMs", 0) / 1000
        segs = [s for s in ev["segs"] if s.get("utf8", "").strip()]
        if not segs:
            continue
        has_offsets = any("tOffsetMs" in s for s in segs)
        if has_offsets:
            for j, s in enumerate(segs):
                st = t0 + s.get("tOffsetMs", 0) / 1000
                if j + 1 < len(segs):
                    en = t0 + segs[j + 1].get("tOffsetMs", 0) / 1000
                else:
                    nxt = next((events[k] for k in range(i + 1, len(events)) if not events[k].get("aAppend")), None)
                    en = min(t0 + dur, nxt["tStartMs"] / 1000) if nxt else t0 + dur
                    en = max(en, st + 0.2)
                for tok in s["utf8"].split():
                    words.append({"w": tok, "s": round(st, 3), "e": round(en, 3)})
        else:
            text = " ".join(s["utf8"] for s in segs).replace("\n", " ")
            toks = text.split()
            if not toks:
                continue
            # A line without word timing lasts until the next line starts - YouTube's duration often overlaps
            # the following line by ~2 s, which made words appear late. Spread words by their length.
            nxt = next((events[k] for k in range(i + 1, len(events)) if not events[k].get("aAppend")), None)
            span = dur or 0.4 * len(toks)
            if nxt:
                span = min(span, max(0.3, nxt["tStartMs"] / 1000 - t0))
            span = min(span, 0.6 * len(toks) + 0.5)  # nobody speaks slower than this; the rest is silence
            weights = [len(t) + 2 for t in toks]
            total, acc = sum(weights), 0
            for tok, wt in zip(toks, weights):
                st = t0 + span * acc / total
                acc += wt
                words.append({"w": tok, "s": round(st, 3), "e": round(t0 + span * acc / total, 3)})
    words.sort(key=lambda x: x["s"])
    # Drop exact duplicates that rolling captions can produce.
    out: list[dict] = []
    for w in words:
        if out and w["w"] == out[-1]["w"] and abs(w["s"] - out[-1]["s"]) < 0.05:
            continue
        out.append(w)
    return out


CUE_RE = re.compile(r"^[\(\[]\s*([A-Za-z ]+?)\s*[\)\]][.,]?$")
REACTIONS = {"laughter", "laughs", "laughing", "applause", "cheering", "cheers", "audience laughs"}


def split_cues(words: list[dict]) -> tuple[list[dict], list[dict]]:
    """Pull caption cues like (Laughter) / [Applause] / [Music] out of the words.

    Returns (spoken words, cues) where cues are {t, kind, reaction}.
    """
    spoken, cues = [], []
    for w in words:
        if w["w"] in (">>", "&gt;&gt;", "-", "--"):  # speaker-change markers, not speech
            continue
        m = CUE_RE.match(w["w"])
        if m:
            kind = m.group(1).strip().lower()
            cues.append({"t": w["s"], "kind": kind, "reaction": kind in REACTIONS})
        else:
            spoken.append(w)
    return spoken, cues


def words_from_whisper(video: Path, work: Work, lang: str, model_size: str) -> list[dict]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise RuntimeError(
            "This video has no captions and faster-whisper is not installed.\n"
            "Install it with:  pip install -e \".[whisper]\""
        ) from e
    wav = work.path("audio16k.wav")
    if not wav.exists():
        ffmpeg_run(["-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
    print(f"  transcribing with faster-whisper ({model_size}); this can take a while...")
    model = WhisperModel(model_size, device="auto", compute_type="int8")
    segments, _ = model.transcribe(str(wav), language=lang, word_timestamps=True, vad_filter=True)
    words = []
    for seg in segments:
        for w in seg.words or []:
            tok = w.word.strip()
            if tok:
                words.append({"w": tok, "s": round(w.start, 3), "e": round(w.end, 3)})
    return words


def words_to_sentences(words: list[dict], max_words: int = 28, max_secs: float = 10.0, pause: float = 0.6) -> list[dict]:
    """Group words into sentences on punctuation, long pauses, or length caps."""
    sents: list[dict] = []
    cur: list[dict] = []
    has_punct = sum(1 for w in words if SENT_END.search(w["w"])) > len(words) / 60

    def flush():
        if cur:
            sents.append({
                "start": cur[0]["s"], "end": cur[-1]["e"],
                "text": " ".join(w["w"] for w in cur), "words": list(cur),
            })
            cur.clear()

    for i, w in enumerate(words):
        if cur and (w["s"] - cur[-1]["e"] > pause):
            flush()
        cur.append(w)
        dur = cur[-1]["e"] - cur[0]["s"]
        if has_punct and SENT_END.search(w["w"]):
            flush()
        elif len(cur) >= max_words or dur >= max_secs:
            flush()
    flush()
    return sents


TRANSCRIPT_VERSION = 2  # bump when caption parsing changes; cached caption transcripts are rebuilt


def cached_transcript(work: Work) -> dict:
    """The video's transcript, rebuilt from its cached captions if it was made by an older version."""
    t = work.load("transcript.json")
    if t.get("source") == "captions" and t.get("v") != TRANSCRIPT_VERSION and work.has("captions.json3"):
        words, cues = split_cues(words_from_json3(work.path("captions.json3")))
        if words:
            t = {"source": "captions", "v": TRANSCRIPT_VERSION, "sentences": words_to_sentences(words), "cues": cues}
            work.save("transcript.json", t)
    return t


def get_transcript(work: Work, captions: Path | None, video_fn, lang: str, whisper_model: str, force_whisper: bool = False) -> dict:
    """video_fn: callable returning the source video path (downloads lazily)."""
    if work.has("transcript.json"):
        t = cached_transcript(work)
        fresh = "cues" in t  # transcripts cached by older versions lack cues
        if fresh and (not force_whisper or t.get("source") == "whisper"):
            return t
    if captions and not force_whisper:
        words = words_from_json3(captions)
        source = "captions"
    else:
        words = words_from_whisper(video_fn(), work, lang, whisper_model)
        source = "whisper"
    words, cues = split_cues(words)
    if not words:
        raise RuntimeError("Transcript is empty: the video seems to have no speech.")
    t = {"source": source, "v": TRANSCRIPT_VERSION, "sentences": words_to_sentences(words), "cues": cues}
    work.save("transcript.json", t)
    return t
