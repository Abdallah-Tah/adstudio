"""Backend source of truth for Produce ad prerequisites."""
import hashlib
import os
import uuid
from datetime import datetime, timezone

from app import generation_config
from app.compiler import engines
from app.schema import (
    Generation,
    ProductionBlockingReason,
    ProductionJob,
    ProductionReadiness,
    Project,
    Scene,
)

ACTIVE_GENERATION_STATUSES = {
    "queued", "submitting", "provider_queued", "provider_processing",
    "downloading", "uploading", "qc_running", "running", "retrying",
}
ACTIVE_PRODUCTION_STATUSES = {
    "preflight", "queued", "generating_videos", "generating_voiceover",
    "selecting_music", "rendering", "qc_running",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def selected_image(scene: Scene) -> Generation | None:
    if not scene.selected_image:
        return None
    return next((g for g in scene.generations
                 if g.generation_id == scene.selected_image and g.kind == "image"), None)


def _image_qc_passed(gen: Generation) -> bool:
    if gen.status != "succeeded" or gen.asset is None:
        return False
    # Existing projects may have approved images generated before identity QC
    # was added. Treat absent QC as pass, but never pass explicit failures.
    if gen.identity_qc is None:
        return True
    return gen.identity_qc.passed


def scene_blockers(scene: Scene) -> list[ProductionBlockingReason]:
    from app.workers.videos import MAX_ATTEMPTS, video_attempts

    reasons: list[ProductionBlockingReason] = []
    image = selected_image(scene)
    label = f"Scene {scene.order + 1}"
    if image is None:
        reasons.append(ProductionBlockingReason(
            code="SCENE_MISSING_SELECTED_IMAGE",
            scene_id=scene.scene_id,
            message=f"{label} has no approved selected image.",
        ))
    elif not _image_qc_passed(image):
        reasons.append(ProductionBlockingReason(
            code="SCENE_QC_FAILED",
            scene_id=scene.scene_id,
            message=f"{label} has no QC-approved image.",
        ))
    if any(g.status in ACTIVE_GENERATION_STATUSES for g in scene.generations):
        reasons.append(ProductionBlockingReason(
            code="SCENE_GENERATION_ACTIVE",
            scene_id=scene.scene_id,
            message=f"{label} has an active generation in progress.",
        ))
    has_rejected_video = scene.selected_video is None and any(
        g.kind == "video" and g.status == "qc_rejected"
        for g in scene.generations
    )
    if has_rejected_video and video_attempts(scene) >= MAX_ATTEMPTS:
        reasons.append(ProductionBlockingReason(
            code="SCENE_VIDEO_ATTEMPTS_EXHAUSTED",
            scene_id=scene.scene_id,
            message=(
                f"{label} has used {MAX_ATTEMPTS}/{MAX_ATTEMPTS} video attempts. "
                "Select a passing video, override QC, or raise the video retry budget before another paid attempt."
            ),
        ))
    elif has_rejected_video:
        reasons.append(ProductionBlockingReason(
            code="SCENE_QC_FAILED",
            scene_id=scene.scene_id,
            message=f"{label} has a video rejected by product identity QC.",
        ))
    return reasons


def idempotency_key(project: Project) -> str:
    engine = engines.active_engine()
    selected = [
        f"{s.scene_id}:{s.selected_image or ''}:{s.selected_video or ''}"
        for s in sorted(project.scenes, key=lambda scene: scene.order)
    ]
    raw = "|".join([
        project.project_id,
        *selected,
        engine.CAPABILITIES.__class__.__name__,
        getattr(engine, "VIDEO_MODEL", ""),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()


def estimate_video_cost(project: Project) -> int:
    engine = engines.active_engine()
    return sum(
        0 if scene.selected_video else engine.video_cost_cents(scene.duration_s)
        for scene in project.scenes
    )


def validate(project: Project) -> ProductionReadiness:
    reasons: list[ProductionBlockingReason] = []
    if project.storyboard_approval.status != "approved":
        reasons.append(ProductionBlockingReason(
            code="STORYBOARD_NOT_APPROVED",
            message="Storyboard must be approved before producing the ad.",
        ))
    for scene in sorted(project.scenes, key=lambda s: s.order):
        reasons.extend(scene_blockers(scene))

    for key in ("FAL_KEY", "ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY"):
        if not os.environ.get(key):
            reasons.append(ProductionBlockingReason(
                code="PROVIDER_NOT_CONFIGURED",
                message=f"{key} is not configured.",
            ))

    if project.production_job and project.production_job.status in ACTIVE_PRODUCTION_STATUSES:
        reasons.append(ProductionBlockingReason(
            code="ACTIVE_PRODUCTION_JOB",
            message="A production job is already active.",
        ))

    estimated = estimate_video_cost(project)
    budget = generation_config.PROJECT_BUDGET_CENTS
    if budget > 0 and project.cost.total + estimated > budget:
        reasons.append(ProductionBlockingReason(
            code="INSUFFICIENT_BUDGET",
            message="Project budget is not sufficient for estimated remaining video cost.",
        ))

    blocked_scenes = {r.scene_id for r in reasons if r.scene_id}
    total = len(project.scenes)
    return ProductionReadiness(
        ready=not reasons,
        blocking_reasons=reasons,
        scene_summary={
            "total": total,
            "ready": max(0, total - len(blocked_scenes)),
            "blocked": len(blocked_scenes),
        },
        estimated_video_cost_cents=estimated,
        estimated_duration_s=sum(s.duration_s for s in project.scenes),
    )


def active_job(project: Project) -> ProductionJob | None:
    job = project.production_job
    if job and job.status in ACTIVE_PRODUCTION_STATUSES:
        return job
    return None


def new_job(project: Project) -> ProductionJob:
    now = _now()
    return ProductionJob(
        production_job_id=f"prod_{uuid.uuid4().hex[:12]}",
        project_id=project.project_id,
        idempotency_key=idempotency_key(project),
        status="queued",
        progress_percent=0,
        created_at=now,
        updated_at=now,
    )
