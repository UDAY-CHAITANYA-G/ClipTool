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
| **Trim** | Drag the handles on the timeline, nudge ±0.5 s, "Set here", or snap to a sentence edge. Speed 0.5×–2× (captions and text stay in sync) - also as the **− [speed] +** control next to Play, and as **Speed** on the main form for new runs (`--speed 1.25`). |
| **Speed zones** (timeline) | The timeline shows the audio **waveform**. Under it, a speed lane: drag to mark a part, then pick 0.25×–4× - orange zones speed up (skip boring bits), blue zones slow down (slow-mo for big moments). Drag zones to move, edges to resize. **Auto: speed up silent parts** finds dead air (no speech, quiet audio) and makes it 2×. The preview plays each zone at its speed; captions, text, logos and the hook follow the new timing. |
| **Hook intro** (in Trim) | Start the clip with its most gripping 3-5 s, then play it from the beginning. **Find best hook** scores every word-aligned 3-5 s stretch inside the clip on replay heatmap, loudness / reactions, strong words and whether it opens and closes a thought; adjust start/end at the playhead and preview hook + clip. The main form's **Hook intro** box does this for every new clip (`--hook-intro` on the command line). |
| **Text** | Add Title / Label / Call-to-action / Watermark boxes, as many as you like. Each has its own font, size, colour, bold/italic, outline, shadow, background box and animation (fade, pop). Drag it on the preview; set its timing or drag its bar on the timeline. |
| **Captions** | Standard: Bold highlight, Word box, One word pop, Neon glow, Minimal, Classic subtitle. **GTA 6 themes**: Vice City, Wasted, Mission Passed, Leonida Neon, Heist Cash, Wanted, Phone Text, Ocean Drive. **Customize text**: font, size, bold, italic, UPPERCASE, letter spacing, colours, spoken-word effect (colour / box / pop / glow / none), border width + colour (0 = no border), shadow size + colour + opacity, background band; one-click "No border", "No shadow", "Clean text" and "Reset to defaults". Position, words per line, fix the caption text. **Sync captions** (−0.5 / −0.1 / +0.1 / +0.5 s) shifts every caption if they show early or late. Fixed caption text covers only the part it was written for, so making the clip longer adds transcript captions on the new part automatically. |
| **Logo** | Up to 3 images per clip (watermarks, logos). Add your default watermark or any uploaded image, pick one of 9 locations or drag it on the preview, set size, opacity and when it shows (whole clip by default); "Make this the default watermark". |
| **Filters** | Brightness, contrast, saturation, warmth, sharpen, vignette; presets Vivid, Cinematic, Warm, Cool, Black & white, Vice sunset. |
| **Audio** | Volume 0–200%, mute, fade in/out (picture and sound). |
| **Extras** | Hook text, progress bar (colour, top/bottom), cover image from the current frame, export quality. |

Undo/redo (Ctrl+Z / Ctrl+Shift+Z), Space play/pause, ←/→ step, Del removes the selected text, Ctrl+S saves.
**Save & render** rebuilds only that clip (about 30–60 s). The template and text style for new runs are set on the main form.

**Settings (⚙ top right):**
- **Logos** - a **Channel logo** (default spot: centred just below the captions) and a **Title logo** (default: top
  centre, e.g. a game title). Upload PNG / JPG / WebP, choose location, size, opacity and **Add to new clips**: every
  new video gets them automatically, in place. **Remove background** turns a logo with a fake checkerboard, solid or
  black background into a clean transparent one (it also trims it and drops stray marks). Per clip, the editor's **Logo** tool moves, resizes or
  removes them (up to 3 images per clip).
- **Description** - the upload description every clip gets: credit + links to the original channel and video, the exact
  moment, the streamer's @handle, and a professional copyright note asking owners to contact you first. Set your
  channel name, contact and hashtags, edit the template (placeholders like `{channel}`, `{timestamp_url}`), see a live
  preview. Copy it per clip from the clip list, or edit it per clip in the editor (Extras).
- **Storage folders** - shows where finished clips and downloaded videos are stored (with space used), lets you
  pick other folders with a folder browser, and opens them in Explorer. Existing files are not moved.

**Full video – make your own clips:** open a run and press **▶ Full video – make clips**. You get the whole
source video, an overview bar (audio waveform, replay heatmap, the clips already made - orange = picked by
ClipTool, purple = picked by you) and the full searchable transcript. Set the start/end by dragging on the bar,
pressing **I** / **O** at the playhead, or the **Start** / **End** buttons on a transcript line (snaps to sentence
edges unless you untick it); **P** plays the selection. Name it (a title is suggested) and press **Create clip** (3 s–3 min) -
it renders with the run's format, captions and default logos and appears in the clip list tagged **Picked by you**.
Delete any single clip with the bin icon on its card.

**Caption timing:** YouTube auto-captions without word timings are now spread over each line's real span (lines
no longer run into the next one). Existing runs are re-read automatically; press **Save & render** (or re-run)
on a clip made before this fix to get the corrected captions.

**Grid / List:** the switch above the clips shows them as cards or as one row per clip (remembered).

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
