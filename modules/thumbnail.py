"""Thumbnail: dark graded still + big high-contrast title text (the biggest lever on click-through rate)."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

FONT_CANDIDATES = [
    "C:/Windows/Fonts/impact.ttf", "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def make_thumbnail(image: Path, text: str, out: Path) -> Path:
    W, H = 1280, 720
    im = Image.open(image).convert("RGB")
    s = max(W / im.width, H / im.height)
    im = im.resize((int(im.width * s) + 1, int(im.height * s) + 1), Image.LANCZOS)
    l, t = (im.width - W) // 2, (im.height - H) // 2
    im = im.crop((l, t, l + W, t + H))
    im = ImageEnhance.Contrast(im).enhance(1.25)
    im = ImageEnhance.Color(im).enhance(0.8)
    im = ImageEnhance.Brightness(im).enhance(0.85)

    # dark gradient from the left so text always reads
    grad = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(grad)
    for x in range(W):
        gd.line([(x, 0), (x, H)], fill=int(max(0, 215 * (1 - x / (W * 0.85)))))
    im = Image.composite(Image.new("RGB", (W, H), (0, 0, 0)), im, grad)

    words = text.upper().split()
    lines = [" ".join(words[: (len(words) + 1) // 2]), " ".join(words[(len(words) + 1) // 2:])] if len(words) > 2 else [" ".join(words)]
    lines = [l for l in lines if l]
    d = ImageDraw.Draw(im)
    size = 150
    while size > 60 and max(d.textlength(l, font=_font(size)) for l in lines) > W * 0.62:
        size -= 6
    f = _font(size)
    y = (H - size * 1.1 * len(lines)) / 2
    for i, line in enumerate(lines):
        colour = (255, 40, 40) if i == len(lines) - 1 else (255, 255, 255)
        d.text((60, y), line, font=f, fill=colour, stroke_width=8, stroke_fill=(0, 0, 0))
        y += size * 1.1
    im.save(out, "JPEG", quality=92)
    return out
