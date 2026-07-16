"""Video-engine registry: selection, LTX compiler/pricing, per-engine payloads."""
import pytest

from app.compiler import engines, kling_fal, ltx_fal
from app.schema import Scene


def make_scene(**overrides) -> Scene:
    base = dict(scene_id="scn_v", order=0, duration_s=3.5,
                camera="slow orbit", lighting="soft rim light",
                action="steam rises from the cup", selected_image="gen_img1")
    return Scene(**{**base, **overrides})


def test_default_engine_is_kling(monkeypatch):
    monkeypatch.delenv("VIDEO_ENGINE", raising=False)
    assert engines.active_name() == "kling"
    assert engines.active_engine() is kling_fal
    # unknown value falls back to the default, never crashes
    monkeypatch.setenv("VIDEO_ENGINE", "nope")
    assert engines.active_name() == "kling"


def test_select_ltx(monkeypatch):
    monkeypatch.setenv("VIDEO_ENGINE", "ltx")
    assert engines.active_engine() is ltx_fal


def test_by_model_roundtrip():
    assert engines.by_model(kling_fal.VIDEO_MODEL) is kling_fal
    assert engines.by_model(ltx_fal.VIDEO_MODEL) is ltx_fal
    assert engines.by_model("something-retired") is None


def test_ltx_frames_are_8k_plus_1_and_clamped():
    # 3.5s * 24fps = 84 -> nearest 8k+1 is 81
    assert ltx_fal.frames_for_duration(3.5) == 81
    assert (ltx_fal.frames_for_duration(3.5) - 1) % 8 == 0
    # clamp floor/ceiling
    assert ltx_fal.frames_for_duration(0.1) == ltx_fal.MIN_FRAMES
    assert ltx_fal.frames_for_duration(999) == ltx_fal.MAX_FRAMES
    assert (ltx_fal.MIN_FRAMES - 1) % 8 == 0 and (ltx_fal.MAX_FRAMES - 1) % 8 == 0


def test_ltx_cost_is_much_cheaper_than_kling():
    # 81 frames / 24fps = 3.375s * $0.02 = $0.0675 -> ceil 7¢
    assert ltx_fal.video_cost_cents(3.5) == 7
    assert kling_fal.video_cost_cents(3.5) == 34   # unchanged


def test_ltx_compile_and_payload():
    compiled = ltx_fal.compile_video_prompt(make_scene(), "minimal_tech")
    assert compiled.model == "fal-ai/ltxv-13b-098-distilled/image-to-video"
    assert compiled.num_frames == 81 and compiled.frame_rate == 24
    assert "BrewLine" not in compiled.prompt  # identity carried by start frame
    payload = ltx_fal.build_payload(compiled, "data:image/png;base64,AAAA")
    # LTX param names (NOT kling's start_image_url/duration/generate_audio)
    assert payload["image_url"] == "data:image/png;base64,AAAA"
    assert payload["num_frames"] == 81
    assert payload["frame_rate"] == 24
    assert payload["resolution"] == "720p"
    assert payload["aspect_ratio"] == "9:16"
    assert payload["expand_prompt"] is True          # LTX prompt enhancement
    assert "start_image_url" not in payload and "generate_audio" not in payload
    # no selected image -> refuse (video needs an approved image)
    with pytest.raises(ValueError, match="no selected image"):
        ltx_fal.compile_video_prompt(make_scene(selected_image=None), "minimal_tech")


def test_kling_payload_shape_unchanged():
    compiled = kling_fal.compile_video_prompt(make_scene(), "minimal_tech")
    payload = kling_fal.build_payload(compiled, "data:image/png;base64,AAAA")
    assert payload["start_image_url"] == "data:image/png;base64,AAAA"
    assert payload["duration"] == "4"                # ceil(3.5) whole seconds
    assert payload["generate_audio"] is False
    assert "num_frames" not in payload
