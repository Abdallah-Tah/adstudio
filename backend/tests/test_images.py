"""Phase 2: compiler snapshot, staleness, retry cap, mocked-generation integration."""
import io

import pytest
import respx
from PIL import Image

from app import product_composite, product_references
from app.compiler.gpt_image import IMAGE_MODEL, compile_image_prompt
from app.providers import openai_images
from app.schema import AssetRef, ProductProfile, ProductReference, Scene
from app.segmentation import alpha_coverage
from app.workers import images as image_worker

from .conftest import create_test_project, load_fixture, tiny_png

NOW = "2026-07-14T00:00:00Z"


def make_scene(**overrides) -> Scene:
    base = dict(scene_id="scn_fixed", order=0, duration_s=3.5,
                camera="slow push-in", lighting="soft window light",
                action="water spirals over grounds")
    return Scene(**{**base, **overrides})


def make_profile() -> ProductProfile:
    refs = [
        AssetRef(asset_id=f"ast_{i}", kind="reference",
                 uri=f"s3://test-bucket/ast_{i}.png", created_at=NOW)
        for i in range(6)
    ]
    return ProductProfile(
        **load_fixture("analysis"),
        reference_images=refs,
        product_references=[
            ProductReference(asset_id="ast_0", reference_type="hero",
                             quality_score=0.9, is_primary=True, width=1200, height=1600),
            ProductReference(asset_id="ast_1", reference_type="cutout",
                             quality_score=0.8, alpha_coverage=0.42, width=1200, height=1600),
            ProductReference(asset_id="ast_2", reference_type="angle",
                             quality_score=0.7, width=1200, height=1600),
            ProductReference(asset_id="ast_3", reference_type="detail_closeup",
                             quality_score=0.75, width=1200, height=1600),
            ProductReference(asset_id="ast_4", reference_type="side",
                             quality_score=0.6, width=1200, height=1600),
        ],
    )


def test_compiler_snapshot_exact_prompt():
    compiled = compile_image_prompt(make_scene(), "minimal_tech", make_profile())
    assert compiled.model == IMAGE_MODEL
    assert "REFERENCE PRIORITY:" in compiled.prompt
    assert "PRODUCT LOCK MODE: STRICT" in compiled.prompt
    assert "COMPOSITE MODE:" in compiled.prompt
    assert "IDENTITY LOCK:" in compiled.prompt
    assert "SCENE DELTA:" in compiled.prompt
    assert "NEGATIVE CONSTRAINTS:" in compiled.prompt
    assert "- Action: water spirals over grounds" in compiled.prompt
    assert "- Camera: slow push-in" in compiled.prompt
    assert "- Lighting: soft window light" in compiled.prompt
    assert "no extra buttons" in compiled.prompt
    # deterministic hash; identity delta only (no product description in prompt)
    again = compile_image_prompt(make_scene(), "minimal_tech", make_profile())
    assert compiled.prompt_hash == again.prompt_hash
    assert "Ceramic" not in compiled.prompt and "BrewLine" not in compiled.prompt
    # reference cap
    assert len(compiled.reference_asset_ids) == 4
    assert compiled.reference_asset_ids[0] == "ast_0"
    assert compiled.reference_types[:2] == ["hero", "cutout"]
    assert compiled.generation_mode == "composite_exact_product"


def test_product_lock_mode_defaults_to_strict():
    assert make_profile().product_lock_mode == "STRICT"


def test_strict_mode_prefers_composite_or_hybrid_with_valid_cutout():
    static = compile_image_prompt(
        make_scene(action="beauty shot product on bathroom counter"),
        "minimal_tech",
        make_profile(),
    )
    assert static.generation_mode == "composite_exact_product"
    assert "Treat the uploaded product cutout/reference as a fixed asset" in static.prompt

    interactive = compile_image_prompt(
        make_scene(action="hand holding product while combing through hair"),
        "minimal_tech",
        make_profile(),
    )
    assert interactive.generation_mode == "hybrid"
    assert "Generate hands, wrist/arm, hair, background" in interactive.prompt


