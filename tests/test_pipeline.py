from cliptool.candidates import make_candidates
from cliptool.fetch import video_id_from_url
from cliptool.rank import merge_claude, suppress
from cliptool.render import build_ass, slugify
from cliptool.config import DEFAULTS
from cliptool.signals import hook_score_text
from cliptool.transcribe import words_to_sentences


def _sentences(n=20, length=6.0):
    words = []
    for k in range(n):
        for w in range(6):
            t = k * length + w
            words.append({"w": f"word{w}" + ("." if w == 5 else ""), "s": t, "e": t + 0.9})
    return words_to_sentences(words)


def test_video_id():
    assert video_id_from_url("https://youtu.be/arj7oStGLkU?t=3") == "arj7oStGLkU"
    assert video_id_from_url("https://www.youtube.com/shorts/arj7oStGLkU") == "arj7oStGLkU"


def test_candidates_respect_lengths():
    sents = _sentences()
    cands = make_candidates(sents, 20, 40)
    assert cands and all(20 <= c["duration"] <= 40 for c in cands)


def test_suppress_no_big_overlap():
    sents = _sentences()
    cands = make_candidates(sents, 20, 40)
    for k, c in enumerate(cands):
        c["local"] = k % 7 / 7
        c["signals"] = {}
    kept = suppress(cands, 0.3, 10)
    for a in kept:
        for b in kept:
            if a is not b:
                inter = min(a["end"], b["end"]) - max(a["start"], b["start"])
                assert inter / min(a["duration"], b["duration"]) < 0.3


def test_merge_claude_snaps_to_sentences():
    sents = _sentences()
    cands = make_candidates(sents, 20, 40)
    for c in cands:
        c["local"], c["signals"] = 0.5, {}
    fin = suppress(cands, 0.3, 5)
    f = fin[0]
    picks = [{"candidate_id": f["id"], "start": f["start"] + 0.3, "end": f["end"] - 0.2,
              "title": "T", "hook": "H", "score": 8, "reason": "R"}]
    out = merge_claude(picks, fin, sents, 20, 40, 0.5, 3)
    assert out[0]["start"] == f["start"] and out[0]["end"] == f["end"]
    assert out[0]["score"] == 0.65


def test_hooks_prefer_questions():
    assert hook_score_text("Why does nobody tell you this?", []) > hook_score_text("and so um yeah", ["um"])


def test_ass_has_highlight():
    words = [{"w": "hello", "s": 1.0, "e": 1.4}, {"w": "world", "s": 1.5, "e": 2.0}]
    ass = build_ass(words, 0.5, 3.0, (1080, 1920), DEFAULTS["captions"], "vertical")
    assert ass.count("Dialogue:") == 2 and "HELLO" in ass


def test_slugify():
    assert slugify("What's the Deal?! 100%") == "what-s-the-deal-100"
