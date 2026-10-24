"""The sections of a page, read as words: what a card shows before and after an edit.

A section is a direct child of the page's ``#wrap``. Nothing here writes; the HTML is only read, and what
comes out is plain text, so a card never renders what a person (or a page) typed as markup.
"""
import hashlib
import re

MAX_LINE = 200
MAX_LINES = 30
HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")


def children(wrap):
    """:return: the sections of a page: the elements directly inside ``#wrap``"""
    return [node for node in wrap if isinstance(node.tag, str)]


def snippet_of(section):
    """:return: the snippet a section came from: its ``data-snippet``, else its first ``s_`` class"""
    name = section.get("data-snippet")
    if name:
        return name
    return next((cls for cls in (section.get("class") or "").split() if cls.startswith("s_")), "section")


def keys_of(wrap):
    return [snippet_of(section) for section in children(wrap)]


def text_of(node):
    return "".join(node.itertext())


def _clean(text):
    text = " ".join((text or "").split())
    return text if len(text) <= MAX_LINE else text[:MAX_LINE - 1] + "…"


def describe(section):
    """:return: the section as plain lines: headings, paragraphs, buttons, list items, pictures"""
    lines = []
    for node in section.iter():
        tag = node.tag
        if not isinstance(tag, str):
            continue
        classes = (node.get("class") or "").split()
        if tag in HEADINGS:
            text = _clean(text_of(node))
            if text:
                lines.append("Heading: " + text)
        elif tag == "a" and "btn" in classes:
            lines.append("Button: %s (%s)" % (_clean(text_of(node)), node.get("href") or ""))
        elif tag == "p" and not node.xpath(".//a[contains(concat(' ', normalize-space(@class), ' '), ' btn ')]"):
            text = _clean(text_of(node))
            if text:
                lines.append("Text: " + text)
        elif tag == "li":
            text = _clean(text_of(node))
            if text:
                lines.append("Item: " + text)
        elif tag == "img":
            lines.append("Image: %s (%s)" % (_clean(node.get("src") or ""), _clean(node.get("alt") or "")))
        elif tag == "span" and "s_parallax_bg" in classes:
            found = re.search(r"url\(['\"]?([^)'\"]*)", node.get("style") or "")
            if found:
                lines.append("Background: " + _clean(found.group(1)))
    return "\n".join(lines[:MAX_LINES]) or "(no text)"


def order_text(keys):
    """:return: the order of the sections, as ``1. s_cover`` lines"""
    return "\n".join("%d. %s" % (number, key) for number, key in enumerate(keys, start=1)) or "(empty page)"


def digest(text):
    return hashlib.sha256((text or "").encode()).hexdigest()
