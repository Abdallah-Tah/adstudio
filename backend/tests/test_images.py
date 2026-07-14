"""Phase 2: compiler snapshot, staleness, retry cap, mocked-generation integration."""
import io

import pytest
import respx
from PIL import Image

from app.compiler.gpt_image import IMAGE_MODEL, compile_image_prompt
from app.providers import openai_images
from app.schema import AssetRef, ProductProfile, Scene
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
    return ProductProfile(
        **load_fixture("analysis"),
        reference_images=[
            AssetRef(asset_id=f"ast_{i}", kind="reference",
                     uri=f"s3://test-bucket/ast_{i}.png", created_at=NOW)
            for i in range(6)
        ],
    )


def test_compiler_snapshot_exact_prompt():
    compiled = compile_image_prompt(make_scene(), "minimal_tech", make_profile())
    assert compiled.model == IMAGE_MODEL
    assert compiled.prompt == (
        "Photograph the exact product shown in the reference images — same shape, "
        "colors, materials, markings. Do not restyle or replace the product.\n"
        "Action: water spirals over grounds\n"
        "Camera: slow push-in\n"
        "Lighting: soft window light\n"
        "Environment: seamless backdrops, matte surfaces, negative space\n"
        "Palette: clean whites, cool greys, one restrained accent color\n"
        "Texture: smooth, precise, engineered surfaces\n"
        "Vertical 9:16 composition with headroom for captions. "
        "Photorealistic, no text, no logos, no watermarks, no people's faces in focus."
    )
    # deterministic hash; identity delta only (no product description in prompt)
    again = compile_image_prompt(make_scene(), "minimal_tech", make_profile())
    assert compiled.prompt_hash == again.prompt_hash
    assert "Ceramic" not in compiled.prompt and "BrewLine" not in compiled.prompt
    # reference cap
    assert len(compiled.reference_asset_ids) == 4


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


@pytest.fixture
def eager_worker(sqlite_session, fake_storage, monkeypatch):
    """Run the Celery task body synchronously against the test session."""
    def fake_delay(project_id, generation_id):
        return image_worker.run_generation(
            sqlite_session, fake_storage, project_id, generation_id)
    monkeypatch.setattr(image_worker.generate_scene_image, "delay", fake_delay)


@pytest.fixture
def good_provider(monkeypatch):
    monkeypatch.setattr(openai_images, "generate_image",
                        lambda *a, **k: (tiny_png(), 11))


@pytest.fixture
def bad_provider(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("deliberately bad prompt")
    monkeypatch.setattr(openai_images, "generate_image", boom)


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
