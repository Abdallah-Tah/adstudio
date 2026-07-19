"""Stage 9: FFmpeg-only render — EDL-driven assembly of the selected clips.

Follows the vendored freecut hard rules (vendor/VENDOR.md):
- per-segment normalize first, assemble second (never one giant graph)
- 30ms audio fades at every VO chunk boundary (no pops)
- captions (ASS) applied LAST in the filter chain
- caption timings use OUTPUT-timeline offsets
- MarginV keeps captions clear of vertical-platform UI (~30% up)

Output: 1080×1920 H.264 MP4, 30 fps, AAC audio (VO placed per scene + licensed
music ducked -12 dB under it).

Discovers FFmpeg from the environment/PATH so the native macOS development
setup works as well as Linux workers.  When the installed build does not have
libass, production still completes without burned-in captions rather than
failing after all scenes have passed QC.
"""
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.schema import Scene



def _media_binary(name: str) -> str:
    """Return an explicitly configured binary or the first one on PATH."""
    configured = os.environ.get(f"ADSTUDIO_{name.upper()}_BIN")
    return configured or shutil.which(name) or f"/usr/bin/{name}"


FFMPEG = _media_binary("ffmpeg")
FFPROBE = _media_binary("ffprobe")
WIDTH, HEIGHT, FPS = 1080, 1920, 30
MUSIC_DUCK_DB = -12.0

# transition -> (xfade transition name, duration seconds)
TRANSITIONS = {
    "cut": ("fade", 1 / FPS),      # one-frame xfade reads as a hard cut
    "fade": ("fade", 0.4),
    "whip": ("slideleft", 0.2),
}


@dataclass
class TimedScene:
    scene: Scene
    clip_path: Path
    start_out: float = 0.0         # scene start on the OUTPUT timeline


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[-2000:]}")


