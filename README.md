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

**Deleting:** hover a previous run and press **×**, or open it and press **Delete this run** (removes its clips and
its downloaded video). **Clear all** removes every run and every downloaded video. Both ask first and can't be undone.

## Command line

```powershell
cliptool "https://www.youtube.com/watch?v=VIDEO_ID"                  # 5 vertical clips, captions
cliptool "https://youtu.be/VIDEO_ID" --count 8 --min 30 --max 90
cliptool "https://youtu.be/VIDEO_ID" --fit blur                      # always show the full frame
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
5. **Auto framing** finds the speaker's face (OpenCV) and crops on it; if faces jump around or there are none
   (slides, wide shots), it uses the full frame over a blurred background so nothing is cut off.

Weights and caption style live in `config.toml`.

Optional: with an Anthropic API key and `pip install -e ".[ai]"`, `--ai` adds a Claude pass that re-ranks and
titles clips. It is off by default and never required.

Only clip videos you own or have permission to use.

## Tests

```powershell
pip install -e ".[dev]"
pytest
```
