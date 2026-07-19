from pydantic import BaseModel, Field, model_validator
from typing import Any, Literal, Optional

ProductLockMode = Literal["STRICT", "HYBRID", "REFERENCE_ONLY"]


class AssetRef(BaseModel):
    asset_id: str
    kind: Literal["image", "video", "audio", "reference", "render"]
    uri: str
    generated_from: Optional[str] = None      # scene_id
    reference_assets: list[str] = Field(default_factory=list)  # asset_ids conditioned in
    created_at: str                           # ISO 8601


class ReferenceAdDNA(BaseModel):
    """The transferable creative language sampled from a reference ad.

    This intentionally describes pacing and production choices, never copies
    another advertiser's branding, product claims, spoken script, or logo.
    """
    format: str = "vertical 9:16"
    pacing: str
    visual_world: str
    color_palette: list[str] = Field(default_factory=list)
    camera_language: list[str] = Field(default_factory=list)
    transition_language: str
    shot_beats: list[str] = Field(default_factory=list, max_length=12)
    copy_patterns: list[str] = Field(default_factory=list, max_length=5)
    hook_options: list[str] = Field(default_factory=list, max_length=12)


class ReferenceAd(BaseModel):
    asset: AssetRef
    goal: str
    dna: ReferenceAdDNA


class ProductReference(BaseModel):
    asset_id: str
    reference_type: Literal[
        "front", "side", "back", "angle", "hero", "cutout",
        "label_closeup", "detail_closeup", "original",
    ]
    quality_score: float = Field(ge=0.0, le=1.0)
    is_primary: bool = False
    alpha_coverage: Optional[float] = None
    width: int
    height: int


class ProductIdentityProfile(BaseModel):
    silhouette: str = ""
    primary_shape: str = ""
    proportions: str = ""
    primary_colors: list[str] = Field(default_factory=list)
    materials: list[str] = Field(default_factory=list)
    transparent_components: list[str] = Field(default_factory=list)
    button_count: Optional[int] = None
    button_locations: list[str] = Field(default_factory=list)
    ports: list[str] = Field(default_factory=list)
    display_details: list[str] = Field(default_factory=list)
    logo_location: Optional[str] = None
    label_layout: Optional[str] = None
    attachments: list[str] = Field(default_factory=list)
    distinctive_features: list[str] = Field(default_factory=list)
    forbidden_changes: list[str] = Field(default_factory=list)


class ProductIdentityLock(BaseModel):
    shape: str = ""
    silhouette: str = ""
    materials: list[str] = Field(default_factory=list)
    colors: list[str] = Field(default_factory=list)
    dimensions: str = ""
    attachment_geometry: list[str] = Field(default_factory=list)
    logo_position: Optional[str] = None
    display: list[str] = Field(default_factory=list)
    buttons: list[str] = Field(default_factory=list)
    transparent_parts: list[str] = Field(default_factory=list)
    accessories: list[str] = Field(default_factory=list)
    forbidden_changes: list[str] = Field(default_factory=list)


class ProductIdentityQC(BaseModel):
    identity_score: float = Field(ge=0.0, le=1.0)
    silhouette_match: bool
    proportions_match: bool
    colors_match: bool
    materials_match: bool
    logo_match: Optional[bool] = None
    label_match: Optional[bool] = None
    buttons_match: Optional[bool] = None
    chamber_match: Optional[bool] = None
    attachments_match: Optional[bool] = None
    invented_parts: list[str] = Field(default_factory=list)
    missing_parts: list[str] = Field(default_factory=list)
    severe_failure: bool = False
    notes: str = ""

    @property
    def passed(self) -> bool:
        return (
            self.identity_score >= 0.82
            and not self.severe_failure
            and not self.invented_parts
            and not self.missing_parts
        )


class AutomationState(BaseModel):
    """Auto-pilot: the customer hands the whole build to the AI after the
    upload quality gate. Manual mode is untouched — automation only drives the
    same gated primitives (approve -> images -> QC select -> consistency ->
    produce) and drops to needs_review instead of ever forcing a gate."""
    mode: Literal["manual", "auto"] = "manual"
    status: Literal[
        "idle", "generating_images", "checking_consistency", "producing",
        "completed", "needs_review", "failed",
    ] = "idle"
    detail: str = ""
    updated_at: Optional[str] = None


class SceneConsistencyVerdict(BaseModel):
    scene_id: str
    consistent: bool
    drifted_features: list[str] = Field(default_factory=list)
    notes: str = ""


class SceneConsistencyReport(BaseModel):
    """Cross-scene product identity check over the selected scene images.

    Scenes are generated independently, so each can pass per-scene identity QC
    against the uploads while still disagreeing with each other. This report is
    the production gate for that failure mode. `fingerprint` binds the verdict
    to the exact set of selected images it judged — reselecting any image makes
    the report stale."""
    checked_at: str
    fingerprint: str
    consistent: bool
    verdicts: list[SceneConsistencyVerdict] = Field(default_factory=list)
    cost_cents: int = 0


