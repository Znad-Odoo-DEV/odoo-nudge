"""Reading a file the person sends with a question.

A PDF or a text file is read for its text, which goes to the model with the
question, inside the context block, as data. An image goes to the model as
an image, when the provider can look at one. Nothing is fetched, nothing is
run: a file is bytes to read, whatever it claims to be.
"""
import base64
import io
import logging

_logger = logging.getLogger(__name__)

MAX_BYTES = 10 * 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_TEXT_CHARS = 20_000
MAX_PAGES = 30
MAX_NAME = 120

PDF_TYPES = ("application/pdf", "application/x-pdf")
TEXT_TYPES = ("text/", "application/json", "application/csv", "application/xml")
IMAGE_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")
TEXT_EXTENSIONS = (".txt", ".csv", ".md", ".json", ".xml", ".tsv", ".log")


class Unreadable(Exception):
    """The file cannot be read; the message is for the person."""


def _cut(text, size=MAX_TEXT_CHARS):
    text = text.strip()
    if len(text) <= size:
        return text, False
    return text[:size].rstrip() + "\n[…]", True


def _pdf_pages(data):
    """:return: the pages of the PDF, whatever version of pypdf Odoo found"""
    from odoo.tools.pdf import OdooPdfFileReader  # noqa: PLC0415

    reader = OdooPdfFileReader(io.BytesIO(data), strict=False)
    if getattr(reader, "isEncrypted", False) or getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception as error:  # noqa: BLE001
            raise Unreadable("This PDF is protected by a password.") from error
    pages = getattr(reader, "pages", None)
    if pages is None:
        pages = [reader.getPage(index) for index in range(reader.getNumPages())]
    return pages


def _page_text(page):
    for name in ("extract_text", "extractText"):
        method = getattr(page, name, None)
        if method:
            return method() or ""
    return ""


def pdf_text(data):
    """:return: (text, pages, pages read)"""
    try:
        pages = _pdf_pages(data)
        count = len(pages)
        parts = []
        for page in list(pages)[:MAX_PAGES]:
            parts.append(_page_text(page))
        return "\n\n".join(parts), count, min(count, MAX_PAGES)
    except Unreadable:
        raise
    except Exception as error:  # noqa: BLE001
        _logger.info("Nudge: a PDF could not be read: %s", error)
        raise Unreadable("This PDF cannot be read.") from error


def pdf_page_texts(data, max_pages=400):
    """:return: list of (page number, text) of a PDF, for a file kept as a source"""
    try:
        pages = list(_pdf_pages(data))[:max_pages]
        return [(number, _page_text(page)) for number, page in enumerate(pages, start=1)]
    except Unreadable:
        raise
    except Exception as error:  # noqa: BLE001
        _logger.info("Nudge: a PDF could not be read: %s", error)
        raise Unreadable("This PDF cannot be read.") from error


def kind_of(name, mimetype):
    """:return: "pdf", "text", "image" or None"""
    lowered = (name or "").lower()
    mimetype = (mimetype or "").lower()
    if mimetype in PDF_TYPES or lowered.endswith(".pdf"):
        return "pdf"
    if mimetype in IMAGE_TYPES:
        return "image"
    if mimetype.startswith(TEXT_TYPES) or lowered.endswith(TEXT_EXTENSIONS):
        return "text"
    return None


def read_attachment(name, data, mimetype):
    """:param str name: as the person's browser named it
    :param bytes data: the file
    :param str mimetype: as the browser said; the content decides
    :return: {"name", "kind", "size", "text", "truncated", "pages", "note",
        "image": (media type, base64) or None}
    :raise Unreadable: with a message for the person"""
    name = " ".join((name or "file").split())[:MAX_NAME]
    if not data:
        raise Unreadable("The file is empty.")
    if len(data) > MAX_BYTES:
        raise Unreadable("The file is too large. The limit is %s MB." % (MAX_BYTES // (1024 * 1024)))
    kind = kind_of(name, mimetype)
    if kind is None:
        raise Unreadable("Only PDF, text, CSV and image files can be read.")
    found = {"name": name, "kind": kind, "size": len(data), "text": "", "truncated": False,
             "pages": 0, "note": "", "image": None}
    if kind == "pdf":
        text, pages, read = pdf_text(data)
        found["text"], found["truncated"] = _cut(text)
        found["pages"] = pages
        if not found["text"]:
            found["note"] = (
                "The PDF holds no text: it is scanned, an image of a document. Nothing of it can "
                "be read here. Ask the person for a photo or an image of it, or for the numbers "
                "and names it holds, typed.")
        elif read < pages:
            found["note"] = f"Only the first {read} of {pages} pages were read."
    elif kind == "text":
        found["text"], found["truncated"] = _cut(data.decode("utf-8", errors="replace"))
    else:
        if len(data) > MAX_IMAGE_BYTES:
            raise Unreadable("The image is too large. The limit is %s MB." % (MAX_IMAGE_BYTES // (1024 * 1024)))
        found["image"] = (mimetype.lower(), base64.b64encode(data).decode("ascii"))
        found["note"] = "An image: look at it, and read what it shows."
    return found


def render(found):
    """:return: the attachment as the context block carries it"""
    block = {"name": found["name"], "kind": found["kind"]}
    if found["pages"]:
        block["pages"] = found["pages"]
    if found["truncated"]:
        block["truncated"] = True
    if found["note"]:
        block["note"] = found["note"]
    return block
