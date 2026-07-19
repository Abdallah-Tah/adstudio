"""Reference-video analysis for the Remake workflow."""
import base64
import subprocess
import tempfile
from pathlib import Path

from app.providers.openai_client import structured_call
from app.schema import ReferenceAdDNA

MAX_VIDEO_BYTES = 100 * 1024 * 1024
MAX_FRAMES = 10


def _frames(video: bytes, filename: str) -> list[bytes]:
    """Extract up to ten compact frames using ffmpeg."""
    suffix = Path(filename).suffix or ".mp4"
    with tempfile.TemporaryDirectory(prefix="adstudio-reference-") as directory:
        root = Path(directory)
        source = root / f"source{suffix}"
        source.write_bytes(video)
        pattern = root / "frame-%02d.jpg"
        try:
            subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(source), "-vf",
                 "fps=1/2,scale=768:-2", "-frames:v", str(MAX_FRAMES),
                 str(pattern)],
                check=True, capture_output=True, timeout=45,
            )
        except FileNotFoundError as exc:
            raise ValueError("ffmpeg is required to analyse a reference video") from exc
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise ValueError("could not sample the uploaded reference video") from exc
        return [path.read_bytes() for path in sorted(root.glob("frame-*.jpg"))]


def run(video: bytes, filename: str, goal: str) -> tuple[ReferenceAdDNA, int]:
    if not video:
        raise ValueError("reference video is empty")
    if len(video) > MAX_VIDEO_BYTES:
        raise ValueError("reference video must be 100 MB or smaller")
    frames = _frames(video, filename)
    if not frames:
        raise ValueError("reference video did not contain usable frames")

    prompt = (
        "You are analysing a reference advertisement to create an original "
        "campaign for a different customer product. Extract ONLY transferable "
        "creative direction: pacing, shot progression, visual world, palette, "
        "camera and transition language, generic copy patterns, and 8-12 "
        "original hook options. Never identify, repeat, or imitate visible "
        "brand names, logos, product claims, or exact spoken copy from the "
        "source. The customer's goal is authoritative:\n\n" + goal
    )
    content: list[dict] = [{"type": "text", "text": prompt}]
    for frame in frames:
        b64 = base64.b64encode(frame).decode()
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
    return structured_call(ReferenceAdDNA, [{"role": "user", "content": content}])
