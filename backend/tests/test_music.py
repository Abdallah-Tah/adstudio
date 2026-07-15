"""Stage 8b: licensed music library — selection rules + license record."""
import pytest

from app.schema import Strategy
from app.stages import music

from .conftest import FakeStorage, load_fixture


def track(track_id: str, duration: float, moods: list[str],
          ads_ok: bool = True) -> music.TrackEntry:
    return music.TrackEntry(
        track_id=track_id, title=track_id, provider="test-catalog",
        license_id=f"lic-{track_id}", license_type="royalty_free_commercial",
        source_url="https://example.com/track", acquired_at="2026-07-15T00:00:00Z",
        valid_for_commercial_ads=ads_ok, duration_s=duration, moods=moods,
        audio_key=f"music-library/{track_id}.mp3",
    )


def strategy() -> Strategy:
    return Strategy(**load_fixture("strategy"))  # style_id: minimal_tech


def test_select_prefers_mood_match_then_shortest_sufficient():
    tracks = [
        track("t_long_generic", 120, ["orchestral"]),
        track("t_clean_short", 30, ["clean", "minimal"]),
        track("t_clean_long", 90, ["clean", "minimal"]),
    ]
    chosen = music.select_track(tracks, strategy(), target_duration_s=20)
    assert chosen.track_id == "t_clean_short"


def test_select_excludes_unlicensed_and_too_short():
    tracks = [
        track("t_not_for_ads", 60, ["minimal"], ads_ok=False),
        track("t_too_short", 10, ["minimal"]),
    ]
    with pytest.raises(LookupError, match="no licensed track"):
        music.select_track(tracks, strategy(), target_duration_s=20)


def test_license_record_preserves_evidence():
    t = track("t1", 60, ["clean"])
    t.evidence_asset_id = "ast_receipt"
    lic = music.license_for(t)
    assert lic.provider == "test-catalog"
    assert lic.license_id == "lic-t1"
    assert lic.valid_for_commercial_ads is True
    assert lic.evidence_asset_id == "ast_receipt"


def test_library_roundtrip():
    storage = FakeStorage()
    assert music.load_library(storage) == []      # empty library is fine
    music.save_library(storage, [track("t1", 45, ["warm"])])
    loaded = music.load_library(storage)
    assert len(loaded) == 1 and loaded[0].track_id == "t1"