def test_reference_only_lock_mode_disables_composite():
    profile = make_profile().model_copy(update={"product_lock_mode": "REFERENCE_ONLY"})
    compiled = compile_image_prompt(make_scene(), "minimal_tech", profile)
    assert compiled.generation_mode == "reference_generation"
    assert "REFERENCE GENERATION MODE:" in compiled.prompt


def test_reference_selection_excludes_invalid_cutout():
    profile = make_profile()
    profile.product_references[1] = profile.product_references[1].model_copy(
        update={"alpha_coverage": 0.99, "quality_score": 0.9})
    selected = product_references.select_product_references(make_scene(), profile, max_refs=4)
    assert selected[0].asset_id == "ast_0"
    assert all(r.asset_id != "ast_1" for r in selected)


def test_reference_selection_uses_angle_and_detail_refs():
    scene = make_scene(camera="side profile macro", action="show the chamber and button controls")
    selected = product_references.select_product_references(scene, make_profile(), max_refs=4)
    assert "ast_0" in [r.asset_id for r in selected]
    assert "ast_4" in [r.asset_id for r in selected]
    assert "ast_3" in [r.asset_id for r in selected]


def test_compiler_hash_changes_with_intent():
    a = compile_image_prompt(make_scene(), "minimal_tech", make_profile())
    b = compile_image_prompt(make_scene(camera="orbit"), "minimal_tech", make_profile())
    assert a.prompt_hash != b.prompt_hash


def test_alpha_coverage():
    buf = io.BytesIO()
    img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    for x in range(5):
        for y in range(10):
            img.putpixel((x, y), (255, 0, 0, 255))
    img.save(buf, format="PNG")
    assert alpha_coverage(buf.getvalue()) == 0.5


def test_exact_product_composite_overlays_cutout_pixels():
    bg_buf = io.BytesIO()
    Image.new("RGB", (120, 180), (240, 240, 240)).save(bg_buf, format="PNG")

    cut = Image.new("RGBA", (50, 70), (0, 0, 0, 0))
    for x in range(10, 40):
        for y in range(8, 62):
            cut.putpixel((x, y), (12, 34, 220, 255))
    cut_buf = io.BytesIO()
    cut.save(cut_buf, format="PNG")

    out = product_composite.composite_exact_product(bg_buf.getvalue(), cut_buf.getvalue())
    img = Image.open(io.BytesIO(out)).convert("RGB")
    exact_pixels = sum(
        1
        for x in range(img.width)
        for y in range(img.height)
        if img.getpixel((x, y)) == (12, 34, 220)
    )
    assert exact_pixels > 1000


@respx.mock
def test_generate_select_and_staleness(client, eager_worker, good_provider):
    project = create_test_project(client)
    pid, sid = project["project_id"], project["scenes"][0]["scene_id"]

    r = client.post(f"/projects/{pid}/scenes/{sid}/generate-image")
    assert r.status_code == 200
    gid = r.json()["generation_id"]

    doc = client.get(f"/projects/{pid}").json()
    scene = doc["scenes"][0]
    gen = scene["generations"][0]
    assert gen["generation_id"] == gid
    assert gen["status"] == "succeeded"
    assert gen["stale"] is False
    assert gen["cost_cents"] == 11
    assert gen["queued_at"]
    assert gen["started_at"]
    assert gen["provider_called_at"]
    assert gen["provider_completed_at"]
    assert gen["asset_uploaded_at"]
    assert gen["finished_at"]
    assert gen["queue_wait_ms"] is not None
    assert gen["provider_latency_ms"] is not None
    assert gen["upload_latency_ms"] is not None
    assert gen["total_latency_ms"] is not None
    assert doc["cost"]["images"] == 11
    assert scene["generation_attempts"] == 1

    # select it
    r = client.post(f"/projects/{pid}/scenes/{sid}/select-image",
                    json={"generation_id": gid})
    assert r.status_code == 200
    assert r.json()["scenes"][0]["selected_image"] == gid

    # asset is downloadable for the editor
    asset_id = gen["asset"]["asset_id"]
    assert client.get(f"/projects/{pid}/assets/{asset_id}").status_code == 200

    # editing intent flips staleness (computed, not stored)
    client.post(f"/projects/{pid}/scenes/{sid}", json={"camera": "orbit shot"})
    doc = client.get(f"/projects/{pid}").json()
    assert doc["scenes"][0]["generations"][0]["stale"] is True

    # other scenes untouched
    assert all(s["generations"] == [] for s in doc["scenes"][1:])


