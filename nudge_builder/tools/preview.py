"""The three design directions, drawn side by side before anything is built.

Each direction is drawn from the home page's own words and the direction's
palette and fonts. When wkhtmltoimage is installed the card is a picture of
that drawing; otherwise it is the drawing itself, in HTML.
"""
import base64
import logging
import os
import subprocess
import tempfile
from html import escape

from odoo.tools.misc import find_in_path

from . import pictures

_logger = logging.getLogger(__name__)

HEROES = ("s_cover", "s_banner", "s_text_image")

FONT_PAIRS_LATIN = (("Playfair Display", "Lato"), ("Montserrat", "Open Sans"), ("Poppins", "Inter"))
FONT_PAIRS_ARABIC = (("Cairo", "Cairo"), ("Tajawal", "Tajawal"), ("Almarai", "Almarai"))


def derive_directions(palette, fonts, arabic=False):
    """Three directions from one palette and font pair: the template's own,
    a bold one, a soft one.

    :return: list of ``{key, name, palette, fonts, hero}``; names are translatable keys, see the model"""
    pairs = FONT_PAIRS_ARABIC if arabic else FONT_PAIRS_LATIN
    bold = dict(palette, primary=palette["dark"], secondary=palette["primary"],
                accent=palette["accent"], dark=pictures.mix(palette["dark"], "#000000", 0.4))
    soft = dict(palette, primary=pictures.mix(palette["primary"], "#ffffff", 0.18),
                secondary=pictures.mix(palette["secondary"], "#ffffff", 0.2),
                light=pictures.mix(palette["primary"], "#ffffff", 0.96),
                dark=pictures.mix(palette["dark"], "#ffffff", 0.2))
    return [
        {"key": "a", "palette": dict(palette), "fonts": dict(fonts), "hero": HEROES[0]},
        {"key": "b", "palette": bold, "fonts": {"heading": pairs[1][0], "body": pairs[1][1]}, "hero": HEROES[1]},
        {"key": "c", "palette": soft, "fonts": {"heading": pairs[2][0], "body": pairs[2][1]}, "hero": HEROES[2]},
    ]


def on_color(color):
    red, green, blue = pictures.to_rgb(color)
    return "#111111" if (red * 299 + green * 587 + blue * 114) / 1000 > 150 else "#ffffff"


def card_document(direction, title, text, button, features, rtl=False):
    """:return: the full HTML document of one direction's home page, as a drawing"""
    palette, fonts = direction["palette"], direction["fonts"]
    heads = "".join(
        f'<div style="flex:1;background:#fff;border-top:4px solid {palette["secondary"]};padding:10px;'
        f'font-size:12px;color:{palette["dark"]}"><b>{escape(item)}</b></div>'
        for item in features[:3])
    return (
        f'<!doctype html><html dir="{"rtl" if rtl else "ltr"}"><meta charset="utf-8"><body '
        f'style="margin:0;width:360px;background:{palette["light"]};font-family:{fonts["body"]},sans-serif">'
        f'<div style="background:{palette["dark"]};color:{on_color(palette["dark"])};padding:10px 14px;'
        f'font-family:{fonts["heading"]},sans-serif;font-size:13px">&#9679; &#9679; &#9679;</div>'
        f'<div style="background:{palette["primary"]};color:{on_color(palette["primary"])};padding:34px 18px">'
        f'<div style="font-family:{fonts["heading"]},serif;font-size:22px;font-weight:700">{escape(title)}</div>'
        f'<p style="font-size:12px;margin:8px 0 14px">{escape(text)}</p>'
        f'<span style="background:{palette["accent"]};color:{on_color(palette["accent"])};padding:6px 14px;'
        f'font-size:12px;border-radius:3px">{escape(button)}</span></div>'
        f'<div style="display:flex;gap:8px;padding:14px">{heads}</div>'
        f'<div style="background:{palette["dark"]};height:34px"></div></body></html>'
    )


def png_of(document):
    """:return: the PNG bytes of an HTML document, or None when wkhtmltoimage is not installed"""
    try:
        binary = find_in_path("wkhtmltoimage")
    except IOError:
        return None
    path = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as handle:
            handle.write(document)
            path = handle.name
        out = path + ".png"
        subprocess.run([binary, "--quiet", "--width", "360", path, out], check=True, timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        with open(out, "rb") as picture:
            return picture.read()
    except Exception:  # noqa: BLE001 - the HTML card stands in
        _logger.info("Nudge Builder: wkhtmltoimage could not draw the card", exc_info=True)
        return None
    finally:
        for name in (path, path and path + ".png"):
            if name and os.path.exists(name):
                os.unlink(name)


def card_html(direction, label, document):
    """:return: the card shown to the person: a picture when one can be made, else the drawing"""
    png = png_of(document)
    if png:
        body = f'<img alt="{escape(label)}" style="width:100%" src="data:image/png;base64,{base64.b64encode(png).decode()}"/>'
    else:
        body = (
            '<div style="width:100%;overflow:hidden">'
            f'<div style="zoom:.85;pointer-events:none">{_inline(document)}</div></div>')
    return (
        f'<div class="o_nudge_builder_card" data-direction="{direction["key"]}" '
        f'style="flex:1;min-width:240px;border:1px solid #ccd;border-radius:6px;overflow:hidden;background:#fff">'
        f'<div style="padding:8px 12px;font-weight:600">{escape(label)}</div>{body}</div>')


def _inline(document):
    """The drawing of a card without its html wrapper, to sit inside the page"""
    start = document.index("<body")
    style = document[start:document.index(">", start) + 1]
    inner = document[document.index(">", start) + 1:document.rindex("</body>")]
    return style.replace("<body", "<div") + inner + "</div>"


def cards_html(cards):
    return '<div style="display:flex;gap:14px;flex-wrap:wrap">' + "".join(cards) + "</div>"
