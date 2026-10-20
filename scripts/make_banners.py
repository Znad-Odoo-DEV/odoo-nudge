"""Draw the banner, large banner and icon of nudge_mcp, nudge_website, nudge_builder and nudge_messaging.

Same palette and layout as the banners of the other Nudge modules (violet
gradient, dark rounded tile on the left, white title and light subtitle on the
right), with a simple glyph in the amber of the mascot instead of the cursor.

    python scripts/make_banners.py addons [module ...]
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

TOP, BOTTOM = (123, 85, 235), (100, 70, 200)
TILE, AMBER, WHITE, SOFT = (24, 25, 30), (255, 181, 72), (255, 255, 255), (232, 224, 255)
FONTS = "C:/Windows/Fonts/"
BANNERS = {
    "nudge_mcp": ("Nudge: External Assistants", "Claude Desktop and ChatGPT use", "Nudge as you, with your rights", "plug"),
    "nudge_website": ("Nudge: Website Assistant", "A chat bubble on the website", "that answers from what is public", "bubble"),
    "nudge_builder": ("Nudge: Website Builder", "Six questions, three designs,", "a website of draft pages", "site"),
    "nudge_messaging": ("Nudge: WhatsApp and Telegram", "Ask from your phone;", "you say yes inside Odoo", "phone"),
}


def font(bold, size):
    for name in (("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf")):
        try:
            return ImageFont.truetype(FONTS + name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def glyph(draw, kind, box):
    """Draw the glyph inside the square box (x0, y0, x1, y1), in amber."""
    x0, y0, x1, y1 = box
    s = x1 - x0
    w = max(2, int(s * 0.05))
    if kind == "plug":
        # Three nodes joined by lines: an assistant on the left, Odoo on the right
        pts = [(x0 + s * .25, y0 + s * .30), (x0 + s * .25, y0 + s * .70), (x0 + s * .75, y0 + s * .50)]
        for a in pts[:2]:
            draw.line([a, pts[2]], fill=AMBER, width=w)
        r = s * .09
        for i, (cx, cy) in enumerate(pts):
            rr = r * 1.5 if i == 2 else r
            draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=AMBER)
    elif kind == "site":
        # A browser window: a bar with three dots, a wide hero block and three small blocks
        bx0, by0, bx1, by1 = x0 + s * .18, y0 + s * .22, x0 + s * .82, y0 + s * .78
        draw.rounded_rectangle([bx0, by0, bx1, by1], radius=s * .07, outline=AMBER, width=w)
        draw.line([(bx0, by0 + s * .11), (bx1, by0 + s * .11)], fill=AMBER, width=w)
        for i in range(3):
            cx = bx0 + s * (.06 + .05 * i)
            draw.ellipse([cx - s * .014, by0 + s * .055 - s * .014, cx + s * .014, by0 + s * .055 + s * .014], fill=AMBER)
        draw.rectangle([bx0 + s * .05, by0 + s * .16, bx1 - s * .05, by0 + s * .30], fill=AMBER)
        for i in range(3):
            left = bx0 + s * .05 + i * s * .185
            draw.rectangle([left, by0 + s * .35, left + s * .15, by1 - s * .05], fill=AMBER)
    elif kind == "phone":
        # A phone with two message lines, one from the person, one from Nudge
        bx0, by0, bx1, by1 = x0 + s * .30, y0 + s * .17, x0 + s * .70, y0 + s * .83
        draw.rounded_rectangle([bx0, by0, bx1, by1], radius=s * .07, outline=AMBER, width=w)
        draw.line([(bx0 + s * .14, by0 + s * .05), (bx1 - s * .14, by0 + s * .05)], fill=AMBER, width=w)
        draw.rounded_rectangle([bx0 + s * .05, by0 + s * .14, bx1 - s * .12, by0 + s * .26], radius=s * .04, fill=AMBER)
        draw.rounded_rectangle([bx0 + s * .12, by0 + s * .31, bx1 - s * .05, by0 + s * .43], radius=s * .04, fill=SOFT)
        draw.rounded_rectangle([bx0 + s * .05, by0 + s * .48, bx1 - s * .17, by0 + s * .60], radius=s * .04, fill=AMBER)
        draw.ellipse([bx0 + s * .15, by1 - s * .075, bx0 + s * .25, by1 - s * .025], outline=AMBER, width=max(1, w // 2))
    else:
        # A speech bubble with three dots
        bx0, by0, bx1, by1 = x0 + s * .20, y0 + s * .24, x0 + s * .80, y0 + s * .66
        draw.rounded_rectangle([bx0, by0, bx1, by1], radius=s * .12, fill=AMBER)
        draw.polygon([(x0 + s * .34, by1 - 1), (x0 + s * .34, y0 + s * .80), (x0 + s * .48, by1 - 1)], fill=AMBER)
        for i in range(3):
            cx = x0 + s * (.36 + .14 * i)
            cy = (by0 + by1) / 2
            draw.ellipse([cx - s * .035, cy - s * .035, cx + s * .035, cy + s * .035], fill=TILE)
    # Sparkle, as on the mascot
    cx, cy, k = x1 - s * .13, y0 + s * .13, s * .07
    draw.polygon([(cx, cy - k), (cx + k * .3, cy - k * .3), (cx + k, cy), (cx + k * .3, cy + k * .3),
                  (cx, cy + k), (cx - k * .3, cy + k * .3), (cx - k, cy), (cx - k * .3, cy - k * .3)], fill=WHITE)


def tile(img, kind, x, y, size):
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([x, y, x + size, y + size], radius=size * .22, fill=TILE)
    glyph(d, kind, (x, y, x + size, y + size))


def banner(title, line1, line2, kind, width, height):
    k = 4  # drawn four times larger, then reduced: smooth edges
    W, H = width * k, height * k
    img = Image.new("RGB", (W, H))
    px = ImageDraw.Draw(img)
    for row in range(H):
        t = row / (H - 1)
        px.line([(0, row), (W, row)], fill=tuple(int(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))
    unit = H / 280.0
    size = int(150 * unit)
    tile(img, kind, int(36 * unit), int((H - size) / 2), size)
    d = ImageDraw.Draw(img)
    x = int(214 * unit)
    room = W - x - int(20 * unit)

    def fit(text, bold, px):
        f = font(bold, int(px * unit))
        while d.textlength(text, font=f) > room and px > 8:
            px -= 1
            f = font(bold, int(px * unit))
        return f

    d.text((x, int(86 * unit)), title, font=fit(title, True, 30), fill=WHITE)
    d.text((x, int(130 * unit)), line1, font=fit(line1, False, 17), fill=SOFT)
    d.text((x, int(156 * unit)), line2, font=fit(line2, False, 17), fill=SOFT)
    return img.resize((width, height), Image.LANCZOS)


def icon(kind):
    img = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    tile(img, kind, 0, 0, 1024)
    return img.resize((256, 256), Image.LANCZOS)


def main(addons, only=()):
    for module, (title, line1, line2, kind) in BANNERS.items():
        if only and module not in only:
            continue
        folder = os.path.join(addons, module, "static", "description")
        banner(title, line1, line2, kind, 560, 280).save(os.path.join(folder, "banner.png"), optimize=True)
        banner(title, line1, line2, kind, 1200, 600).save(os.path.join(folder, "banner_large.png"), optimize=True)
        icon(kind).save(os.path.join(folder, "icon.png"), optimize=True)
        print("drawn", module)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
