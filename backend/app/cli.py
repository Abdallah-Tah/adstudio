"""CLI: python -m app.cli create ./photos/ "description..." --audience ... --style ...

Runs stages 1-4 directly (same pipeline as the API), persists to the DB,
pretty-prints the Project, and writes project.json.
"""
import argparse
import json
import sys
from pathlib import Path

from app import db
from app.pipeline import create_project
from app.stages.brief import UserInputs
from app.storage import Storage
from app.styles import STYLES

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def cmd_create(args: argparse.Namespace) -> int:
    photo_dir = Path(args.photos)
    photos = sorted(
        p for p in photo_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS
    ) if photo_dir.is_dir() else []
    if not photos:
        print(f"no images found in {photo_dir}", file=sys.stderr)
        return 1

    user = UserInputs(
        audience=args.audience, offer=args.offer, cta=args.cta,
        tone=args.tone, style=args.style, target_duration_s=args.duration,
    )
    engine = db.make_engine()
    db.init_db(engine)
    session = db.make_session_factory(engine)()
    try:
        project = create_project(
            session, Storage(),
            [(p.name, p.read_bytes()) for p in photos],
            args.description, user, actor="cli",
        )
    finally:
        session.close()

    doc = project.model_dump(mode="json")
    print(json.dumps(doc, indent=2))
    out = Path(args.out)
    out.write_text(json.dumps(doc, indent=2))

    total = project.cost.total
    print(f"\nproject_id: {project.project_id}", file=sys.stderr)
    print(f"scenes: {len(project.scenes)}  "
          f"duration: {sum(s.duration_s for s in project.scenes):.1f}s", file=sys.stderr)
    print(f"cost: analysis {project.cost.analysis}¢ + strategy {project.cost.strategy}¢ "
          f"= {total}¢ (${total / 100:.2f})", file=sys.stderr)
    print(f"wrote {out}", file=sys.stderr)
    return 0


def cmd_contact_sheet(args: argparse.Namespace) -> int:
    """Grid of each scene's selected (or latest succeeded) image, for the
    Gate 2 identity-consistency review."""
    import io

    from PIL import Image, ImageDraw

    from app.schema import Project

    engine = db.make_engine()
    session = db.make_session_factory(engine)()
    storage = Storage()
    try:
        row = session.get(db.ProjectRow, args.project_id)
        if row is None:
            print(f"project {args.project_id} not found", file=sys.stderr)
            return 1
        project = Project.model_validate(row.data)
    finally:
        session.close()

    thumb_w, thumb_h, pad = 270, 480, 12
    cols = min(5, len(project.scenes))
    rows = -(-len(project.scenes) // cols)
    sheet = Image.new("RGB", (cols * (thumb_w + pad) + pad,
                              rows * (thumb_h + pad + 24) + pad), "white")
    draw = ImageDraw.Draw(sheet)
    for i, scene in enumerate(project.scenes):
        gen = next((g for g in scene.generations
                    if g.generation_id == scene.selected_image), None)
        if gen is None:
            gen = next((g for g in reversed(scene.generations)
                        if g.status == "succeeded"), None)
        x = pad + (i % cols) * (thumb_w + pad)
        y = pad + (i // cols) * (thumb_h + pad + 24)
        if gen and gen.asset:
            img = Image.open(io.BytesIO(storage.get_bytes(gen.asset.uri)))
            img.thumbnail((thumb_w, thumb_h))
            sheet.paste(img, (x, y))
        else:
            draw.rectangle([x, y, x + thumb_w, y + thumb_h], outline="grey")
            draw.text((x + 8, y + 8), "no image", fill="grey")
        draw.text((x, y + thumb_h + 6),
                  f"scene {scene.order} · {scene.duration_s}s", fill="black")
    out = Path(args.out or f"contact_sheet_{args.project_id}.png")
    sheet.save(out)
    print(f"wrote {out}", file=sys.stderr)
    return 0


def cmd_music_add(args: argparse.Namespace) -> int:
    """Ingest a licensed track + its license evidence into the music library.
    A track cannot enter the library without license fields (hard rule)."""
    import subprocess
    import uuid
    from datetime import datetime, timezone

    from app.stages import music

    audio = Path(args.file)
    if not audio.is_file():
        print(f"{audio} not found", file=sys.stderr)
        return 1
    probe = subprocess.run(
        ["/usr/bin/ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(audio)], capture_output=True, text=True)
    if probe.returncode != 0:
        print(f"ffprobe failed: {probe.stderr}", file=sys.stderr)
        return 1
    duration = float(probe.stdout.strip())

    storage = Storage()
    storage.ensure_bucket()
    track_id = f"trk_{uuid.uuid4().hex[:12]}"
    audio_key = f"music-library/{track_id}{audio.suffix.lower()}"
    storage.put_bytes(audio.read_bytes(), audio_key, "audio/mpeg")

    evidence_asset_id = None
    if args.evidence:
        evidence_asset_id = f"ast_{uuid.uuid4().hex[:12]}"
        ev = Path(args.evidence)
        storage.put_bytes(ev.read_bytes(),
                          f"music-library/evidence/{evidence_asset_id}{ev.suffix}",
                          "application/octet-stream")

    tracks = music.load_library(storage)
    tracks.append(music.TrackEntry(
        track_id=track_id, title=args.title or audio.stem,
        provider=args.provider, license_id=args.license_id,
        license_type=args.license_type, source_url=args.source_url,
        acquired_at=datetime.now(timezone.utc).isoformat(),
        valid_for_commercial_ads=True,
        evidence_asset_id=evidence_asset_id,
        duration_s=duration, moods=args.moods or [], audio_key=audio_key,
    ))
    music.save_library(storage, tracks)
    print(f"{track_id}  {duration:.1f}s  provider={args.provider} "
          f"license={args.license_id}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="photos + description -> storyboarded Project")
    create.add_argument("photos", help="directory of product photos")
    create.add_argument("description", help="product description")
    create.add_argument("--audience")
    create.add_argument("--offer")
    create.add_argument("--cta")
    create.add_argument("--tone")
    create.add_argument("--style", choices=sorted(STYLES))
    create.add_argument("--duration", type=float, help="target duration seconds (10-60)")
    create.add_argument("--out", default="project.json")
    create.set_defaults(func=cmd_create)

    sheet = sub.add_parser("contact-sheet",
                           help="grid of generated images per scene (Gate 2 review)")
    sheet.add_argument("project_id")
    sheet.add_argument("--out")
    sheet.set_defaults(func=cmd_contact_sheet)

    madd = sub.add_parser("music-add",
                          help="ingest a licensed track into the music library")
    madd.add_argument("file", help="audio file (mp3)")
    madd.add_argument("--provider", required=True,
                      help="catalog/storefront the license was purchased from")
    madd.add_argument("--license-id", required=True, dest="license_id")
    madd.add_argument("--license-type", default="royalty_free_commercial",
                      dest="license_type")
    madd.add_argument("--source-url", dest="source_url")
    madd.add_argument("--title")
    madd.add_argument("--moods", nargs="*",
                      help="e.g. upbeat warm cinematic minimal")
    madd.add_argument("--evidence", help="receipt/license PDF to store alongside")
    madd.set_defaults(func=cmd_music_add)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
