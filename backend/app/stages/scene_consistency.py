"""Cross-scene product identity consistency check.

Per-scene identity QC compares each generated image against the uploads, but
scenes are generated independently, so the product can still differ from scene
to scene while every scene individually passes. This stage judges ALL selected
scene images together against the primary product reference in ONE structured
vision call, and the resulting report gates production (see
app.production_readiness).
"""
import base64
import hashlib
import io
from datetime import datetime, timezone

from PIL import Image
from pydantic import BaseModel, Field

from app.providers.openai_client import structured_call
from app.schema import (
    Project,
    SceneConsistencyReport,
    SceneConsistencyVerdict,
)
from app.storage import Storage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _media_type(raw: bytes) -> str:
    try:
        with Image.open(io.BytesIO(raw)) as img:
            fmt = (img.format or "").lower()
    except Exception:
        return "image/png"
    if fmt in {"jpeg", "jpg"}:
        return "image/jpeg"
    if fmt == "webp":
        return "image/webp"
    return "image/png"


def _image_block(raw: bytes) -> dict:
    b64 = base64.b64encode(raw).decode()
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{_media_type(raw)};base64,{b64}"},
    }


class _SceneVerdictOut(BaseModel):
    scene_number: int = Field(ge=1)
    consistent: bool
    drifted_features: list[str] = Field(default_factory=list)
    notes: str = ""


class _ConsistencyOut(BaseModel):
    consistent: bool
    scenes: list[_SceneVerdictOut]


def fingerprint(project: Project) -> str:
    """Binds a report to the exact selected images it judged."""
    parts = [
        f"{s.scene_id}:{s.selected_image or ''}"
        for s in sorted(project.scenes, key=lambda s: s.order)
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def is_current(project: Project) -> bool:
    report = project.scene_consistency
    return report is not None and report.fingerprint == fingerprint(project)


def _primary_reference_bytes(project: Project, storage: Storage) -> bytes:
    profile = project.product
    by_id = {a.asset_id: a for a in profile.reference_images}
    primary = next((r for r in profile.product_references if r.is_primary), None)
    asset = by_id.get(primary.asset_id) if primary else None
    if asset is None and profile.reference_images:
        asset = profile.reference_images[0]
    if asset is None:
        raise ValueError("project has no product reference images")
    return storage.get_bytes(asset.uri)


def _selected_image_bytes(project: Project, storage: Storage) -> list[tuple[str, bytes]]:
    out: list[tuple[str, bytes]] = []
    for scene in sorted(project.scenes, key=lambda s: s.order):
        gen = next((g for g in scene.generations
                    if g.generation_id == scene.selected_image
                    and g.kind == "image" and g.asset), None)
        if gen is None:
            raise ValueError(
                f"scene {scene.scene_id} has no selected image with an asset")
        out.append((scene.scene_id, storage.get_bytes(gen.asset.uri)))
    return out


def run_check(project: Project, storage: Storage) -> tuple[SceneConsistencyReport, int]:
    """Returns (report, cost_cents). One structured vision call for the project."""
    reference = _primary_reference_bytes(project, storage)
    scene_images = _selected_image_bytes(project, storage)

    content: list[dict] = [{
        "type": "text",
        "text": (
            "You are a strict cross-scene product identity gate for a video ad.\n"
            "The first image is the authoritative reference photo of the real "
            f"product. The following {len(scene_images)} images are the approved "
            "stills for scenes 1..N of the same ad, generated independently.\n\n"
            "The SAME physical product must appear in every scene. Judge each "
            "scene image against the reference AND against the other scenes:\n"
            "- consistent: the product is the same object — same shape, "
            "proportions, colors, materials, buttons, attachments, chambers, "
            "displays, logos/labels, ports. Camera angle, background, hands, "
            "and lighting may change freely.\n"
            "- drifted_features: name each product feature that differs "
            "(e.g. 'button count', 'comb attachment', 'transparent chamber').\n"
            "Set the top-level consistent to true only if every scene is "
            "consistent. Return one verdict per scene, numbered from 1 in the "
            "order the scene images were given."
        ),
    }, _image_block(reference)]
    for index, (_, raw) in enumerate(scene_images, start=1):
        content.append({"type": "text", "text": f"Scene {index}:"})
        content.append(_image_block(raw))

    result, cost = structured_call(_ConsistencyOut, [{"role": "user", "content": content}])

    by_number = {v.scene_number: v for v in result.scenes}
    verdicts: list[SceneConsistencyVerdict] = []
    for index, (scene_id, _) in enumerate(scene_images, start=1):
        out = by_number.get(index)
        if out is None:
            verdicts.append(SceneConsistencyVerdict(
                scene_id=scene_id, consistent=False,
                notes="no verdict returned for this scene"))
        else:
            verdicts.append(SceneConsistencyVerdict(
                scene_id=scene_id, consistent=out.consistent,
                drifted_features=out.drifted_features, notes=out.notes))

    report = SceneConsistencyReport(
        checked_at=_now(),
        fingerprint=fingerprint(project),
        consistent=result.consistent and all(v.consistent for v in verdicts),
        verdicts=verdicts,
        cost_cents=cost,
    )
    return report, cost
