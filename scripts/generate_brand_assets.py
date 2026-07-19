"""Generate the multi-resolution Windows icon from the approved orbital mark."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"


def orbital_mark(size: int = 1024) -> Image.Image:
    scale = size / 128
    image = Image.new("RGBA", (size, size), (3, 3, 3, 255))
    draw = ImageDraw.Draw(image)
    white = (241, 240, 234, 255)

    def ellipse(box, width):
        draw.ellipse(tuple(int(v * scale) for v in box), outline=white, width=max(1, int(width * scale)))

    ellipse((26, 26, 102, 102), 6)
    ellipse((13, 13, 115, 115), 1.5)

    for angle in (33, -33):
        layer = Image.new("RGBA", image.size)
        orbit = ImageDraw.Draw(layer)
        orbit.ellipse(
            tuple(int(v * scale) for v in (8, 42, 120, 86)),
            outline=white,
            width=max(1, int(2 * scale)),
        )
        layer = layer.rotate(angle, center=(size // 2, size // 2), resample=Image.Resampling.BICUBIC)
        image.alpha_composite(layer)

    draw = ImageDraw.Draw(image)
    for x1, y1, x2, y2 in ((64, 7, 64, 19), (64, 109, 64, 121), (7, 64, 19, 64), (109, 64, 121, 64)):
        draw.line(tuple(int(v * scale) for v in (x1, y1, x2, y2)), fill=white, width=max(1, int(2 * scale)))
    ellipse((56, 56, 72, 72), 8)
    node_x, node_y = 99.5 * scale, 34.5 * scale
    node_r = 4.5 * scale
    draw.ellipse(
        (int(node_x - node_r), int(node_y - node_r), int(node_x + node_r), int(node_y + node_r)),
        fill=(3, 3, 3, 255),
        outline=white,
        width=max(1, int(2.5 * scale)),
    )
    return image


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    master = orbital_mark()
    master.save(ASSETS / "occhialini.png")
    master.save(
        ASSETS / "occhialini.ico",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(ASSETS / "occhialini.ico")


if __name__ == "__main__":
    main()
