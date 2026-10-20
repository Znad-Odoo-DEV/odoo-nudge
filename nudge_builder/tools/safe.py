"""What the words of an AI provider may carry into a page of the website.

Text goes in as text; a link or a picture address goes in only when it is one of a few plain shapes.
Nothing from a provider may become a QWeb directive, a script, an event handler or a ``javascript:`` address."""
import re

URL = re.compile(
    r"^(?:/(?!/)[^\s\"'<>]*|https://[^\s\"'<>]+|mailto:[^\s\"'<>]+|tel:[+\d\s().-]{3,30}|#[\w-]+"
    r"|data:image/(?:png|jpeg|gif|webp);base64,[A-Za-z0-9+/=]+)$")
TAG = re.compile(r"</?[a-zA-Z!?][^>]*>?")
URL_KEYS = ("button_url", "image", "url", "href", "link", "src")
MAX_URL = 2000000


def safe_url(value, default=""):
    """:return: ``value`` when it is a plain link or picture address, else ``default``"""
    value = str(value or "").strip()
    return value if len(value) <= MAX_URL and URL.match(value) else default


def safe_text(value):
    """:return: ``value`` as plain words: no tags, no directive, no handler"""
    text = re.sub(r"<\s*(script|style)\b.*?(</\s*\1\s*>|$)", "", str(value), flags=re.IGNORECASE | re.DOTALL)
    text = TAG.sub("", text)
    text = re.sub(r"javascript:", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\{\{|\}\}|#\{|\$\{", "", text)
    text = re.sub(r"\b(t-[a-z-]+|on[a-z]+)\s*=", r"\1", text, flags=re.IGNORECASE)
    return text


def clean_content(value, key=""):
    """:return: a copy of the copy of a block with every text safe and every address checked"""
    if isinstance(value, dict):
        return {name: clean_content(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [clean_content(item, key) for item in value]
    if isinstance(value, str):
        return safe_url(value) if key in URL_KEYS else safe_text(value)
    return value