def supports_ass_captions() -> bool:
    """Whether this FFmpeg build can apply ASS subtitle files."""
    try:
        proc = subprocess.run(
            [FFMPEG, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return proc.returncode == 0 and any(
        line.split()[1:2] == ["ass"] for line in proc.stdout.splitlines() if line.split()
    )


def probe_duration(path: Path) -> float:
    proc = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float(proc.stdout.strip())


def compute_timeline(scenes: list[Scene]) -> tuple[list[float], float]:
    """Output-timeline start of each scene + total duration, accounting for
    transition overlap (xfade eats `d` seconds between neighbours)."""
    starts, t = [], 0.0
    for i, scene in enumerate(scenes):
        starts.append(t)
        t += scene.duration_s
        if i < len(scenes) - 1:
            t -= TRANSITIONS[scene.transition_out][1]
    return starts, t


def normalize_clip(src: Path, dst: Path, duration_s: float) -> None:
    """Per-segment pass: trim to intent, 1080x1920 center-crop, 30fps, no audio."""
    _run([
        FFMPEG, "-y", "-i", str(src), "-t", f"{duration_s:.3f}",
        "-vf",
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},fps={FPS},setsar=1",
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p", str(dst),
    ])


def assemble_video(timed: list[TimedScene], out: Path) -> None:
    """xfade chain over the normalized clips (video only)."""
    if len(timed) == 1:
        timed[0].clip_path.rename(out)
        return
    inputs: list[str] = []
    for ts in timed:
        inputs += ["-i", str(ts.clip_path)]
    graph, prev, t = [], "[0:v]", 0.0
    for i in range(len(timed) - 1):
        scene = timed[i].scene
        trans, d = TRANSITIONS[scene.transition_out]
        t += scene.duration_s - d
        label = f"[v{i}]" if i < len(timed) - 2 else "[vout]"
        graph.append(
            f"{prev}[{i + 1}:v]xfade=transition={trans}:duration={d:.4f}:"
            f"offset={t:.4f}{label}")
        prev = label
    _run([
        FFMPEG, "-y", *inputs, "-filter_complex", ";".join(graph),
        "-map", "[vout]", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "18", "-pix_fmt", "yuv420p", str(out),
    ])


# ---------------------------------------------------------------- captions --

ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {WIDTH}
PlayResY: {HEIGHT}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: bounce,DejaVu Sans,110,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,8,0,2,60,60,560,1
Style: highlight,DejaVu Sans,84,&H00FFFFFF,&H0000D7FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,6,0,2,60,60,560,1
Style: plain,DejaVu Sans,72,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,5,0,2,60,60,560,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _chunk(words: list[tuple[str, float, float]], n: int):
    for i in range(0, len(words), n):
        yield words[i:i + n]


def build_ass(
    scenes: list[Scene],
    words_by_scene: dict[str, list[tuple[str, float, float]]],
    spans: dict[str, tuple[float, float]],
    scene_starts: dict[str, float],
) -> str:
    """Caption events retimed to the OUTPUT timeline:
    out_time = scene_start_out + (word_time - vo_span_start)."""
    events: list[str] = []
    for scene in scenes:
        words = words_by_scene.get(scene.scene_id)
        if not words:
            continue
        span_start = spans[scene.scene_id][0]
        base = scene_starts[scene.scene_id]

        def out_t(t: float) -> float:
            return base + (t - span_start)

        if scene.caption_style == "bounce":
            # 2-word UPPERCASE chunks with a scale pop
            for chunk in _chunk(words, 2):
                text = " ".join(w for w, _, _ in chunk).upper()
                start, end = out_t(chunk[0][1]), out_t(chunk[-1][2])
                events.append(
                    f"Dialogue: 0,{_ts(start)},{_ts(end)},bounce,,0,0,0,,"
                    "{\\t(0,120,\\fscx115\\fscy115)\\t(120,240,\\fscx100\\fscy100)}"
                    + text)
        elif scene.caption_style == "highlight":
            # 4-word lines with per-word karaoke highlight
            for chunk in _chunk(words, 4):
                start, end = out_t(chunk[0][1]), out_t(chunk[-1][2])
                parts = []
                for w, ws, we in chunk:
                    k = max(1, round((we - ws) * 100))  # centiseconds
                    parts.append(f"{{\\k{k}}}{w}")
                events.append(
                    f"Dialogue: 0,{_ts(start)},{_ts(end)},highlight,,0,0,0,,"
                    + " ".join(parts))
        else:  # plain
            for chunk in _chunk(words, 4):
                text = " ".join(w for w, _, _ in chunk)
                start, end = out_t(chunk[0][1]), out_t(chunk[-1][2])
                events.append(
                    f"Dialogue: 0,{_ts(start)},{_ts(end)},plain,,0,0,0,," + text)
    return ASS_HEADER + "\n".join(events) + "\n"


# ------------------------------------------------------------------- audio --

def build_audio_graph(
    timed: list[TimedScene],
    spans: dict[str, tuple[float, float]],
    total_s: float,
    vo_input: int,
    music_input: int | None,
) -> tuple[str, str]:
    """VO chunks placed at their scene starts (30ms fades at every boundary)
    + music ducked -12dB, faded out over the last second."""
    parts: list[str] = []
    mix_labels: list[str] = []
    for i, ts in enumerate(timed):
        span = spans.get(ts.scene.scene_id)
        if span is None:
            continue
        start, end = span
        dur = end - start
        delay_ms = int(ts.start_out * 1000)
        parts.append(
            f"[{vo_input}:a]atrim=start={start:.3f}:end={end:.3f},"
            f"asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d=0.03,afade=t=out:st={max(0.0, dur - 0.03):.3f}:d=0.03,"
            f"adelay={delay_ms}|{delay_ms}[vo{i}]")
        mix_labels.append(f"[vo{i}]")
    if music_input is not None:
        parts.append(
            f"[{music_input}:a]atrim=0:{total_s:.3f},asetpts=PTS-STARTPTS,"
            f"volume={MUSIC_DUCK_DB}dB,"
            f"afade=t=out:st={max(0.0, total_s - 1):.3f}:d=1[music]")
        mix_labels.append("[music]")
    if not mix_labels:
        return "", ""
    parts.append(
        f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:normalize=0,"
        f"apad=whole_dur={total_s:.3f}[aout]")
    return ";".join(parts), "[aout]"


# ---------------------------------------------------------------- pipeline --

def render(
    scenes: list[Scene],
    clips: dict[str, bytes],          # scene_id -> selected clip mp4 bytes
    vo_mp3: bytes | None,
    alignment: dict | None,
    music_mp3: bytes | None,
    workdir: Path | None = None,
) -> bytes:
    """Full stage 9. Returns the final MP4 bytes."""
    from app.stages import audio as audio_stage

    ordered = sorted(scenes, key=lambda s: s.order)
    with tempfile.TemporaryDirectory(prefix="adstudio-render-") as tmp:
        work = workdir or Path(tmp)
        starts_list, total = compute_timeline(ordered)
        timed: list[TimedScene] = []
        for i, scene in enumerate(ordered):
            raw = work / f"raw_{i}.mp4"
            raw.write_bytes(clips[scene.scene_id])
            norm = work / f"seg_{i}.mp4"
            normalize_clip(raw, norm, scene.duration_s)
            timed.append(TimedScene(scene=scene, clip_path=norm,
                                    start_out=starts_list[i]))

        base = work / "base.mp4"
        assemble_video(timed, base)

        inputs = [FFMPEG, "-y", "-i", str(base)]
        n_inputs = 1
        vo_input = music_input = None
        spans: dict[str, tuple[float, float]] = {}
        if vo_mp3 is not None and alignment is not None:
            vo_path = work / "vo.mp3"
            vo_path.write_bytes(vo_mp3)
            vo_input, n_inputs = n_inputs, n_inputs + 1
            inputs += ["-i", str(vo_path)]
            spans = audio_stage.scene_spans(alignment, ordered)
        if music_mp3 is not None:
            music_path = work / "music.mp3"
            music_path.write_bytes(music_mp3)
            music_input, n_inputs = n_inputs, n_inputs + 1
            inputs += ["-i", str(music_path)]

        graph_parts: list[str] = []
        # captions LAST in the chain (hard rule) — after any video work
        # Stream specifiers (e.g. ``0:v``) are mapped directly; bracketed
        # labels are only valid for streams produced by a filter graph.
        video_label = "0:v"
        if vo_mp3 is not None and alignment is not None and supports_ass_captions():
            ass_path = work / "captions.ass"
            words = audio_stage.scene_words(alignment, ordered)
            scene_starts = {t.scene.scene_id: t.start_out for t in timed}
            ass_path.write_text(build_ass(ordered, words, spans, scene_starts))
            graph_parts.append(
                f"[0:v]ass={ass_path}[vcap]")
            video_label = "[vcap]"

        audio_graph, audio_label = build_audio_graph(
            timed, spans, total, vo_input or 1, music_input)
        if audio_graph:
            graph_parts.append(audio_graph)

        out = work / "final.mp4"
        cmd = list(inputs)
        if graph_parts:
            cmd += ["-filter_complex", ";".join(graph_parts)]
            cmd += ["-map", video_label]
        else:
            cmd += ["-map", "0:v"]
        if audio_label:
            cmd += ["-map", audio_label, "-c:a", "aac", "-b:a", "192k"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-pix_fmt", "yuv420p", "-t", f"{total:.3f}", str(out)]
        _run(cmd)
        return out.read_bytes()