class QCOverride(BaseModel):
    overridden_by: str
    overridden_at: str
    reason: str = ""
    acknowledgement: str


class Generation(BaseModel):
    generation_id: str
    scene_id: str
    # "audio" added for the Project.voiceover generation (stage 8) — the only
    # amendment to the Section A literal; flagged at Gate 3.
    kind: Literal["image", "video", "audio"]
    provider: str
    model: str
    prompt: str
    prompt_hash: str                          # sha256 of compiled prompt
    seed: Optional[int] = None
    reference_assets: list[str] = Field(default_factory=list)
    reference_types: list[str] = Field(default_factory=list)
    source_generation_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    generation_mode: Optional[
        Literal["reference_generation", "composite_exact_product", "hybrid"]
    ] = None
    status: Literal[
        "queued", "submitting", "provider_queued", "provider_processing",
        "downloading", "uploading", "qc_running", "running", "succeeded",
        "retrying", "failed", "timed_out", "cancelled", "qc_rejected",
    ] = "queued"
    qc_notes: Optional[str] = None
    identity_qc: Optional[ProductIdentityQC] = None
    qc_override: Optional[QCOverride] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    cost_cents: int = 0
    asset: Optional[AssetRef] = None
    created_at: str
    queued_at: Optional[str] = None
    # Set when the worker transitions the job to "running"; used by the watchdog
    # to time out jobs whose worker died or hung (see app.workers.reaper).
    started_at: Optional[str] = None
    provider_called_at: Optional[str] = None
    provider_completed_at: Optional[str] = None
    provider_job_id: Optional[str] = None
    provider_status: Optional[str] = None
    provider_progress: Optional[float] = None
    provider_submitted_at: Optional[str] = None
    provider_started_at: Optional[str] = None
    last_provider_check_at: Optional[str] = None
    next_provider_check_at: Optional[str] = None
    provider_status_url: Optional[str] = None
    provider_response_url: Optional[str] = None
    provider_cancel_url: Optional[str] = None
    provider_result: Optional[dict[str, Any]] = None
    last_heartbeat_at: Optional[str] = None
    asset_uploaded_at: Optional[str] = None
    finished_at: Optional[str] = None
    attempt_number: int = 1
    queue_wait_ms: Optional[int] = None
    provider_latency_ms: Optional[int] = None
    download_latency_ms: Optional[int] = None
    upload_latency_ms: Optional[int] = None
    qc_latency_ms: Optional[int] = None
    total_latency_ms: Optional[int] = None


class ProcessingWarning(BaseModel):
    """Structured preprocessing diagnostics (e.g. rembg cutout problems).
    A warning is not a project failure: the original image stays usable."""
    code: Literal[
        "segmentation_failed",
        "segmentation_low_coverage",
        "segmentation_high_coverage",
        "segmentation_fragmented",
        "unsupported_image",
        "reference_low_resolution",
        "reference_extreme_crop",
        "reference_small_product_coverage",
        "reference_bad_transparency",
        "reference_duplicate",
    ]
    asset_id: Optional[str] = None
    message: str
    recoverable: bool = True
    created_at: str


class ProductProfile(BaseModel):
    name: str
    brand: str
    category: str
    colors: list[str]
    materials: list[str]
    product_lock_mode: ProductLockMode = "STRICT"
    key_benefits: list[str] = Field(max_length=3)
    audience: str
    reference_images: list[AssetRef]
    product_references: list[ProductReference] = Field(default_factory=list)
    identity_profile: ProductIdentityProfile = Field(default_factory=ProductIdentityProfile)
    identity_lock: ProductIdentityLock = Field(default_factory=ProductIdentityLock)
    processing_warnings: list[ProcessingWarning] = Field(default_factory=list)

    @model_validator(mode="after")
    def populate_identity_lock(self) -> "ProductProfile":
        if any([
            self.identity_lock.shape,
            self.identity_lock.silhouette,
            self.identity_lock.materials,
            self.identity_lock.colors,
            self.identity_lock.dimensions,
            self.identity_lock.attachment_geometry,
            self.identity_lock.logo_position,
            self.identity_lock.display,
            self.identity_lock.buttons,
            self.identity_lock.transparent_parts,
            self.identity_lock.accessories,
            self.identity_lock.forbidden_changes,
        ]):
            return self
        identity = self.identity_profile
        self.identity_lock = ProductIdentityLock(
            shape=identity.primary_shape,
            silhouette=identity.silhouette,
            materials=identity.materials or self.materials,
            colors=identity.primary_colors or self.colors,
            dimensions=identity.proportions,
            attachment_geometry=identity.attachments,
            logo_position=identity.logo_location,
            display=identity.display_details,
            buttons=[
                *([f"button count: {identity.button_count}"]
                  if identity.button_count is not None else []),
                *identity.button_locations,
            ],
            transparent_parts=identity.transparent_components,
            accessories=identity.distinctive_features,
            forbidden_changes=identity.forbidden_changes,
        )
        return self


