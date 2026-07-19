"""Simple exact-product compositing for product-lock STRICT scenes."""
import io

from PIL import Image, ImageFilter


def _open_rgba(raw: bytes) -> Image.Image:
    return Image.open(io.BytesIO(raw)).convert("RGBA")


def _content_bbox(img: Image.Image) -> tuple[int, int, int, int]:
    alpha = img.getchannel("A")
    return alpha.getbbox() or (0, 0, img.width, img.height)


def composite_exact_product(background_png: bytes, cutout_png: bytes) -> bytes:
    """Overlay the uploaded product cutout onto a generated scene.

    The generated image supplies environment/lighting; the cutout supplies the
    product identity. Placement is intentionally conservative for hero/static
    product scenes where exact identity matters more than complex interaction.
    """
    bg = _open_rgba(background_png)
    cutout_src = _open_rgba(cutout_png)
    cutout = cutout_src.crop(_content_bbox(cutout_src))

    max_w = int(bg.width * 0.72)
    max_h = int(bg.height * 0.62)
    scale = min(max_w / cutout.width, max_h / cutout.height, 1.0)
    target = (
        max(1, int(cutout.width * scale)),
        max(1, int(cutout.height * scale)),
    )
    cutout = cutout.resize(target, Image.Resampling.LANCZOS)

    x = (bg.width - cutout.width) // 2
    y = int(bg.height * 0.55 - cutout.height / 2)
    y = max(int(bg.height * 0.12), min(y, bg.height - cutout.height - int(bg.height * 0.08)))

    shadow_alpha = cutout.getchannel("A").filter(ImageFilter.GaussianBlur(20))
    shadow = Image.new("RGBA", cutout.size, (0, 0, 0, 92))
    shadow.putalpha(shadow_alpha)
    bg.alpha_composite(shadow, (x + int(bg.width * 0.012), y + int(bg.height * 0.014)))
    bg.alpha_composite(cutout, (x, y))

    out = io.BytesIO()
    bg.convert("RGB").save(out, format="PNG")
    return out.getvalue()
