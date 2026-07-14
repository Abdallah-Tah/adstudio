"""Prompt compiler seams: engine Protocols + capabilities.

One compiler target per provider is added per phase (Phase 2: image, Phase 3: video).
"""
from typing import Protocol

from pydantic import BaseModel

from app.schema import AssetRef


class EngineCapabilities(BaseModel):
    max_duration_s: float
    ref_conditioning: bool
    cost_per_s: float  # dollars per second of output


class ImageEngine(Protocol):
    capabilities: EngineCapabilities

    def generate(
        self, prompt: str, reference_images: list[AssetRef], seed: int | None
    ) -> AssetRef: ...


class VideoEngine(Protocol):
    capabilities: EngineCapabilities  # max_duration_s, ref_conditioning, cost_per_s

    def generate(self, image: AssetRef, prompt: str,
                 duration_s: float, seed: int | None) -> AssetRef: ...
