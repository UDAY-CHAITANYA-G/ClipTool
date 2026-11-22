"""Catchy on-screen titles for clips (offline, no AI): a big title that stays up for the whole clip so
viewers know right away why to keep watching.

Titles are built from what is actually said in the clip (the punchiest short line, in capitals), or a
curiosity line that fits the moment ("WAIT FOR IT...", "THIS ESCALATED QUICKLY"), with the game's name
when the source video is about a known game. No emoji: burned-in text can't draw them.
"""
from __future__ import annotations

import hashlib
import re

from .rank import LEAD_FILLER, _sentence_texts
from .signals import hook_score_text

CENSOR = re.compile(r"\[\s*_+\s*\]")
FILLER_WORDS = re.compile(r"\b(uh+|um+|erm|hmm+|like,|you know,)\s*", re.I)
GAMES = [
    (re.compile(r"\bGTA\s*(6|VI)\b|grand theft auto\s*(6|vi)\b", re.I), "GTA 6"),
    (re.compile(r"\bGTA\s*(5|V)\b|grand theft auto\s*(5|v)\b", re.I), "GTA 5"),
    (re.compile(r"\bminecraft\b", re.I), "MINECRAFT"),
    (re.compile(r"\bfortnite\b", re.I), "FORTNITE"),
    (re.compile(r"\bvalorant\b", re.I), "VALORANT"),
    (re.compile(r"\bbgmi\b|\bpubg\b", re.I), "BGMI"),
    (re.compile(r"\bfree\s*fire\b", re.I), "FREE FIRE"),
    (re.compile(r"\bcall of duty\b|\bwarzone\b", re.I), "COD"),
    (re.compile(r"\bred dead\b", re.I), "RED DEAD"),
]
REACTION = re.compile(r"\b(what the|no way|oh my god|omg|bro|are you kidding|are you serious|wait|what\?|holy|insane|crazy)\b", re.I)
MONEY = re.compile(r"(\$\s?\d|\d+\s?(k|million|billion|lakh|crore)\b|\bmoney\b|\bcash\b|\bpaid\b)", re.I)
ACTION = re.compile(r"\b(cops?|police|wanted|stars?|chase|crash|shoot|gun|fight|kill|run|explod)\w*", re.I)

CURIOUS = {
    "reaction": ["DIDN'T SEE THIS COMING", "WAIT FOR IT...", "THIS ESCALATED QUICKLY", "NOBODY EXPECTED THIS"],
    "money": ["THIS COST HOW MUCH?!", "MONEY TALKS...", "BIG MONEY MOVES"],
    "action": ["THIS GOT OUT OF HAND", "WAIT FOR THE ENDING...", "IT ALL WENT WRONG"],
    "question": ["WAIT... WHAT?!", "DID THAT JUST HAPPEN?", "YOU WON'T BELIEVE THIS"],
    "default": ["WAIT FOR IT...", "WATCH TILL THE END", "THIS IS WILD", "YOU HAVE TO SEE THIS"],
}


def game_name(video_title: str) -> str:
    for rx, name in GAMES:
        if rx.search(video_title or ""):
            return name
    return ""


def _clean(text: str) -> str:
    t = CENSOR.sub("", text or "")
    t = FILLER_WORDS.sub("", t)
    t = LEAD_FILLER.sub("", t)
    t = re.sub(r"\s+", " ", t).strip(" ,;:-.")
    return t


def _quotes(sentences: list[dict], clip: dict) -> list[tuple[float, str]]:
    """Short, punchy lines said in the clip, scored."""
    out = []
    try:
        texts = _sentence_texts(sentences, clip)
    except (KeyError, IndexError):
        texts = []
    for k, sent in enumerate(texts):
        for part in [sent] + re.split(r"(?<=[,;:.!?])\s+", sent):
            if CENSOR.search(part):
                continue  # a bleeped word leaves a broken line ("WHAT THE!")
            t = _clean(part)
            n = len(t.split())
            if not 2 <= n <= 7 or len(t) > 42:
                continue
            toks = re.findall(r"[a-z']+", t.lower())
            if toks and len(set(toks)) / len(toks) < 0.6:
                continue  # "Whoa, whoa, whoa, whoa"
            s = hook_score_text(t, []) - 0.02 * k
            s += 0.2 if t.endswith(("?", "!")) else 0.0
            s += 0.4 if REACTION.search(t) else 0.0
            s += 0.2 if MONEY.search(t) or ACTION.search(t) else 0.0
            s += 0.15 if 3 <= n <= 6 else 0.0
            s -= 0.35 if n < 3 and not REACTION.search(t) else 0.0   # "Shall we?" says nothing on its own
            out.append((s, t))
    out.sort(key=lambda x: -x[0])
    seen, uniq = set(), []
    for s, t in out:
        key = t.lower().rstrip("?!.")
        if key not in seen:
            seen.add(key)
            uniq.append((s, t))
    return uniq


