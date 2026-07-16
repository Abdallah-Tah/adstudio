"""Versioned provider pricing configuration.

Provider prices live HERE, not scattered through code, so a price change is
one reviewed edit with a version bump. All figures verified against the
provider's own docs on PRICING_VERSION date (hard rule: never from memory).

Sources:
- developers.openai.com/api/docs/pricing            (tokens + images)
- fal.ai/models/fal-ai/kling-video/v3/standard/image-to-video  (video, Phase 3)
- fal.ai/models/fal-ai/ltxv-13b-098-distilled/image-to-video    (video, engine #2)
- platform.claude.com/docs/en/about-claude/pricing  (QC vision verdicts, Phase 3)
"""

PRICING_VERSION = "2026-07-15"

# (input $/1M tokens, output $/1M tokens)
TOKEN_PRICES: dict[str, tuple[float, float]] = {
    "gpt-5.4-mini-2026-03-17": (0.75, 4.50),
    "gpt-5.4-mini": (0.75, 4.50),
}

# Anthropic — stage 7 QC verdicts (input $/1M, output $/1M)
ANTHROPIC_TOKEN_PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
}

# ElevenLabs bills subscription credits (~1 credit/char), not per call.
# Effective $/1k chars on the Creator tier ($22 / 100k credits) — adjust to
# the actual subscription so CostLedger.voice tracks reality.
ELEVENLABS_USD_PER_1K_CHARS = 0.22

# $ per image at 1024x1024 by quality; scaled by pixel count, ceil to cents.
IMAGE_BASE_RATES: dict[str, float] = {"low": 0.006, "medium": 0.053, "high": 0.211}
IMAGE_BASE_PIXELS = 1024 * 1024

# $ per generated second (Phase 3 video engines via fal.ai).
VIDEO_PRICES: dict[str, dict[str, float]] = {
    "fal-ai/kling-video/v3/standard/image-to-video": {
        "per_second": 0.084,          # generate_audio=false (our pipeline)
        "per_second_with_audio": 0.126,
    },
    # LTX-Video 13B 0.9.8 distilled — $0.02/s billed at 24fps (verified fal docs).
    "fal-ai/ltxv-13b-098-distilled/image-to-video": {
        "per_second": 0.02,
    },
}
