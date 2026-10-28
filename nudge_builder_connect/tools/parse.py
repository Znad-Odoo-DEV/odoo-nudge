"""Reading what the providers answer: pure functions, no record and no network."""
import re
from urllib.parse import parse_qs, urlparse

DRIVE_HOSTS = ("drive.google.com", "docs.google.com")
FOLDER_PATH = re.compile(r"/folders/([A-Za-z0-9_-]{10,})(?=/|$)")
FILE_ID = re.compile(r"[A-Za-z0-9_-]{10,}")
DAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")
STARS = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5}
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
INSTAGRAM_MEDIA_HOSTS = ("cdninstagram.com", "fbcdn.net", "instagram.com")
TAG = re.compile(r"<[^>]*>")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def drive_folder_id(link):
    """:return: the id of the folder a Drive link points to, or False when it is not a Drive folder link"""
    try:
        parsed = urlparse((link or "").strip())
    except ValueError:
        return False
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in DRIVE_HOSTS:
        return False
    found = FOLDER_PATH.search(parsed.path)
    if found:
        return found.group(1)
    wanted = parse_qs(parsed.query).get("id", [""])[0]
    if FILE_ID.fullmatch(wanted) and parsed.path.rstrip("/").endswith("/open"):
        return wanted
    return False


def valid_file_id(value):
    return bool(value) and isinstance(value, str) and bool(FILE_ID.fullmatch(value))


def instagram_media_url_ok(url):
    """:return: whether ``url`` is an https address on the picture hosts of Instagram"""
    try:
        parsed = urlparse(url or "")
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return (parsed.scheme == "https" and not parsed.username and not parsed.port
            and any(host == known or host.endswith("." + known) for known in INSTAGRAM_MEDIA_HOSTS))


def clean_text(value, limit):
    """:return: ``value`` as text without control characters, cut at ``limit``"""
    if not isinstance(value, str):
        return ""
    return CONTROL.sub("", value).strip()[:limit]


def first_name(display_name):
    """:return: the first word of the name of a reviewer: the website shows no more of a customer"""
    words = TAG.sub("", clean_text(display_name, 120)).replace("<", "").replace(">", "").split()
    return words[0][:40] if words else ""


def stars(value):
    """:return: 1 to 5 for the ``starRating`` of Google, 0 when it says none"""
    return STARS.get(str(value or "").upper(), 0)


def _time(value):
    if not isinstance(value, dict):
        return "00:00"
    try:
        return "%02d:%02d" % (int(value.get("hours") or 0), int(value.get("minutes") or 0))
    except (TypeError, ValueError):
        return "00:00"


def format_hours(regular_hours, day_names, closed_word):
    """:return: the weekly hours of a Business Profile as lines, one per day (``day_names``: the seven names,
    Monday first), or '' when it publishes none"""
    periods = (regular_hours or {}).get("periods") if isinstance(regular_hours, dict) else None
    if not isinstance(periods, list):
        return ""
    by_day = {}
    for period in periods:
        if not isinstance(period, dict) or period.get("openDay") not in DAYS:
            continue
        by_day.setdefault(period["openDay"], []).append(
            "%s-%s" % (_time(period.get("openTime")), _time(period.get("closeTime"))))
    if not by_day:
        return ""
    return "\n".join("%s: %s" % (day_names[i], ", ".join(by_day[day]) if day in by_day else closed_word)
                     for i, day in enumerate(DAYS))


def place_values(location, day_names, closed_word):
    """:return: the plain values of a Business Profile location, ready for a content location record
    (country and state are left to the caller, which has the database): name, street, city, zip, phone, hours,
    maps_url, latitude, longitude, region (the country code)"""
    address = location.get("storefrontAddress") if isinstance(location.get("storefrontAddress"), dict) else {}
    lines = [clean_text(line, 120) for line in (address.get("addressLines") or []) if isinstance(line, str)]
    phones = location.get("phoneNumbers") if isinstance(location.get("phoneNumbers"), dict) else {}
    meta = location.get("metadata") if isinstance(location.get("metadata"), dict) else {}
    point = location.get("latlng") if isinstance(location.get("latlng"), dict) else {}
    values = {
        "name": clean_text(location.get("title"), 120),
        "street": ", ".join(line for line in lines if line)[:200],
        "city": clean_text(address.get("locality"), 80),
        "zip": clean_text(address.get("postalCode"), 20),
        "phone": clean_text(phones.get("primaryPhone"), 40),
        "hours": format_hours(location.get("regularHours"), day_names, closed_word),
        "maps_url": clean_text(meta.get("mapsUri"), 300),
        "region": clean_text(address.get("regionCode"), 2).upper(),
        "state": clean_text(address.get("administrativeArea"), 80),
    }
    try:
        latitude, longitude = float(point["latitude"]), float(point["longitude"])
        if -90 <= latitude <= 90 and -180 <= longitude <= 180:
            values.update(latitude=latitude, longitude=longitude)
    except (KeyError, TypeError, ValueError):
        pass
    return {key: value for key, value in values.items() if value not in ("", None)}