def _mood(text: str) -> str:
    if MONEY.search(text):
        return "money"
    if REACTION.search(text):
        return "reaction"
    if ACTION.search(text):
        return "action"
    if "?" in text:
        return "question"
    return "default"


def _caps(t: str) -> str:
    t = t.upper()
    if not t.endswith(("?", "!", "...")):
        t += "!" if len(t.split()) <= 4 else ""
    return t


def suggestions(clip: dict, sentences: list[dict], video_title: str = "") -> list[str]:
    """Several title options, best first."""
    game = game_name(video_title)
    quotes = _quotes(sentences, clip)
    said = " ".join(t for _, t in quotes[:6]) or clip.get("title", "")
    mood = _mood(said)
    seed = int(hashlib.md5(f"{clip.get('start')}{clip.get('title')}".encode()).hexdigest(), 16)
    curious = CURIOUS[mood][seed % len(CURIOUS[mood]):] + CURIOUS[mood][:seed % len(CURIOUS[mood])]
    lead = f"{game}: {curious[0]}" if game else curious[0]
    q1 = f'"{_caps(quotes[0][1])}"' if quotes else ""
    q2 = f'"{_caps(quotes[1][1])}"' if len(quotes) > 1 else ""
    strong = bool(quotes) and quotes[0][0] >= 0.2   # a punchy line said in the clip beats a generic hook
    opts = [q1, lead, q2] if strong else [lead, q1, q2]
    opts += [curious[0] if game else "", f'{game} - {q1}' if game and q1 else "", *curious[1:3]]
    seen, out = set(), []
    for o in opts:
        if o and o not in seen:
            seen.add(o)
            out.append(o)
    return out[:6]


def wrap(text: str, max_chars: int = 18, max_lines: int = 3) -> str:
    """Break a title into short lines (burned-in text doesn't wrap by itself)."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + [" ".join(lines[max_lines - 1:])]
    return "\n".join(lines)


def placement(clip: dict, aspect: str, lines: int = 2, size: int = 92) -> dict:
    """Where the title goes so it doesn't cover the facecam, captions or logos."""
    fit = clip.get("fit") or "auto"
    if aspect == "vertical":
        if fit == "stack":
            # Just below the seam (cam on top) / just above it (cam below): never over the streamer's face.
            share = float(clip.get("cam_share") or 0.33)
            half = lines * size * 1.2 / 2 / 1920 + 0.008
            pos = clip.get("cam_pos") or "top"
            return {"x": 0.5, "y": round(share + half if pos == "top" else 1 - share - half, 3)}
        if fit in ("blur", "black"):
            return {"x": 0.5, "y": 0.2}   # in the empty band above the 16:9 picture
        return {"x": 0.5, "y": 0.16}
    if aspect == "portrait":
        return {"x": 0.5, "y": 0.13}
    return {"x": 0.5, "y": 0.1}


STYLE = {"font": "Impact", "size": 92, "color": "#FFD400", "outline": 7, "outline_color": "#000000", "shadow": True,
         "bold": False, "box": False, "anim": "pop"}


def title_box(text: str, clip: dict, aspect: str) -> dict:
    """A whole-clip text box for the title."""
    size = STYLE["size"] if aspect == "vertical" else 72
    wrapped = wrap(text, 18 if aspect == "vertical" else 26)
    return {"id": "ttitle", "text": wrapped, **STYLE, "size": size,
            **placement(clip, aspect, wrapped.count("\n") + 1, size), "start": 0.0, "end": None}


def add_title(clip: dict, sentences: list[dict], video_title: str, aspect: str, used: set | None = None) -> dict:
    """The clip with a catchy whole-clip title (replaces an earlier auto title, keeps your other text).
    `used` = titles other clips of the run already have, so two clips don't get the same one."""
    opts = suggestions(clip, sentences, video_title)
    if not opts:
        return clip
    pick = next((o for o in opts if not used or o not in used), opts[0])
    if used is not None:
        used.add(pick)
    texts = [t for t in (clip.get("texts") or []) if t.get("id") != "ttitle"]
    return {**clip, "texts": [title_box(pick, clip, aspect)] + texts}
