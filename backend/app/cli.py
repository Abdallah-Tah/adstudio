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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
