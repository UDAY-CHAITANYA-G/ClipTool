# ClipTool

Turn a YouTube link into a ranked list of ready-to-post short clips (9:16, word-timed captions).
Everything runs on your computer: **no API keys, no accounts, no AI service needed.**

Design doc: https://claude.ai/artifact/P95BeCV7RZMjqE6HFhL1Zq

## Setup (Windows PowerShell, once)

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
cliptool doctor
```

ffmpeg comes bundled (imageio-ffmpeg). Node.js on PATH lets yt-dlp solve YouTube's JS challenges.
Optional: `pip install -e ".[whisper]"` for videos that have no captions (local speech-to-text).

## Website (easiest)

```powershell
cliptool serve --open
```

Opens http://127.0.0.1:8765 — paste a link, choose clip count / length / format / framing, press
**Find clips**, watch progress, then play, download or copy titles. Previous runs are listed on the right.
The site is only reachable from this computer.

**Editing a clip:** press **Edit** on any clip to open the editor. It has a live 9:16 preview, where you can:
- trim the start and end (±0.5 s, "Set here" at the playhead, or "Snap" to a sentence edge);
- change the title and hook;
- fix the caption text;
- pick a text style: **Bold highlight**, **Word box**, **One word pop**, **Neon glow**, **Minimal** or
  **Classic subtitle**;
- set the caption position (top/middle/bottom), size, colours, words per line and UPPERCASE, or turn captions off;
- switch the format (9:16, 1:1, 16:9) and pick a **template**:
  - **Auto** follows the action;
  - **Crop to fill**, with a draggable visible area;
  - **Full 16:9 + blur** or **Full 16:9 + black** puts the whole frame inside 9:16;
  - **Facecam top + 1:1 game**: the facecam is auto-detected, and you can drag or resize its box and slide the game area;
- adjust **filters**: brightness, contrast, saturation, warmth, sharpen and vignette, or presets (Vivid,
  Cinematic, Warm, Cool, Black & white, Vice sunset);
- use the GTA-themed **Vice City** caption style (pink and orange, bold italic, purple outline).

**Save & re-render** rebuilds only that clip (about 30 s). The text style for new runs is set on the main form.

**Deleting:** hover a previous run and press **×**, or open it and press **Delete this run** (removes its clips and
its downloaded video). **Clear all** removes every run and every downloaded video. Both ask first and can't be undone.

## Command line

```powershell
cliptool "https://www.youtube.com/watch?v=VIDEO_ID"                  # 5 vertical clips, captions
cliptool "https://youtu.be/VIDEO_ID" --count 8 --min 30 --max 90
cliptool "https://youtu.be/VIDEO_ID" --fit stack --style vice         # facecam on top, game below, GTA captions
cliptool "https://youtu.be/VIDEO_ID" --dry-run                       # list clips, no rendering
cliptool render output\<folder> --aspect square                      # re-render a previous run
cliptool clean                                                       # delete downloaded videos (work\)
cliptool clean --all                                                 # also delete every clip (output\); asks first, -y skips
```

Results go to `output\<title>_<id>\`: `clips\*.mp4` + thumbnails, `clips.json`, `index.html`.

## How clips are picked (offline)

1. Sentence-aligned windows (default 20-60 s) from YouTube captions (or local Whisper).
2. Each window is scored on:
   - **Heatmap 0.35** — YouTube's "most replayed" curve;
   - **Audio 0.20** — loud moments, plus captioned (Laughter)/(Applause);
   - **Hooks 0.30** — opening is a question, number, strong claim or story cue; starting on "and/so/um" or a
     dangling "he/that" is penalised; filler words subtract;
   - **Pacing 0.15** — speech rate near the video's norm, clean sentence ending, no dead air.
3. Overlapping windows are removed; the best N are rendered.
4. Title = the clip's most quotable line; hook = its opening line; reason = which signals stood out.
5. **Auto framing** samples each clip at 4 fps and maps where the faces and motion are, then picks:
   - **Speaker crop** — one steady face: a fixed 9:16 crop centred on it;
   - **Follows the action** — the moving subject fits in a 9:16 window: a smooth virtual camera pans with it;
   - **Full frame** — the action is wider than 9:16 (gameplay, wide shots, a moving camera, slides): the whole
     frame over a blurred background, so no context is cut off.
   The website shows which one each clip used. `--fit crop` / `--fit blur` force a mode.

Weights and caption style live in `config.toml`.

Optional: with an Anthropic API key and `pip install -e ".[ai]"`, `--ai` adds a Claude pass that re-ranks and
titles clips. It is off by default and never required.

Only clip videos you own or have permission to use.

## Tests

```powershell
pip install -e ".[dev]"
pytest
```
