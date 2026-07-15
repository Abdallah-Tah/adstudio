"""Stage 8a: ElevenLabs VO (mocked) + per-scene span mapping from timestamps."""
import base64

import pytest
import respx
from httpx import Response

from app.stages import audio
from app.schema import Scene

SCRIPT = "Bitter mornings end here.  This is the pour-over that fixes it."


def make_alignment(text: str, spc: float = 0.05) -> dict:
    """Synthetic alignment: each character takes `spc` seconds."""
    return {
        "characters": list(text),
        "character_start_times_seconds": [i * spc for i in range(len(text))],
        "character_end_times_seconds": [(i + 1) * spc for i in range(len(text))],
    }


def make_scenes() -> list[Scene]:
    base = dict(duration_s=3.0, camera="c", lighting="l", action="a")
    return [
        Scene(scene_id="s1", order=0, vo_line="Bitter mornings end here.", **base),
        Scene(scene_id="s2", order=1, vo_line=None, **base),  # visual-only beat
        Scene(scene_id="s3", order=2,
              vo_line="This is  the pour-over that fixes it.", **base),
    ]


def test_scene_spans_from_character_timestamps():
    spans = audio.scene_spans(make_alignment(SCRIPT), make_scenes())
    assert set(spans) == {"s1", "s3"}
    s1 = spans["s1"]
    assert s1[0] == 0.0                       # starts at the first character
    assert s1[1] == pytest.approx(25 * 0.05)  # ends after "…here."
    s3 = spans["s3"]
    assert s3[0] > s1[1]                      # in order, after the gap
    assert s3[1] == pytest.approx(len(SCRIPT) * 0.05)


def test_scene_spans_rejects_unknown_line():
    scenes = make_scenes()
    scenes[0] = scenes[0].model_copy(update={"vo_line": "Never in the script."})
    with pytest.raises(ValueError, match="not found in VO alignment"):
        audio.scene_spans(make_alignment(SCRIPT), scenes)


@respx.mock
def test_synthesize_one_call_with_timestamps(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice123")
    route = respx.post(
        "https://api.elevenlabs.io/v1/text-to-speech/voice123/with-timestamps"
    ).mock(return_value=Response(200, json={
        "audio_base64": base64.b64encode(b"mp3-bytes").decode(),
        "alignment": make_alignment(SCRIPT),
    }))
    mp3, alignment, cost = audio.synthesize(SCRIPT)
    assert mp3 == b"mp3-bytes"
    assert alignment["characters"][0] == "B"
    assert cost == 2  # 63 chars * $0.22/1k = $0.014 -> ceil 2¢
    sent = route.calls[0].request
    assert b'"model_id": "eleven_multilingual_v2"' in sent.read() or \
           b'"model_id":"eleven_multilingual_v2"' in sent.read()
    assert sent.headers["xi-api-key"] == "el-test"


def test_synthesize_requires_key(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ELEVENLABS_API_KEY"):
        audio.synthesize("hello")
