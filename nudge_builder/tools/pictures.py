"""Colours from a logo, the bundled pictures, and the picture model.

The picture model is optional: a provider that draws (an OpenAI-compatible
images endpoint) named in the system parameter ``nudge_builder.image_model``.
Without it every picture is one of the twelve bundled placeholders.
"""
import base64
import colorsys
import io
import logging

from odoo.tools.misc import file_path

from odoo.addons.nudge.tools import pinned_http

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


def brand_card(logo, palette, size=(1600, 900)):
    """A neutral picture made of the company's own logo, centred on the light colour of its palette: for the
    places of a page where a picture is wanted and none of a fitting sector is at hand (no photograph of
    another sector, no drawing of a city).

    :param logo: the logo's bytes
    :param dict palette: the five colours of the site
    :return: PNG bytes, or None when the logo cannot be read"""
    try:
        from PIL import Image
        base = Image.new("RGB", size, palette.get("light") or DEFAULT_PALETTE["light"])
        image = Image.open(io.BytesIO(logo)).convert("RGBA")
        image.thumbnail((int(size[0] * 0.5), int(size[1] * 0.5)))
        base.paste(image, ((size[0] - image.width) // 2, (size[1] - image.height) // 2), image)
        out = io.BytesIO()
        base.save(out, "PNG")
        return out.getvalue()
    except Exception:  # noqa: BLE001 - a logo that cannot be read is not an error: the placeholder stands in
        _logger.info("Nudge Builder: the logo card could not be made", exc_info=True)
        return None


def draw_picture(provider, model, prompt, timeout=60):
    """One picture from the images endpoint of an OpenAI-compatible provider.

    :return: the image bytes, or None when the provider cannot draw or failed"""
    if provider.kind != "openai_compatible" or not provider.base_url or not model:
        return None
    provider._check_private()
    try:
        provider.env["nudge.egress"].check(provider.base_url, "picture")  # NP-15
        local = bool(provider.runs_locally or provider.looks_local)
        response = pinned_http.post(
            provider.base_url.rstrip("/") + "/images/generations", allow_private=local,
            json={"model": model, "prompt": prompt[:900], "n": 1, "size": "1536x1024", "response_format": "b64_json"},
            headers={"Authorization": f"Bearer {provider.api_key or ''}"}, timeout=timeout)
        response.raise_for_status()
        entry = response.json()["data"][0]
        if entry.get("b64_json"):
            return base64.b64decode(entry["b64_json"])
        if entry.get("url"):
            provider.env["nudge.egress"].check(entry["url"], "picture")  # NP-15: it lives on another host
            # Not followed: a redirect would go to a host the list was not asked about. A public address
            # only (S11), unless the provider is on our own network
            fetched = pinned_http.get(entry["url"], timeout=timeout, allow_redirects=False,
                                      allow_private=local)
            fetched.raise_for_status()
            return fetched.content
    except Exception:  # noqa: BLE001 - the placeholder takes over
        _logger.info("Nudge Builder: the picture model gave no picture", exc_info=True)
    return None


def web_image(data, width=1280, quality=85):
    """:return: the base64 of a picture resized to fit ``width`` x ``width`` (ratio kept) and re-encoded, which
    also drops the metadata of the upload (location, camera); the original bytes when it cannot be read.
    :param data: base64 text/bytes as an Image field holds it"""
    from odoo.tools import image_process
    raw = base64.b64decode(data)
    try:
        return base64.b64encode(image_process(raw, size=(width, width), quality=quality) or raw)
    except Exception:  # noqa: BLE001 - the upload as it is
        _logger.info("Nudge Builder: a picture could not be resized", exc_info=True)
        return base64.b64encode(raw)
