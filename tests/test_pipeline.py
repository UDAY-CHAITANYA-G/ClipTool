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


def test_gta_theme_presets():
    from cliptool.render import PRESETS, build_ass, caption_style

    gta = [k for k, p in PRESETS.items() if p.get("group") == "gta"]
    assert len(gta) >= 5 and len({PRESETS[k]["mode"] for k in gta}) >= 4  # several different formats
    words = [{"w": "wasted", "s": 0.0, "e": 0.6}, {"w": "again", "s": 0.6, "e": 1.2}]
    for k in gta:
        ass = build_ass(words, 0, 2, (1080, 1920), caption_style({}, {"preset": k}), "vertical")
        assert "Dialogue:" in ass, k
    wasted = build_ass(words, 0, 2, (1080, 1920), caption_style({}, {"preset": "wasted"}), "vertical")
    assert ",5,70,70,0," in wasted and ",100,100,3,0," in wasted  # centred, letter-spaced
    phone = build_ass(words, 0, 2, (1080, 1920), caption_style({}, {"preset": "phone"}), "vertical")
    assert ",&H404B1B1E," in phone and ",8,70,70,300," in phone  # indigo band, top


def test_custom_caption_fields():
    from cliptool.pipeline import clean_changes
    from cliptool.render import build_ass, caption_style

    words = [{"w": "now", "s": 0.0, "e": 0.5}, {"w": "where", "s": 0.5, "e": 1.0}]
    style_line = lambda o: [l for l in build_ass(words, 0, 2, (1080, 1920), caption_style({}, o), "vertical").splitlines()
                            if l.startswith("Style: Cap,")][0].split(",")
    # Style fields: 7 Bold, 8 Italic, 13 Spacing, 15 BorderStyle, 16 Outline, 17 Shadow
    clean = style_line({"preset": "vice", "outline": 0, "shadow": 0})
    assert clean[16] == "0" and clean[17] == "0"
    custom = style_line({"preset": "highlight", "bold": False, "italic": True, "spacing": 8, "font": "Georgia"})
    assert custom[1] == "Georgia" and custom[7] == "0" and custom[8] == "-1" and custom[13] == "8"
    band = style_line({"preset": "vice", "background": True, "band_color": "#112233", "band_opacity": 1.0})
    assert band[15] == "3" and band[6] == "&H00332211"
    ok = clean_changes({"style": {"preset": "vice", "outline": 99, "shadow": -3, "mode": "plain", "background": 1}}, 100)
    assert ok["style"]["outline"] == 12 and ok["style"]["shadow"] == 0 and ok["style"]["background"] is True


def test_watermark_images(tmp_path, monkeypatch):
    import cv2
    import numpy as np
    import pytest
    from cliptool import assets
    from cliptool.render import _video_filter

    monkeypatch.setattr(assets, "WATERMARKS", tmp_path / "wm")
    monkeypatch.setattr(assets, "SETTINGS", tmp_path / "settings.json")
    monkeypatch.setattr(assets, "ASSETS", tmp_path)
    ok, png = cv2.imencode(".png", np.zeros((40, 80, 4), np.uint8))
    info = assets.save_upload(png.tobytes())
    assert info["name"].endswith(".png") and info["w"] == 80 and assets.path_of(info["name"])
    for bad in (b"", b"GIF89a....", b"\x89PNG\r\n\x1a\nbroken"):
        with pytest.raises(ValueError):
            assets.save_upload(bad)
    assert assets.path_of("../settings.json") is None
    layer = assets.clean_layer({"file": info["name"], "x": 2, "size": 5, "opacity": 0})
    assert layer["x"] == 1.0 and layer["size"] == 0.9 and layer["opacity"] == 0.05 and layer["end"] is None
    # default watermark only applies when "add to new clips" is on
    assert assets.default_layer_for_clip() is None
    assets.save_settings({"watermark": {**assets.DEFAULT_LAYER, "file": info["name"], "auto": True}})
    assert assets.default_layer_for_clip()["file"] == info["name"]
    g = _video_filter("vertical", "blur", True, None, None, {"images": [layer, layer]}, out_dur=10.0, subs=True)
    assert "[1:v]format=rgba" in g and "[2:v]format=rgba" in g and "enable='between(t,0.00,10.00)'" in g
    assert g.index("overlay=x='1.0000*W-w/2'") < g.index("ass=subs.ass")  # images under captions


