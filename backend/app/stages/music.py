"""Stage 8b: music from the licensed library (hard rule: music ONLY from the
licensed source, license reference stored with the asset).

v0.1 source = a managed licensed library in object storage:
    s3://<bucket>/music-library/library.json      (index, see TrackEntry)
    s3://<bucket>/music-library/<track_id>.mp3    (the licensed audio)
Tracks are ingested via `python -m app.cli music-add`, which requires the
license fields up front — a track without license evidence cannot enter the
library. A paid catalog API (chosen at Gate 3) plugs in behind select_track()
without touching callers.
"""
import json
from typing import Optional

from pydantic import BaseModel, Field

from app.schema import MusicLicense, Strategy
from app.storage import Storage

LIBRARY_KEY = "music-library/library.json"


class TrackEntry(BaseModel):
    track_id: str
    title: str
    provider: str                      # catalog / storefront the license is from
    license_id: str
    license_type: str                  # e.g. "royalty_free_commercial"
    source_url: Optional[str] = None
    acquired_at: str                   # ISO 8601
    valid_for_commercial_ads: bool
    evidence_asset_id: Optional[str] = None   # stored receipt/license PDF
    duration_s: float
    moods: list[str] = Field(default_factory=list)  # e.g. ["upbeat", "warm"]
    audio_key: str                     # object-storage key of the mp3


# style -> mood vocabulary used for matching (no brand names — hard rule)
STYLE_MOODS: dict[str, list[str]] = {
    "minimal_tech": ["minimal", "clean", "electronic", "focused"],
    "warm_lifestyle": ["warm", "acoustic", "cozy", "organic"],
    "bold_energy": ["upbeat", "energetic", "driving", "percussive"],
    "studio_luxury": ["cinematic", "elegant", "smooth", "deep"],
    "ugc_handheld": ["playful", "casual", "light", "upbeat"],
}


def load_library(storage: Storage) -> list[TrackEntry]:
    try:
        raw = storage.get_bytes(f"s3://{storage.bucket}/{LIBRARY_KEY}")
    except Exception:
        return []
    return [TrackEntry.model_validate(t) for t in json.loads(raw)]


def save_library(storage: Storage, tracks: list[TrackEntry]) -> None:
    storage.put_bytes(
        json.dumps([t.model_dump(mode="json") for t in tracks], indent=2).encode(),
        LIBRARY_KEY, "application/json")


def select_track(
    tracks: list[TrackEntry], strategy: Strategy, target_duration_s: float
) -> TrackEntry:
    """Deterministic pick: ad-licensed, long enough, best mood match for the
    style; ties broken by shortest sufficient duration then track_id."""
    moods = set(STYLE_MOODS.get(strategy.style_id, []))
    eligible = [t for t in tracks
                if t.valid_for_commercial_ads and t.duration_s >= target_duration_s]
    if not eligible:
        raise LookupError(
            f"no licensed track >= {target_duration_s}s in the music library — "
            "ingest one with `python -m app.cli music-add`")
    return sorted(
        eligible,
        key=lambda t: (-len(moods & set(m.lower() for m in t.moods)),
                       t.duration_s, t.track_id),
    )[0]


def license_for(track: TrackEntry) -> MusicLicense:
    """The license record stored on the Project — never just the audio file."""
    return MusicLicense(
        provider=track.provider,
        track_id=track.track_id,
        license_id=track.license_id,
        license_type=track.license_type,
        source_url=track.source_url,
        acquired_at=track.acquired_at,
        valid_for_commercial_ads=track.valid_for_commercial_ads,
        evidence_asset_id=track.evidence_asset_id,
    )
