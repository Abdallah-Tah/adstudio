"""The 5 visual styles. Descriptive vocabulary only — no trademarked brand names.

The storyboard prompt consumes these dicts verbatim. A guard at import time
rejects trademarked brand names in style content (hard rule).
"""

STYLES: dict[str, dict[str, str]] = {
    "minimal_tech": {
        "palette": "clean whites, cool greys, one restrained accent color",
        "lighting": "soft even studio light, gentle gradients, no harsh shadows",
        "camera": "locked-off or slow precise moves, macro details, centered framing",
        "environment": "seamless backdrops, matte surfaces, negative space",
        "texture": "smooth, precise, engineered surfaces",
        "motion": "slow push-ins, controlled reveals, subtle parallax",
        "typography_mood": "thin, geometric, quiet confidence",
    },
    "warm_lifestyle": {
        "palette": "warm neutrals, honey tones, soft greens",
        "lighting": "golden-hour window light, gentle lens bloom",
        "camera": "handheld but calm, shallow depth of field, eye-level",
        "environment": "lived-in homes, kitchens, morning routines",
        "texture": "linen, wood grain, ceramic, natural materials",
        "motion": "easy drifts, natural gestures, unhurried pacing",
        "typography_mood": "rounded, friendly, human",
    },
    "bold_energy": {
        "palette": "high-saturation primaries, hard color blocking",
        "lighting": "punchy directional light, crisp speculars, colored gels",
        "camera": "fast whips, snap zooms, dynamic low angles",
        "environment": "graphic backdrops, urban surfaces, motion streaks",
        "texture": "glossy, high-contrast, kinetic",
        "motion": "quick cuts, impact frames, rhythm-driven moves",
        "typography_mood": "heavy, loud, all-caps energy",
    },
    "studio_luxury": {
        "palette": "deep charcoals, champagne golds, rich jewel accents",
        "lighting": "sculpted key light, elegant falloff, mirror-like speculars",
        "camera": "slow orbital moves, macro glides over surfaces",
        "environment": "dark seamless sets, polished stone, silk drapes",
        "texture": "velvet, brushed metal, glass reflections",
        "motion": "weighty, deliberate, gliding reveals",
        "typography_mood": "serif elegance, wide letter spacing",
    },
    "ugc_handheld": {
        "palette": "true-to-life colors, phone-camera contrast",
        "lighting": "available light, slightly imperfect exposure",
        "camera": "front-camera selfie angles, quick reframes, vertical-native",
        "environment": "real bedrooms, cars, store aisles, everyday clutter",
        "texture": "authentic, unpolished, relatable",
        "motion": "spontaneous handheld moves, direct-to-camera address",
        "typography_mood": "native captions, casual, emoji-adjacent",
    },
}

# Trademarked names that must never appear in style content (case-insensitive).
BANNED_BRANDS = [
    "apple", "nike", "pixar", "adidas", "samsung", "sony", "gucci",
    "chanel", "tesla", "disney", "coca-cola", "ikea", "dyson", "gopro",
]


def _guard_styles() -> None:
    for style_id, vocab in STYLES.items():
        blob = " ".join(vocab.values()).lower()
        for brand in BANNED_BRANDS:
            if brand in blob:
                raise ValueError(
                    f"trademarked brand {brand!r} found in style {style_id!r}"
                )


_guard_styles()