def test_remove_background_fake_checkerboard():
    import numpy as np
    from cliptool.assets import remove_background

    # 400x400: grey checkerboard, a white ring "logo" in the middle, a cream band at the bottom with a speck.
    img = np.zeros((400, 400, 3), np.uint8)
    yy, xx = np.mgrid[:400, :400]
    img[:] = np.where(((yy // 20 + xx // 20) % 2 == 0)[..., None], 120, 165)
    ring = (np.hypot(yy - 170, xx - 200) < 80) & (np.hypot(yy - 170, xx - 200) > 55)
    img[ring] = 240
    img[330:] = (232, 240, 245)          # cream band (BGR)
    img[370:380, 370:380] = 255          # sparkle in the band
    out = remove_background(img)
    assert out.shape[2] == 4
    h, w = out.shape[:2]
    assert 150 <= w <= 200 and 150 <= h <= 200  # trimmed to the ring (+ small padding)
    a = out[..., 3]
    assert a[h // 2, w // 2] < 10            # checker inside the ring is transparent
    assert a.max() == 255 and (a > 200).mean() > 0.15


def test_storage_folders(tmp_path, monkeypatch):
    import pytest
    from cliptool import assets

    monkeypatch.setattr(assets, "SETTINGS", tmp_path / "settings.json")
    monkeypatch.setattr(assets, "ASSETS", tmp_path)
    assert assets.folder("output").name == "output"
    s = assets.set_folders({"output": str(tmp_path / "clips"), "work": str(tmp_path / "dl")})
    assert (tmp_path / "clips").is_dir() and assets.folder("work") == (tmp_path / "dl").resolve()
    with pytest.raises(ValueError):
        assets.set_folders({"output": "relative/path"})
    with pytest.raises(ValueError):
        assets.set_folders({"output": str(tmp_path / "same"), "work": str(tmp_path / "same")})
    assets.set_folders({"output": ""})
    assert assets.folder("output").name == "output"
    listing = assets.browse(str(tmp_path))
    assert any(d["name"] == "clips" for d in listing["dirs"])


def test_find_hook_picks_loud_strong_moment(monkeypatch):
    import numpy as np
    from cliptool import ffmpeg, signals

    text = ("so we went there and then we drove around for a while and it was fine okay. "
            "Why does nobody tell you this secret? anyway we kept going home after that").split()
    words, t = [], 0.0
    for w in text:
        words.append({"w": w, "s": round(t, 2), "e": round(t + 0.35, 2)})
        t += 0.4
    q0 = next(w["s"] for w in words if w["w"] == "Why")
    q1 = next(w["e"] for w in words if w["w"] == "secret?")
    # Loud exactly while the question is spoken.
    rate = 8000
    audio = np.full(int(rate * (t + 1)), 0.01, np.float32)
    audio[int(rate * q0): int(rate * q1)] = 0.6
    monkeypatch.setattr(ffmpeg, "read_audio_range", lambda *a, **k: audio)
    sentences = [{"start": 0, "end": t, "text": " ".join(text), "words": words}]
    h = signals.find_hook({"start": 0.0, "end": t}, sentences, {"heatmap": []}, None)
    assert h and 3.0 <= h["end"] - h["start"] <= 5.5
    assert h["text"].startswith("Why") and "secret?" in h["text"]  # opens the thought, ends on the payoff
    assert h["start"] >= 3.0 - 0.1  # never from the clip's first seconds


def test_description_template():
    from cliptool import description

    info = {"id": "abcdefghijk", "title": "Big Stream", "channel": "Streamer TV",
            "webpage_url": "https://www.youtube.com/watch?v=abcdefghijk", "channel_url": None, "channel_handle": "@streamer"}
    clip = {"title": "Wild moment", "start": 125.4, "end": 160.0}
    text = description.for_clip(info, clip, {**description.DEFAULTS, "my_channel": "ClipStories", "contact": "me@example.com"})
    assert text.startswith("Wild moment")
    assert "Streamer TV" in text and "https://youtu.be/abcdefghijk?t=125" in text and "(2:05–2:40)" in text
    assert "me@example.com" in text and "@streamer" in text and "#Shorts" in text
    assert "{channel_url}" not in text and "Subscribe to" not in text  # line with an empty link is dropped
    # an edited description wins over the template
    assert description.for_clip(info, {**clip, "description": "Mine"}, description.DEFAULTS) == "Mine"
    # missing contact: the copyright line is dropped rather than left half-empty
    assert "contact me first at" not in description.for_clip(info, clip, description.DEFAULTS)


def test_speed_zones_timeline_and_validation():
    import pytest
    from cliptool.pipeline import clean_changes
    from cliptool.render import Timeline, atempo_chain, speed_pieces

    ps = speed_pieces(0, 20, 1.0, [{"start": 5, "end": 9, "speed": 2}, {"start": 12, "end": 14, "speed": 0.5}])
    assert ps == [(0, 5, 1.0), (5, 9, 2.0), (9, 12, 1.0), (12, 14, 0.5), (14, 20, 1.0)]
    tl = Timeline([("hook", 30, 33, 1.0)] + [("main", a, b, s) for a, b, s in ps])
    assert tl.duration == 3 + 5 + 2 + 3 + 4 + 6
    assert tl.out_time("main", 7) == 3 + 5 + 1     # halfway through the 2x zone
    assert tl.out_time("main", 13) == 3 + 5 + 2 + 3 + 2  # halfway through the slow-mo zone
    assert tl.out_time("hook", 31.5) == 1.5
    assert atempo_chain(4) == "atempo=2.0000,atempo=2.0000" and atempo_chain(0.25).count("atempo") == 2
    ok = clean_changes({"speed_zones": [{"start": 8, "end": 12, "speed": 3}, {"start": 5, "end": 9, "speed": 2},
                                        {"start": 1, "end": 1.1, "speed": 2}]}, 100)
    assert ok["speed_zones"] == [{"start": 5.0, "end": 9.0, "speed": 2.0}, {"start": 9.0, "end": 12.0, "speed": 3.0}]
    with pytest.raises(ValueError):
        clean_changes({"speed_zones": [{"start": 1, "end": 5, "speed": 8}]}, 100)


def test_caption_words_range_offset_and_json3_timing(tmp_path):
    import json
    from cliptool.render import clip_caption_words
    from cliptool.transcribe import words_from_json3

    words = [{"w": f"w{i}", "s": float(i), "e": i + 0.8} for i in range(20)]
    sentences = [{"start": 0, "end": 20, "text": "x", "words": words}]
    # edited text was written for 2-6 s; the clip was later lengthened to 0-10 s
    clip = {"start": 0.0, "end": 10.0, "caption_text": "AA BB CC DD", "caption_range": [2.0, 6.0], "caption_offset": 0.5}
    got = clip_caption_words(clip, sentences)
    texts = [w["w"] for w in got]
    assert texts == ["w0", "w1", "AA", "BB", "CC", "DD", "w6", "w7", "w8", "w9"]
    assert got[0]["s"] == 0.5 and got[-1]["s"] == 9.5  # +0.5 s sync offset
    # json3 line without word timing: words end where the next line starts, not at the overlapping duration
    cap = {"events": [{"tStartMs": 1000, "dDurationMs": 8000, "segs": [{"utf8": "Okay, rolling."}]},
                      {"tStartMs": 3000, "dDurationMs": 4000, "segs": [{"utf8": "next"}, {"utf8": " line", "tOffsetMs": 300}]}]}
    f = tmp_path / "c.json3"
    f.write_text(json.dumps(cap), encoding="utf-8")
    ws = words_from_json3(f)
    assert ws[1]["w"] == "rolling." and ws[1]["e"] <= 3.0
