"""Stages 1-4 run synchronously (Phase 1); snapshot after each stage.

Used by both the API (POST /projects) and the CLI.
"""
import mimetypes
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import product_references, segmentation
from app.db import ProjectRow
from app.schema import AssetRef, CostLedger, ProcessingWarning, ProductReference, Project
from app.snapshots import snapshot_state
from app.stages import analysis, brief as brief_stage, storyboard, strategy as strategy_stage
from app.stages.brief import UserInputs
from app.storage import Storage


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def guess_mime(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "image/jpeg"


def create_project(
    session: Session,
    storage: Storage,
    photos: list[tuple[str, bytes]],          # (filename, raw bytes)
    description: str,
    user: UserInputs,
    actor: str = "user",
) -> Project:
    """Upload photos, run stages 1-4, snapshot after each, persist the Project."""
    project_id = f"prj_{uuid.uuid4().hex[:12]}"

    # Upload originals + rembg cutouts as reference assets.
    storage.ensure_bucket()
    refs: list[AssetRef] = []
    product_refs: list[ProductReference] = []
    photo_payloads: list[tuple[bytes, str]] = []
    warnings: list[ProcessingWarning] = []
    seen_hashes: set[str] = set()
    for index, (filename, raw) in enumerate(photos):
        mime = guess_mime(filename)
        asset_id = f"ast_{uuid.uuid4().hex[:12]}"
        uri = storage.put_bytes(raw, f"{project_id}/uploads/{asset_id}", mime)
        original = AssetRef(asset_id=asset_id, kind="reference", uri=uri, created_at=_now())
        refs.append(original)
        photo_payloads.append((raw, mime))
        warnings.extend(product_references.upload_warnings(
            asset_id, raw, seen_hashes=seen_hashes))
        seen_hashes.add(product_references.content_hash(raw))
        try:
            product_refs.append(product_references.make_original_reference(
                asset_id, filename, raw, index))
        except Exception:
            warnings.append(ProcessingWarning(
                code="unsupported_image",
                asset_id=asset_id,
                message="image could not be opened for reference metadata",
                created_at=_now(),
            ))
        cutout, warning = segmentation.segment(raw)
        if cutout is not None:
            cut_id = f"ast_{uuid.uuid4().hex[:12]}"
            cut_uri = storage.put_bytes(
                cutout, f"{project_id}/uploads/{cut_id}.png", "image/png")
            refs.append(AssetRef(asset_id=cut_id, kind="reference", uri=cut_uri,
                                 reference_assets=[asset_id], created_at=_now()))
            try:
                cut_ref = product_references.make_cutout_reference(
                    cut_id, cutout, primary=False)
                product_refs.append(cut_ref)
                if cut_ref.alpha_coverage is not None and not (
                    segmentation.ALPHA_MIN <= cut_ref.alpha_coverage <= segmentation.ALPHA_MAX
                ):
                    warnings.append(ProcessingWarning(
                        code="reference_bad_transparency",
                        asset_id=cut_id,
                        message=f"cutout alpha coverage {cut_ref.alpha_coverage:.2f} is not usable as a primary reference",
                        created_at=_now(),
                    ))
            except Exception:
                warnings.append(ProcessingWarning(
                    code="unsupported_image",
                    asset_id=cut_id,
                    message="cutout could not be opened for reference metadata",
                    created_at=_now(),
                ))
        elif warning is not None:
            # recoverable: the original stays a usable reference asset
            warnings.append(warning.model_copy(update={"asset_id": asset_id}))

    cost = CostLedger()
    state: dict = {"project_id": project_id, "description": description,
                   "user_inputs": user.model_dump(exclude_none=True)}

    # Stage 1 — analysis
    profile, c = analysis.run(photo_payloads, description, refs)
    profile.product_references = product_refs
    profile.processing_warnings = warnings
    cost.analysis += c
    state["product"] = profile.model_dump(mode="json")
    snapshot_state(session, project_id, state, actor, "stage1:analysis")

    # Stage 2 — brief
    brief, c = brief_stage.run(profile, user)
    cost.strategy += c
    state["brief"] = brief.model_dump(mode="json")
    snapshot_state(session, project_id, state, actor, "stage2:brief")

    # Stage 3 — strategy
    strategy, c = strategy_stage.run(brief, profile, style_id=user.style)
    cost.strategy += c
    state["strategy"] = strategy.model_dump(mode="json")
    snapshot_state(session, project_id, state, actor, "stage3:strategy")

    # Stage 4 — storyboard (one corrective retry inside; both calls metered)
    try:
        scenes, c, sb_meta = storyboard.run(strategy, brief)
    except storyboard.StoryboardValidationError as exc:
        # observability: record the failed attempt(s) + spend before aborting
        state["storyboard_failure"] = {"error": str(exc), **exc.meta,
                                       "cost_cents": exc.cost_cents}
        snapshot_state(session, project_id, state, actor, "stage4:storyboard:failed")
        session.commit()
        raise
    cost.strategy += c
    state["storyboard_meta"] = sb_meta

    project = Project(
        project_id=project_id,
        created_at=_now(),
        product=profile,
        brief=brief,
        strategy=strategy,
        scenes=scenes,
        cost=cost,
    )
    # stage-4 snapshot carries the retry/prompt-version meta (observability)
    snapshot_state(session, project_id,
                   {**project.model_dump(mode="json"), "storyboard_meta": sb_meta},
                   actor, "stage4:storyboard")
    session.add(ProjectRow(project_id=project_id, data=project.model_dump(mode="json")))
    session.commit()
    return project
