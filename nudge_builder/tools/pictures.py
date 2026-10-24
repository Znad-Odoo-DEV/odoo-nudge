"""Colours from a logo, the bundled pictures, and the picture model.

The picture model is optional: a provider that draws (an OpenAI-compatible
images endpoint) named in the system parameter ``nudge_builder.image_model``.
Without it every picture is one of the twelve bundled placeholders.
"""
import base64
import colorsys
import io
import logging

import requests

from odoo.tools.misc import file_path

from .schema import PLACEHOLDERS

_logger = logging.getLogger(__name__)

IMAGE_MODEL_PARAM = "nudge_builder.image_model"
DEFAULT_PALETTE = {
    "primary": "#1f3a5f", "secondary": "#c9a227", "accent": "#e07a1f", "light": "#f6f4ef", "dark": "#14213d",
}


def to_hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(v))) for v in rgb)


def to_rgb(color):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def mix(color, other, ratio):
    """:return: ``color`` moved ``ratio`` of the way to ``other``, as hex"""
    a, b = to_rgb(color), to_rgb(other)
    return to_hex(tuple(a[i] + (b[i] - a[i]) * ratio for i in range(3)))


def dominant_colors(data, count=3):
    """The colours that stand out in a logo: not white, not black, not grey.

    :param data: the image bytes
    :return: up to ``count`` hex colours, the most used first; [] when the image cannot be read"""
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(data)).convert("RGBA")
        image.thumbnail((96, 96))
        pixels = [px[:3] for px in image.getdata() if px[3] > 200]
        if not pixels:
            return []
        sample = Image.new("RGB", (len(pixels), 1))
        sample.putdata(pixels)
        quantized = sample.quantize(colors=8, method=Image.Quantize.MEDIANCUT)
        palette = quantized.getpalette() or []
        counts = sorted(quantized.getcolors() or [], reverse=True)
    except Exception:  # noqa: BLE001 - a logo that cannot be read is not an error
        _logger.info("Nudge Builder: the logo could not be read", exc_info=True)
        return []
    found = []
    for _times, index in counts:
        rgb = tuple(palette[index * 3:index * 3 + 3])
        hue, light, saturation = colorsys.rgb_to_hls(*(v / 255 for v in rgb))
        if saturation < 0.25 or light > 0.9 or light < 0.12:
            continue
        if any(abs(hue - other) < 0.06 for other in found_hues(found)):
            continue
        found.append(to_hex(rgb))
        if len(found) == count:
            break
    return found


def found_hues(colors):
    return [colorsys.rgb_to_hls(*(v / 255 for v in to_rgb(color)))[0] for color in colors]


def palette_from(colors, fallback=None):
    """:param colors: the colours of the logo
    :return: the five colours of a theme made from them"""
    base = dict(fallback or DEFAULT_PALETTE)
    if not colors:
        return base
    primary = colors[0]
    secondary = colors[1] if len(colors) > 1 else mix(primary, "#c9a227", 0.55)
    accent = colors[2] if len(colors) > 2 else mix(secondary, "#ffffff", 0.2)
    return {
        "primary": primary,
        "secondary": secondary,
        "accent": accent,
        "light": mix(primary, "#ffffff", 0.94),
        "dark": mix(primary, "#000000", 0.62),
    }


def placeholder_path(key):
    key = key if key in PLACEHOLDERS else "abstract"
    return file_path(f"nudge_builder/static/img/placeholders/{key}.jpg")


def placeholder_bytes(key):
    with open(placeholder_path(key), "rb") as handle:
        return handle.read()


def draw_picture(provider, model, prompt, timeout=60):
    """One picture from the images endpoint of an OpenAI-compatible provider.

    :return: the image bytes, or None when the provider cannot draw or failed"""
    if provider.kind != "openai_compatible" or not provider.base_url or not model:
        return None
    provider._check_private()
    try:
        response = requests.post(
            provider.base_url.rstrip("/") + "/images/generations",
            json={"model": model, "prompt": prompt[:900], "n": 1, "size": "1536x1024", "response_format": "b64_json"},
            headers={"Authorization": f"Bearer {provider.api_key or ''}"}, timeout=timeout)
        response.raise_for_status()
        entry = response.json()["data"][0]
        if entry.get("b64_json"):
            return base64.b64decode(entry["b64_json"])
        if entry.get("url"):
            provider.env["nudge.egress"].check(entry["url"], "picture")  # NP-15: it lives on another host
            fetched = requests.get(entry["url"], timeout=timeout)
            fetched.raise_for_status()
            return fetched.content
    except Exception:  # noqa: BLE001 - the placeholder takes over
        _logger.info("Nudge Builder: the picture model gave no picture", exc_info=True)
    return None
