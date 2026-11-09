#!/usr/bin/env python3
"""Generate the workshop wall textures and their Ogre material script.

Run from the repository root with the project venv:

    .venv/Scripts/python scripts/generate_workshop_textures.py
"""

from __future__ import annotations

import colorsys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "robot_collab_sim" / "materials"

MATERIALS = [
    ("Workshop/WallLaboratory", "wall_laboratory.png"),
    ("Workshop/WallFactory", "wall_factory.png"),
    ("Workshop/Calibration", "calibration_board.png"),
]


def checker(
    size: tuple[int, int],
    cell: int = 64,
    light: tuple[int, int, int] = (230, 230, 230),
    dark: tuple[int, int, int] = (25, 25, 25),
) -> Image.Image:
    image = Image.new("RGB", size, light)
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], cell):
        for x in range(0, size[0], cell):
            if (x // cell + y // cell) % 2 == 0:
                draw.rectangle([x, y, x + cell - 1, y + cell - 1], fill=dark)
    return image


def load_font(size: int):
    for name in ("arialbd.ttf", "DejaVuSans-Bold.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def gradient_text(image: Image.Image, text: str, top: int, size: int) -> None:
    """Draw text with a horizontal rainbow gradient, one hue per letter."""
    font = load_font(size)
    draw = ImageDraw.Draw(image)
    letters = list(text)
    letter_widths = [draw.textlength(ch, font=font) for ch in letters]
    x = (image.width - sum(letter_widths)) / 2
    for index, (char, advance) in enumerate(zip(letters, letter_widths, strict=True)):
        hue = index / max(1, len(letters) - 1)
        color = hsv_to_rgb(hue, 0.95, 0.98)
        draw.text((x, top), char, font=font, fill=color, stroke_width=4, stroke_fill=(0, 0, 0))
        x += advance


def logo_wall(text: str) -> Image.Image:
    image = checker((1600, 520), cell=52)
    band = Image.new("RGB", (1600, 260), (20, 20, 20))
    image.paste(band, (0, 130))
    gradient_text(image, text, top=140, size=200)
    return image


def calibration_board() -> Image.Image:
    image = Image.new("RGB", (1400, 1000), (245, 245, 245))
    draw = ImageDraw.Draw(image)
    margin = 70
    cols, rows = 9, 6
    cell = min((image.width - 2 * margin) // cols, (image.height - 2 * margin) // rows)
    origin_x = (image.width - cols * cell) // 2
    origin_y = (image.height - rows * cell) // 2
    for row in range(rows):
        for col in range(cols):
            if (row + col) % 2 == 0:
                x = origin_x + col * cell
                y = origin_y + row * cell
                draw.rectangle([x, y, x + cell - 1, y + cell - 1], fill=(15, 15, 15))
    return image


def write_material_script(out: Path) -> None:
    """Ogre material script: Gazebo Classic applies textures through it."""
    blocks = []
    for name, texture in MATERIALS:
        blocks.append(
            "material {name}\n"
            "{{\n"
            "  technique\n"
            "  {{\n"
            "    pass\n"
            "    {{\n"
            "      texture_unit\n"
            "      {{\n"
            "        texture {texture}\n"
            "      }}\n"
            "    }}\n"
            "  }}\n"
            "}}\n".format(name=name, texture=texture)
        )
    (out / "workshop.material").write_text("".join(blocks), encoding="ascii")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    logo_wall("laboratory").save(OUT / "wall_laboratory.png")
    logo_wall("factory").save(OUT / "wall_factory.png")
    calibration_board().save(OUT / "calibration_board.png")
    checker((512, 512), cell=64).save(OUT / "checker.png")
    write_material_script(OUT)
    print(f"wrote textures to {OUT}")


if __name__ == "__main__":
    main()
