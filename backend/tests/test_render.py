"""Stage 9: timeline math, ASS caption building, and one real (tiny) ffmpeg
end-to-end render — clips are lavfi-generated, no provider involved."""
import shutil
import subprocess
from pathlib import Path

import pytest

from app.schema import Scene
from app.stages import render

HAVE_FFMPEG = Path(render.FFMPEG).exists()


def scene(sid: str, order: int, duration: float, transition: str = "cut",
          vo: str | None = None, style: str = "bounce") -> Scene:
    return Scene(scene_id=sid, order=order, duration_s=duration,
                 camera="c", lighting="l", action="a", vo_line=vo,
                 caption_style=style, transition_out=transition)


def test_compute_timeline_transition_overlap():
    scenes = [scene("a", 0, 3.0, "fade"), scene("b", 1, 4.0, "whip"),
              scene("c", 2, 2.0)]
    starts, total = render.compute_timeline(scenes)
    assert starts[0] == 0.0
    assert starts[1] == pytest.approx(3.0 - 0.4)   # fade eats 0.4s
    assert starts[2] == pytest.approx(2.6 + 4.0 - 0.2)
    assert total == pytest.approx(2.6 + 3.8 + 2.0)


def make_alignment(text: str, spc: float = 0.1) -> dict:
    return {
        "characters": list(text),
        "character_start_times_seconds": [i * spc for i in range(len(text))],
        "character_end_times_seconds": [(i + 1) * spc for i in range(len(text))],
    }


def test_build_ass_styles_and_output_offsets():
    from app.stages import audio as audio_stage

    text = "Fresh coffee every single morning"
    scenes = [scene("s1", 0, 3.0, vo="Fresh coffee every single morning",
                    style="bounce")]
    alignment = make_alignment(text)
    spans = audio_stage.scene_spans(alignment, scenes)
    words = audio_stage.scene_words(alignment, scenes)
    assert [w for w, _, _ in words["s1"]] == text.split()

    ass = render.build_ass(scenes, words, spans, {"s1": 10.0})
    assert "Style: bounce" in ass and "PlayResY: 1920" in ass
    # output-timeline offset: scene starts at 10.0s in the final cut
    assert "Dialogue: 0,0:00:10.00," in ass
    assert "FRESH COFFEE" in ass                      # bounce = 2-word UPPERCASE
    assert "\\fscx115" in ass                          # scale pop

    # highlight -> karaoke tags
    scenes_h = [scene("s1", 0, 3.0, vo=text, style="highlight")]
    ass_h = render.build_ass(scenes_h, words, spans, {"s1": 0.0})
    assert "\\k" in ass_h and "FRESH" not in ass_h

    # plain -> no tags
    scenes_p = [scene("s1", 0, 3.0, vo=text, style="plain")]
    ass_p = render.build_ass(scenes_p, words, spans, {"s1": 0.0})
    assert "\\k" not in ass_p and "\\t(" not in ass_p


def _lavfi_clip(path: Path, color: str, seconds: float) -> None:
    subprocess.run(
        [render.FFMPEG, "-y", "-f", "lavfi",
         "-i", f"color=c={color}:s=540x960:d={seconds}:r=30",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         str(path)], capture_output=True, check=True)


def _lavfi_mp3(path: Path, seconds: float, freq: int) -> None:
    subprocess.run(
        [render.FFMPEG, "-y", "-f", "lavfi",
         "-i", f"sine=frequency={freq}:duration={seconds}",
         "-c:a", "libmp3lame", str(path)], capture_output=True, check=True)


@pytest.mark.skipif(not HAVE_FFMPEG, reason="system ffmpeg not present")
def test_end_to_end_render(tmp_path):
    if shutil.which(render.FFPROBE) is None and not Path(render.FFPROBE).exists():
        pytest.skip("ffprobe missing")
    _lavfi_clip(tmp_path / "c1.mp4", "steelblue", 2.0)
    _lavfi_clip(tmp_path / "c2.mp4", "darkorange", 2.0)
    _lavfi_mp3(tmp_path / "vo.mp3", 4.0, 440)
    _lavfi_mp3(tmp_path / "music.mp3", 6.0, 220)

    text = "Great coffee starts here. Order yours today."
    scenes = [
        scene("s1", 0, 2.0, "fade", vo="Great coffee starts here.", style="bounce"),
        scene("s2", 1, 2.0, vo="Order yours today.", style="plain"),
    ]
    mp4 = render.render(
        scenes,
        clips={"s1": (tmp_path / "c1.mp4").read_bytes(),
               "s2": (tmp_path / "c2.mp4").read_bytes()},
        vo_mp3=(tmp_path / "vo.mp3").read_bytes(),
        alignment=make_alignment(text, spc=0.08),
        music_mp3=(tmp_path / "music.mp3").read_bytes(),
    )
    out = tmp_path / "final.mp4"
    out.write_bytes(mp4)
    duration = render.probe_duration(out)
    assert duration == pytest.approx(3.6, abs=0.25)   # 2 + 2 - 0.4 fade overlap
    probe = subprocess.run(
        [render.FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True)
    assert probe.stdout.strip() == "1080,1920"
    # has an audio stream (VO + ducked music mixed in)
    probe_a = subprocess.run(
        [render.FFPROBE, "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True)
    assert probe_a.stdout.strip() == "aac"
