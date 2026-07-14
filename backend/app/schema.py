from pydantic import BaseModel, Field, model_validator
from typing import Literal, Optional


class AssetRef(BaseModel):
    asset_id: str
    kind: Literal["image", "video", "audio", "reference", "render"]
    uri: str
    generated_from: Optional[str] = None      # scene_id
    reference_assets: list[str] = []          # asset_ids conditioned in
    created_at: str                           # ISO 8601


class Generation(BaseModel):
    generation_id: str
    scene_id: str
    kind: Literal["image", "video"]
    provider: str
    model: str
    prompt: str
    prompt_hash: str                          # sha256 of compiled prompt
    seed: Optional[int] = None
    reference_assets: list[str] = []
    status: Literal["queued", "running", "succeeded",
                    "failed", "qc_rejected"] = "queued"
    qc_notes: Optional[str] = None
    cost_cents: int = 0
    asset: Optional[AssetRef] = None
    created_at: str


class ProductProfile(BaseModel):
    name: str
    brand: str
    category: str
    colors: list[str]
    materials: list[str]
    key_benefits: list[str] = Field(max_length=3)
    audience: str
    reference_images: list[AssetRef]


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
    generations: list[Generation] = []
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


class Project(BaseModel):
    schema_version: Literal["3.0"] = "3.0"
    project_id: str
    created_at: str
    product: ProductProfile
    brief: CreativeBrief
    strategy: Strategy
    scenes: list[Scene] = Field(min_length=3, max_length=12)
    voiceover: Optional[Generation] = None
    music: Optional[AssetRef] = None
    final_render: Optional[AssetRef] = None
    cost: CostLedger = CostLedger()

    @model_validator(mode="after")
    def duration_matches_brief(self):
        total = sum(s.duration_s for s in self.scenes)
        assert abs(total - self.brief.target_duration_s) <= 3.0, \
            f"scene durations sum to {total}s, brief wants {self.brief.target_duration_s}s"
        return self
