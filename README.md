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

**Editing a clip:** press **Edit** on any clip. A full-screen workspace opens with a live preview, a timeline,
and these tools on the left:

| Tool | What you can do |
| --- | --- |
| **Layout** | Ratio **9:16 / 4:5 / 1:1 / 16:9**. Templates: **Auto** (follows the action), **Crop to fill** (zoom, drag the visible area), **Full + blur** / **Full + black** (whole 16:9 frame inside, with zoom), **Facecam + game**. |
| **Facecam + game** | Split presets (Cam 1/4, 1/3, 44%, half, 60%) or a custom height. Put the cam on top or bottom. The facecam is auto-detected; drag or resize its box. Drag and zoom the game area. |
| **Trim** | Drag the handles on the timeline, nudge ±0.5 s, "Set here", or snap to a sentence edge. Speed 0.5×–2× (captions and text stay in sync). |
| **Text** | Add Title / Label / Call-to-action / Watermark boxes, as many as you like. Each has its own font, size, colour, bold/italic, outline, shadow, background box and animation (fade, pop). Drag it on the preview; set its timing or drag its bar on the timeline. |
| **Captions** | 7 styles: Bold highlight, Word box, One word pop, Neon glow, Minimal, Classic subtitle, **Vice City (GTA)**. Position, size, colours, words per line, UPPERCASE, fix the caption text. |
| **Filters** | Brightness, contrast, saturation, warmth, sharpen, vignette; presets Vivid, Cinematic, Warm, Cool, Black & white, Vice sunset. |
| **Audio** | Volume 0–200%, mute, fade in/out (picture and sound). |
| **Extras** | Hook text, progress bar (colour, top/bottom), cover image from the current frame, export quality. |

Undo/redo (Ctrl+Z / Ctrl+Shift+Z), Space play/pause, ←/→ step, Del removes the selected text, Ctrl+S saves.
**Save & render** rebuilds only that clip (about 30–60 s). The template and text style for new runs are set on the main form.

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
