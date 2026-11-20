"""Claude picks, refines and titles the final clips from the finalists."""
from __future__ import annotations

import json
import os
import shutil
import subprocess

SYSTEM = """You are an expert short-form video editor. You pick moments from long videos that work as
stand-alone YouTube Shorts / Reels / TikToks.

A great clip:
- hooks the viewer in its first 3 seconds (a surprising claim, a question, a strong opinion, a story opening, a number);
- makes sense with no context from the rest of the video (no dangling "he", "that thing", "as I said");
- ends on a payoff: an answer, punchline, conclusion or strong line - never mid-thought;
- has one clear idea.

You will get candidate windows with sentence-level timestamps. For each clip you choose you may tighten its
start and end, but only to sentence timestamps shown inside that candidate (start = a sentence's start time,
end = a later sentence's end time) and the result must stay within the requested length range.
Write titles as a viewer would see them on a Short: punchy, specific, at most 60 characters, no clickbait lies,
no hashtags, no emoji. The hook is the on-screen text for the first seconds (at most 80 characters).
Score each pick 1-10 for how well it will hold attention as a short. Chosen clips must not overlap."""

SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "integer"},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "score": {"type": "integer"},
                    "reason": {"type": "string"},
                },
                "required": ["candidate_id", "start", "end", "title", "hook", "score", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}


def has_credentials() -> bool:
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    if shutil.which("ant"):
        try:
            r = subprocess.run(["ant", "auth", "status"], capture_output=True, text=True, timeout=10)
            return r.returncode == 0 and "no active" not in (r.stdout + r.stderr).lower()
        except Exception:
            return False
    return False


def build_prompt(info: dict, finalists: list[dict], sentences: list[dict], count: int, min_s: float, max_s: float) -> str:
    parts = [
        f"Video title: {info.get('title')}",
        f"Channel: {info.get('channel')}",
        f"Choose the best {count} clips (fewer only if there are not {count} good ones). "
        f"Each clip must be {min_s:.0f}-{max_s:.0f} seconds long.",
        "",
        "Candidates:",
    ]
    for f in finalists:
        parts.append(f"\n<candidate id=\"{f['id']}\" start=\"{f['start']:.2f}\" end=\"{f['end']:.2f}\" local_score=\"{f['local']:.2f}\">")
        for s in sentences[f["i"]: f["j"] + 1]:
            parts.append(f"[{s['start']:.2f}-{s['end']:.2f}] {s['text']}")
        parts.append("</candidate>")
    return "\n".join(parts)


def rank_with_claude(info, finalists, sentences, count, min_s, max_s, model: str, effort: str) -> list[dict]:
    import anthropic

    client = anthropic.Anthropic()
    prompt = build_prompt(info, finalists, sentences, count, min_s, max_s)
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.AuthenticationError as e:
        raise RuntimeError("Claude rejected the API key. Set ANTHROPIC_API_KEY or run with --no-ai.") from e
    except anthropic.RateLimitError as e:
        raise RuntimeError("Claude rate limit hit; try again shortly or run with --no-ai.") from e
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"Claude API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise RuntimeError("Could not reach the Claude API (network).") from e

    if response.stop_reason == "refusal":
        raise RuntimeError("Claude declined to rank this video's clips.")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude's answer was cut off (max_tokens).")
    text = next((b.text for b in response.content if b.type == "text"), "")
    data = json.loads(text)
    usage = response.usage
    print(f"  Claude ({response.model}): {usage.input_tokens} in / {usage.output_tokens} out tokens")
    return data["clips"]