@respx.mock
def test_retry_cap_is_absolute(client, eager_worker, bad_provider):
    project = create_test_project(client)
    pid, sid = project["project_id"], project["scenes"][1]["scene_id"]

    for attempt in range(1, 4):
        r = client.post(f"/projects/{pid}/scenes/{sid}/generate-image")
        assert r.status_code == 200, f"attempt {attempt} should be allowed"

    r = client.post(f"/projects/{pid}/scenes/{sid}/generate-image")
    assert r.status_code == 409  # cap of 3 per kind per scene is absolute

    doc = client.get(f"/projects/{pid}").json()
    scene = next(s for s in doc["scenes"] if s["scene_id"] == sid)
    assert scene["generation_attempts"] == 3
    assert len(scene["generations"]) == 3
    assert all(g["status"] == "failed" for g in scene["generations"])
    assert doc["cost"]["images"] == 0  # a bad prompt cannot spend past the cap


@respx.mock
def test_generation_history_preserved_on_regenerate(client, eager_worker, good_provider):
    project = create_test_project(client)
    pid, sid = project["project_id"], project["scenes"][2]["scene_id"]
    g1 = client.post(f"/projects/{pid}/scenes/{sid}/generate-image").json()["generation_id"]
    g2 = client.post(f"/projects/{pid}/scenes/{sid}/generate-image").json()["generation_id"]
    doc = client.get(f"/projects/{pid}").json()
    scene = next(s for s in doc["scenes"] if s["scene_id"] == sid)
    ids = [g["generation_id"] for g in scene["generations"]]
    assert ids == [g1, g2]  # append-only, history preserved


@respx.mock
def test_duplicate_generate_reuses_active_job(client, monkeypatch):
    project = create_test_project(client)
    pid, sid = project["project_id"], project["scenes"][0]["scene_id"]
    monkeypatch.setattr(image_worker.generate_scene_image, "delay",
                        lambda *a, **k: None)

    first = client.post(f"/projects/{pid}/scenes/{sid}/generate-image").json()
    second = client.post(f"/projects/{pid}/scenes/{sid}/generate-image").json()
    assert second["duplicate"] is True
    assert second["generation_id"] == first["generation_id"]

    doc = client.get(f"/projects/{pid}").json()
    scene = doc["scenes"][0]
    assert scene["generation_attempts"] == 1
    assert len(scene["generations"]) == 1


@respx.mock
def test_image_identity_qc_rejection_is_terminal(
        client, eager_worker, monkeypatch):
    from app.schema import ProductIdentityQC
    from app.stages import image_identity_qc

    project = create_test_project(client)
    pid, sid = project["project_id"], project["scenes"][0]["scene_id"]
    monkeypatch.setattr(openai_images, "generate_image",
                        lambda *a, **k: (tiny_png(), 11))
    monkeypatch.setattr(image_worker.generation_config, "AUTO_RETRY_IMAGES", False)
    monkeypatch.setattr(image_identity_qc, "run_qc", lambda *a, **k: (
        ProductIdentityQC(
            identity_score=0.41,
            silhouette_match=False,
            proportions_match=False,
            colors_match=True,
            materials_match=True,
            missing_parts=["transparent chamber"],
            severe_failure=True,
            notes="transparent chamber missing",
        ),
        2,
    ))

    gid = client.post(f"/projects/{pid}/scenes/{sid}/generate-image").json()["generation_id"]
    doc = client.get(f"/projects/{pid}").json()
    gen = doc["scenes"][0]["generations"][0]
    assert gen["generation_id"] == gid
    assert gen["status"] == "qc_rejected"
    assert gen["error_code"] == "QC_REJECTED"
    assert gen["identity_qc"]["identity_score"] == 0.41
    assert doc["cost"]["images"] == 11
    assert doc["cost"]["qc"] == 2


