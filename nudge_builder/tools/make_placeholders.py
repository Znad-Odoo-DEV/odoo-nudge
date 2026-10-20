"""Draws the 12 placeholder pictures of static/img/placeholders (run by hand, with PIL).

Every picture is drawn by this script from shapes and gradients, so there is
no photograph and no third party author: they are CC0 (see LICENSE next to them).

    python make_placeholders.py <output folder>
"""
import os
import random
import sys

from PIL import Image, ImageDraw, ImageFilter

WIDTH, HEIGHT = 1600, 900
THEMES = {
    "building": ((38, 70, 110), (214, 170, 80)),
    "interior": ((120, 96, 80), (236, 222, 200)),
    "office": ((40, 90, 120), (190, 220, 235)),
    "team": ((90, 70, 130), (240, 190, 150)),
    "workshop": ((70, 70, 78), (230, 140, 50)),
    "product": ((60, 60, 70), (230, 230, 235)),
    "food": ((150, 60, 40), (245, 200, 90)),
    "fashion": ((130, 50, 90), (245, 215, 225)),
    "tech": ((20, 40, 90), (80, 200, 230)),
    "clinic": ((30, 120, 130), (225, 245, 245)),
    "school": ((40, 100, 70), (250, 215, 100)),
    "abstract": ((70, 60, 120), (120, 190, 220)),
}


def gradient(top, bottom):
    image = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(image)
    for y in range(HEIGHT):
        ratio = y / HEIGHT
        draw.line([(0, y), (WIDTH, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3)))
    return image


def shade(color, factor):
    return tuple(max(0, min(255, int(value * factor))) for value in color)


def draw_key(key, draw, rng, dark, light):
    if key in ("building", "tech", "office"):
        x = 0
        while x < WIDTH:
            width = rng.randint(90, 220)
            height = rng.randint(220, 640)
            draw.rectangle([x, HEIGHT - height, x + width, HEIGHT], fill=shade(dark, rng.uniform(0.5, 0.9)))
            for wx in range(x + 14, x + width - 20, 38):
                for wy in range(HEIGHT - height + 20, HEIGHT - 30, 52):
                    if rng.random() < 0.55:
                        draw.rectangle([wx, wy, wx + 18, wy + 26], fill=light)
            x += width + rng.randint(6, 30)
    elif key in ("interior", "clinic", "school"):
        for index in range(3):
            left = 160 + index * 460
            draw.rectangle([left, 140, left + 340, 620], fill=shade(light, 0.95 - index * 0.05))
            draw.line([(left + 170, 140), (left + 170, 620)], fill=dark, width=8)
            draw.line([(left, 380), (left + 340, 380)], fill=dark, width=8)
        draw.rectangle([0, 700, WIDTH, HEIGHT], fill=shade(dark, 0.8))
    elif key in ("workshop", "product", "fashion", "food"):
        for index in range(7):
            cx, cy = rng.randint(150, WIDTH - 150), rng.randint(200, HEIGHT - 150)
            radius = rng.randint(60, 170)
            draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=shade(light, rng.uniform(0.7, 1.0)))
            draw.ellipse([cx - radius // 2, cy - radius // 2, cx + radius // 2, cy + radius // 2], fill=shade(dark, 0.9))
    elif key == "team":
        for index in range(5):
            cx = 200 + index * 300
            draw.ellipse([cx - 70, 260, cx + 70, 400], fill=shade(light, 0.9))
            draw.ellipse([cx - 120, 420, cx + 120, 900], fill=shade(light, 0.75))
    else:
        for index in range(14):
            cx, cy = rng.randint(0, WIDTH), rng.randint(0, HEIGHT)
            radius = rng.randint(80, 320)
            draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], outline=light, width=rng.randint(4, 14))


def make(key, folder):
    rng = random.Random(key)
    dark, light = THEMES[key]
    image = gradient(shade(dark, 0.75), shade(dark, 1.15))
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw_key(key, ImageDraw.Draw(overlay), rng, dark, light)
    image = Image.alpha_composite(image.convert("RGBA"), overlay.filter(ImageFilter.GaussianBlur(1.2))).convert("RGB")
    image.save(os.path.join(folder, f"{key}.jpg"), quality=78, optimize=True)


if __name__ == "__main__":
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    for name in THEMES:
        make(name, out)
