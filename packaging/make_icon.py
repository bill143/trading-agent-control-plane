"""Render the application icon (same design as app/web/icon.svg) to packaging/icon.ico."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

SIZES = (16, 24, 32, 48, 64, 128, 256)
BLUE, WHITE, GREEN = (11, 107, 203, 255), (255, 255, 255, 255), (63, 196, 127, 255)


def draw(size: int) -> Image.Image:
    s = size / 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=14 * s, fill=BLUE)
    pts = [(12 * s, 44 * s), (24 * s, 30 * s), (33 * s, 37 * s), (50 * s, 18 * s)]
    d.line(pts, fill=WHITE, width=max(1, round(6 * s)), joint="curve")
    r = 5 * s
    d.ellipse((50 * s - r, 18 * s - r, 50 * s + r, 18 * s + r), fill=GREEN)
    d.rounded_rectangle((12 * s, 49 * s, 52 * s, 53 * s), radius=2 * s, fill=(255, 255, 255, 140))
    return img


def main() -> None:
    out = Path(__file__).with_name("icon.ico")
    big = draw(256)
    big.save(out, sizes=[(n, n) for n in SIZES], append_images=[draw(n) for n in SIZES[:-1]])
    print(out)


if __name__ == "__main__":
    main()
