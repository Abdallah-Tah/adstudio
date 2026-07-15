"""Stage 8a: voiceover via ElevenLabs — ONE call for the full script, then
per-scene timing spans derived from the API's character timestamps.

Endpoint verified against elevenlabs.io/docs (2026-07-15):
POST /v1/text-to-speech/{voice_id}/with-timestamps, header xi-api-key,
response {audio_base64, alignment: {characters, character_start_times_seconds,
character_end_times_seconds}}.

vo_lines are guaranteed contiguous slices of the script (storyboard validator),
so spans are found with the same normalized-cursor match — no forced alignment
dependency needed.
"""
import base64
import math
import os
import re

import httpx

from app.pricing import ELEVENLABS_USD_PER_1K_CHARS
from app.schema import Scene

ELEVEN_BASE = "https://api.elevenlabs.io"
TTS_MODEL = "eleven_multilingual_v2"
DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"   # ElevenLabs premade voice
OUTPUT_FORMAT = "mp3_44100_128"


def voice_cost_cents(characters: int) -> int:
    """Estimated from the subscription's effective $/1k chars (pricing.py) —
    ElevenLabs bills credits, not per-call dollars. Ceil, never undercount."""
    return math.ceil(characters * ELEVENLABS_USD_PER_1K_CHARS / 1000 * 100)


def synthesize(script: str) -> tuple[bytes, dict, int]:
    """One TTS call for the whole script.
    Returns (mp3 bytes, alignment dict, cost_cents)."""
    key = os.environ.get("ELEVENLABS_API_KEY", "")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY is not configured")
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID", DEFAULT_VOICE_ID)
    r = httpx.post(
        f"{ELEVEN_BASE}/v1/text-to-speech/{voice_id}/with-timestamps",
        params={"output_format": OUTPUT_FORMAT},
        headers={"xi-api-key": key},
        json={"text": script, "model_id": TTS_MODEL},
        timeout=120.0,
    )
    r.raise_for_status()
    doc = r.json()
    audio = base64.b64decode(doc["audio_base64"])
    return audio, doc["alignment"], voice_cost_cents(len(script))


def _norm_map(text: str) -> tuple[str, list[int]]:
    """Whitespace-normalized text + map from normalized index -> original index.
    Mirrors the storyboard validator's normalization."""
    norm_chars: list[str] = []
    idx_map: list[int] = []
    in_ws = True  # strips leading whitespace
    for i, ch in enumerate(text):
        if ch.isspace():
            if not in_ws:
                norm_chars.append(" ")
                idx_map.append(i)
                in_ws = True
        else:
            norm_chars.append(ch)
            idx_map.append(i)
            in_ws = False
    if norm_chars and norm_chars[-1] == " ":
        norm_chars.pop()
        idx_map.pop()
    return "".join(norm_chars), idx_map


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _locate(alignment: dict, scenes: list[Scene]) -> dict[str, tuple[int, int]]:
    """Char-index range (first, last inclusive) of each scene's vo_line in the
    full VO text, via the same normalized-cursor match as the validator."""
    joined = "".join(alignment["characters"])
    norm_text, idx_map = _norm_map(joined)
    ranges: dict[str, tuple[int, int]] = {}
    cursor = 0
    for scene in scenes:
        if not scene.vo_line:
            continue
        line = _norm(scene.vo_line)
        pos = norm_text.find(line, cursor)
        if pos == -1:
            raise ValueError(
                f"vo_line for scene {scene.scene_id} not found in VO alignment: "
                f"{scene.vo_line!r}")
        ranges[scene.scene_id] = (idx_map[pos], idx_map[pos + len(line) - 1])
        cursor = pos + len(line)
    return ranges


def scene_spans(alignment: dict, scenes: list[Scene]) -> dict[str, tuple[float, float]]:
    """Map each scene's vo_line to (start_s, end_s) in the full VO track."""
    starts = alignment["character_start_times_seconds"]
    ends = alignment["character_end_times_seconds"]
    return {
        sid: (float(starts[first]), float(ends[last]))
        for sid, (first, last) in _locate(alignment, scenes).items()
    }


def scene_words(alignment: dict, scenes: list[Scene]) -> dict[str, list[tuple[str, float, float]]]:
    """Per-scene word timings [(word, start_s, end_s), ...] for captions."""
    chars = alignment["characters"]
    starts = alignment["character_start_times_seconds"]
    ends = alignment["character_end_times_seconds"]
    out: dict[str, list[tuple[str, float, float]]] = {}
    for sid, (first, last) in _locate(alignment, scenes).items():
        words: list[tuple[str, float, float]] = []
        w_start: int | None = None
        for i in range(first, last + 2):
            is_ws = i > last or chars[i].isspace()
            if is_ws:
                if w_start is not None:
                    word = "".join(chars[w_start:i])
                    words.append((word, float(starts[w_start]), float(ends[i - 1])))
                    w_start = None
            elif w_start is None:
                w_start = i
        out[sid] = words
    return out