class CreativeBrief(BaseModel):
    audience: str
    pain_points: list[str]
    desired_emotion: str
    tone: str
    brand_voice: str
    offer: str
    cta: str
    platform: Literal["tiktok"] = "tiktok"
    target_duration_s: float = Field(ge=10, le=60)
    remake_goal: Optional[str] = None
    reference_ad_dna: Optional[ReferenceAdDNA] = None


class Strategy(BaseModel):
    hook: str
    angle: Literal["problem_solution", "demo", "social_proof",
                   "before_after", "curiosity"]
    emotional_trigger: str
    script: str
    style_id: str
    scene_count: int = Field(ge=3, le=12)


class Scene(BaseModel):
    scene_id: str
    order: int
    duration_s: float = Field(ge=0.5, le=8.0)
    camera: str
    lighting: str
    action: str
    vo_line: Optional[str] = None
    caption: Optional[str] = None
    caption_style: Literal["bounce", "highlight", "plain"] = "bounce"
    transition_out: Literal["cut", "fade", "whip"] = "cut"
    generations: list[Generation] = Field(default_factory=list)
    selected_image: Optional[str] = None      # generation_id
    selected_video: Optional[str] = None      # generation_id
    generation_attempts: int = 0

    @property
    def status(self) -> str:
        if self.selected_video: return "video_ready"
        if self.selected_image: return "image_ready"
        return "draft"


class CostLedger(BaseModel):
    analysis: int = 0
    strategy: int = 0
    images: int = 0
    videos: int = 0
    voice: int = 0
    music: int = 0
    qc: int = 0
    render: int = 0

    @property
    def total(self) -> int:
        return sum(self.model_dump().values())


class StoryboardApproval(BaseModel):
    """Explicit approval state. Batch image generation requires 'approved';
    single-scene generation is allowed as a preview while draft. Any scene
    intent edit after approval returns the storyboard to 'draft'."""
    status: Literal["draft", "approved", "changes_requested"] = "draft"
    approved_at: Optional[str] = None
    approved_by: Optional[str] = None
    approved_version_id: Optional[str] = None


class MusicLicense(BaseModel):
    """License provenance for the licensed stock catalog track (Phase 3).
    Never store only the audio file — keep the license evidence."""
    provider: str
    track_id: str
    license_id: str
    license_type: str
    source_url: Optional[str] = None
    acquired_at: str
    valid_for_commercial_ads: bool
    evidence_asset_id: Optional[str] = None


class ProductionBlockingReason(BaseModel):
    code: str
    scene_id: Optional[str] = None
    message: str


class ProductionReadiness(BaseModel):
    ready: bool
    blocking_reasons: list[ProductionBlockingReason] = Field(default_factory=list)
    scene_summary: dict[str, int]
    estimated_video_cost_cents: int = 0
    estimated_duration_s: float = 0


class ProductionJob(BaseModel):
    production_job_id: str
    project_id: str
    idempotency_key: str
    status: Literal[
        "preflight", "queued", "generating_videos", "generating_voiceover",
        "selecting_music", "rendering", "qc_running", "completed", "failed",
        "cancelled",
    ] = "preflight"
    current_scene_id: Optional[str] = None
    progress_percent: float = Field(default=0, ge=0, le=100)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    created_at: str
    updated_at: str


class Project(BaseModel):
    schema_version: Literal["3.0"] = "3.0"
    project_id: str
    created_at: str
    product: ProductProfile
    reference_ad: Optional[ReferenceAd] = None
    brief: CreativeBrief
    strategy: Strategy
    scenes: list[Scene] = Field(min_length=3, max_length=12)
    storyboard_approval: StoryboardApproval = Field(default_factory=StoryboardApproval)
    voiceover: Optional[Generation] = None
    music: Optional[AssetRef] = None
    music_license: Optional[MusicLicense] = None
    final_render: Optional[AssetRef] = None
    scene_consistency: Optional[SceneConsistencyReport] = None
    automation: AutomationState = Field(default_factory=AutomationState)
    production_job: Optional[ProductionJob] = None
    cost: CostLedger = Field(default_factory=CostLedger)

    @model_validator(mode="after")
    def duration_matches_brief(self):
        total = sum(s.duration_s for s in self.scenes)
        assert abs(total - self.brief.target_duration_s) <= 3.0, \
            f"scene durations sum to {total}s, brief wants {self.brief.target_duration_s}s"
        return self