@respx.mock
def test_image_identity_qc_corrective_retry_is_capped(
        client, eager_worker, monkeypatch):
    from app.schema import ProductIdentityQC
    from app.stages import image_identity_qc

    project = create_test_project(client)
    pid, sid = project["project_id"], project["scenes"][0]["scene_id"]
    calls: list[str] = []

    def provider(prompt, refs, size, quality, model):
        calls.append(prompt)
        return tiny_png(), 11

    verdicts = iter([
        ProductIdentityQC(
            identity_score=0.5,
            silhouette_match=True,
            proportions_match=False,
            colors_match=True,
            materials_match=True,
            invented_parts=["extra button"],
            notes="extra button invented",
        ),
        ProductIdentityQC(
            identity_score=0.93,
            silhouette_match=True,
            proportions_match=True,
            colors_match=True,
            materials_match=True,
            notes="identity preserved",
        ),
    ])
    monkeypatch.setattr(openai_images, "generate_image", provider)
    monkeypatch.setattr(image_identity_qc, "run_qc",
                        lambda *a, **k: (next(verdicts), 2))

    client.post(f"/projects/{pid}/scenes/{sid}/generate-image")
    doc = client.get(f"/projects/{pid}").json()
    gen = doc["scenes"][0]["generations"][0]
    assert gen["status"] == "succeeded"
    assert len(calls) == 2
    assert "Previous generation failed identity validation" in calls[1]
    assert "extra button" in calls[1]
    assert doc["cost"]["images"] == 22
    assert doc["cost"]["qc"] == 4


@respx.mock
def test_image_identity_qc_retry_respects_budget(
        client, eager_worker, monkeypatch):
    from app.schema import ProductIdentityQC
    from app.stages import image_identity_qc

    project = create_test_project(client)
    pid, sid = project["project_id"], project["scenes"][0]["scene_id"]
    calls: list[str] = []

    monkeypatch.setattr(openai_images, "generate_image",
                        lambda prompt, *a, **k: (calls.append(prompt) or tiny_png(), 11))
    monkeypatch.setattr(image_worker.generation_config, "PROJECT_BUDGET_CENTS", 11)
    monkeypatch.setattr(image_identity_qc, "run_qc", lambda *a, **k: (
        ProductIdentityQC(
            identity_score=0.5,
            silhouette_match=True,
            proportions_match=False,
            colors_match=True,
            materials_match=True,
            missing_parts=["comb head"],
            notes="comb head changed",
        ),
        2,
    ))

    client.post(f"/projects/{pid}/scenes/{sid}/generate-image")
    doc = client.get(f"/projects/{pid}").json()
    assert doc["scenes"][0]["generations"][0]["status"] == "qc_rejected"
    assert len(calls) == 1
    assert doc["cost"]["images"] == 11


@respx.mock
def test_cancel_active_generation(client, monkeypatch):
    project = create_test_project(client)
    pid, sid = project["project_id"], project["scenes"][0]["scene_id"]
    monkeypatch.setattr(image_worker.generate_scene_image, "delay",
                        lambda *a, **k: None)

    gid = client.post(f"/projects/{pid}/scenes/{sid}/generate-image").json()["generation_id"]
    doc = client.post(f"/projects/{pid}/generations/{gid}/cancel").json()
    gen = doc["scenes"][0]["generations"][0]
    assert gen["status"] == "cancelled"
    assert gen["error_code"] == "CANCELLED"

    assert client.post(f"/projects/{pid}/generations/{gid}/cancel").status_code == 409


@respx.mock
def test_select_failed_generation_rejected(client, eager_worker, bad_provider):
    project = create_test_project(client)
    pid, sid = project["project_id"], project["scenes"][3]["scene_id"]
    gid = client.post(f"/projects/{pid}/scenes/{sid}/generate-image").json()["generation_id"]
    r = client.post(f"/projects/{pid}/scenes/{sid}/select-image",
                    json={"generation_id": gid})
    assert r.status_code == 422


def test_image_cost_estimate():
    # medium 1088x1920 ≈ $0.053 * 1.99 ≈ 11¢
    assert openai_images.image_cost_cents("1088x1920", "medium") == 11
    assert openai_images.image_cost_cents("1024x1024", "low") == 1
