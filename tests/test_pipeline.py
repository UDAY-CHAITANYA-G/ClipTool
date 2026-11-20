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


def test_best_window_follows_interest():
    import numpy as np
    from cliptool.framing import best_windows, smooth_path

    prof = np.zeros((8, 100), dtype=np.float32)
    for k in range(8):
        prof[k, 10 + 8 * k: 20 + 8 * k] = 1.0  # a blob moving right
    centres, cov, info = best_windows(prof, 30)
    assert info.all() and (cov > 0.99).all()
    path = smooth_path(centres, info, 30, 100)
    assert path[-1] > path[0] and path.min() >= 15 and path.max() <= 85


def test_pan_commands_format(tmp_path, monkeypatch):
    from cliptool import framing

    monkeypatch.setattr(framing, "video_size", lambda p: (1920, 1080))
    cmd = framing.pan_commands(tmp_path / "x.mp4", [(0.0, 0.2), (2.0, 0.8)], 2.0)
    first, last = cmd.splitlines()[0], cmd.splitlines()[-1]
    assert first.startswith("0.000 crop@pan x ") and first.endswith(";")
    assert int(last.split()[-1].rstrip(";")) > int(first.split()[-1].rstrip(";"))


def test_caption_presets_render_and_edit_alignment():
    from cliptool.render import PRESETS, align_text, caption_style

    words = [{"w": "hello", "s": 1.0, "e": 1.4}, {"w": "world", "s": 1.5, "e": 2.0}]
    for name in PRESETS:
        st = caption_style(DEFAULTS["captions"], {"preset": name, "position": "top"})
        ass = build_ass(words, 0.5, 3.0, (1080, 1920), st, "vertical")
        assert "Dialogue:" in ass and ",8,70,70,300," in ass  # top alignment + margin
    same = align_text(words, "Hello World!")
    assert [w["w"] for w in same] == ["Hello", "World!"] and same[1]["s"] == 1.5
    more = align_text(words, "hello there big world")
    assert len(more) == 4 and more[0]["s"] == 1.0 and abs(more[-1]["e"] - 2.0) < 1e-6


def test_clean_changes_validates():
    import pytest
    from cliptool.pipeline import clean_changes

    ok = clean_changes({"start": 10, "end": 30, "title": " T ", "style": {"preset": "neon", "scale": 9}}, 100)
    assert ok["title"] == "T" and ok["style"]["scale"] == 1.8
    for bad in ({"start": 10, "end": 10.5}, {"style": {"preset": "nope"}}, {"title": "  "},
                {"style": {"preset": "box", "color": "red"}}, {"start": 90, "end": 120}):
        with pytest.raises(ValueError):
            clean_changes(bad, 100)


def test_window_respects_aspect_zoom_and_bounds():
    from cliptool.render import window

    w, h, x, y = window(1920, 1080, 9 / 16, 1.0, 0.5, 0.5)
    assert h == 1080 and abs(w / h - 9 / 16) < 0.01 and x == (1920 - w) // 2
    w2, h2, x2, y2 = window(1920, 1080, 9 / 16, 2.0, 1.0, 0.0)  # zoomed, pushed to the top-right corner
    assert h2 == 540 and x2 + w2 <= 1920 and y2 == 0 and x2 == 1920 - w2
    w3, h3, _, _ = window(1920, 1080, 3.0, 1.0, 0.5, 0.5)  # wider than the source: full width
    assert w3 == 1920 and h3 == 640


def test_text_overlays_and_speed_in_ass():
    from cliptool.render import build_ass, caption_style

    words = [{"w": "hello", "s": 10.0, "e": 10.4}, {"w": "world", "s": 11.0, "e": 11.5}]
    texts = [{"text": "TITLE", "x": 0.5, "y": 0.1, "size": 90, "color": "#FFD400", "box": True,
              "box_color": "#000000", "start": 0, "end": 2, "anim": "pop"}]
    ass = build_ass(words, 10.0, 14.0, (1080, 1920), caption_style({}, None), "vertical", speed=2.0, texts=texts)
    assert r"\pos(540,192)" in ass and ",TxtBox," in ass and "TITLE" in ass
    assert "Dialogue: 0,0:00:00.50," in ass  # 'world' at source 11.0 -> 0.5 s at 2x speed
    no_caps = build_ass(words, 10.0, 14.0, (1080, 1350), caption_style({}, None), "portrait", texts=texts, captions=False)
    assert ",Cap,," not in no_caps and "TITLE" in no_caps


def test_video_filter_extras():
    from cliptool.render import _video_filter

    clip = {"speed": 1.5, "fade_in": 0.5, "fade_out": 1.0, "progress_bar": {"on": True, "color": "#FF0000"},
            "filters": {"saturation": 0}}
    g = _video_filter("vertical", "blur", True, None, None, clip, out_dur=10.0, subs=True)
    for part in ("setpts=PTS/1.5000", "fade=t=in:st=0:d=0.50", "fade=t=out:st=9.00:d=1.00",
                 "color=c=0xFF0000:s=1080x14", "ass=subs.ass[v]", "saturation=0.000"):
        assert part in g, part


def test_clean_texts_and_new_fields():
    import pytest
    from cliptool.pipeline import clean_changes

    ok = clean_changes({"texts": [{"text": "Hi", "font": "Nope", "size": 999, "start": 1, "end": 3}],
                        "speed": 5, "cam_share": 0.01, "cam_pos": "bottom", "zoom": None,
                        "progress_bar": {"on": True, "color": "#00FF00", "position": "top"}, "quality": "high"}, 100)
    t = ok["texts"][0]
    assert t["font"] == "Arial Black" and t["size"] == 300 and ok["speed"] == 2 and ok["cam_share"] == 0.15
    assert ok["zoom"] is None and ok["progress_bar"]["position"] == "top" and ok["quality"] == "high"
    for bad in ({"texts": [{"text": "x", "start": 3, "end": 1}]}, {"cam_pos": "left"}, {"quality": "ultra"},
                {"texts": [{"text": "x", "color": "pink"}]}, {"texts": "nope"}):
        with pytest.raises(ValueError):
            clean_changes(bad, 100)
