"""The footer (and the header's contact strip) of a website, made to show the company instead of Odoo's sample.

Odoo's footer layouts are templates of the layout view with the sample company written in them (MyCompany, a
street in Brisbane, +1 555-555-5556, info@yourcompany.com, links to Documentation and Marketplace). They are
read here as XML and only the words of the sample change; the structure stays what Odoo made. The caller writes
the result on the website's own copy of the view, so no other website changes.
"""
import re
from xml.sax.saxutils import escape

from lxml import etree

SAMPLE_NAME = re.compile(r"\bMy ?Company\b")
SAMPLE_EMAIL = re.compile(r"[\w.\-]+@(?:yourcompany|mycompany)\.[\w.]+", re.I)
SAMPLE_PHONE = re.compile(r"\+1 555-555-5556\"?")
SAMPLE_ABOUT = re.compile(r"^We are a team of passionate people", re.I)
SAMPLE_HEADLINES = ("Designed for companies",)
SAMPLE_HEADLINE_START = ("50,000+ companies run Odoo",)
SOCIAL = re.compile(r"/website/social/(\w+)")
DOT = "•"
MAX_LINKS = 4  # links a footer column of sample links is given
COPYRIGHT = "Copyright &amp;copy; Company name"


def is_sample(value):
    """:return: whether ``value`` is Odoo's sample company data (phone, email, street) and not a company's own"""
    value = value or ""
    return bool(SAMPLE_EMAIL.search(value) or "555-555-5556" in value
                or "Marina Blvd" in value)


def _text(node):
    return " ".join(node.xpath("string()").split())


def _remove(node):
    parent = node.getparent()
    if parent is not None:
        parent.remove(node)


def _remove_row(node):
    """Take a link out with the list item or paragraph it sits in"""
    for ancestor in node.iterancestors():
        if ancestor.tag in ("li", "p"):
            _remove(ancestor)
            return
        if ancestor.tag in ("ul", "div", "section"):
            break
    _remove(node)


def _sub_text(root, pattern, new):
    """Replace ``pattern`` in the text and the tail of every element, and in the attributes that are words"""
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        if node.text:
            node.text = pattern.sub(lambda _m: new, node.text)
        if node.tail:
            node.tail = pattern.sub(lambda _m: new, node.tail)
        for key in ("aria-label", "title", "alt"):
            if node.get(key) and pattern.search(node.get(key)):
                node.set(key, pattern.sub(lambda _m: new, node.get(key)))


def _lines(element, lines, bold=None):
    """Write ``lines`` one under the other, or on one line with a dot where Odoo's sample did"""
    inline = DOT in _text(element)
    for child in list(element):
        element.remove(child)
    element.text = None
    if inline:
        element.text = (" %s " % DOT).join(lines)
        return
    last = None
    if bold:
        last = etree.SubElement(element, "b")
        last.text = bold
    for line in lines:
        if last is None:
            element.text = line
            last = element
        else:
            last = etree.SubElement(element, "br")
            last.tail = line


def _contact(root, facts):
    email, phone = facts.get("email") or "", facts.get("phone") or ""
    for link in root.xpath(".//a[starts-with(@href, 'mailto:')]"):
        if SAMPLE_EMAIL.search(link.get("href") or ""):
            if email:
                link.set("href", "mailto:" + email)
            else:
                _remove_row(link)
    for link in root.xpath(".//a[starts-with(@href, 'tel:')]"):
        if "555-555-5556" in (link.get("href") or ""):
            if phone:
                link.set("href", "tel:" + re.sub(r"[^\d+]", "", phone))
            else:
                _remove_row(link)
    _sub_text(root, SAMPLE_EMAIL, email)
    _sub_text(root, SAMPLE_PHONE, phone)


def _address(root, facts):
    lines = [line for line in facts.get("address_lines") or [] if line]
    for node in root.xpath(".//p"):
        if "Marina Blvd" not in _text(node):
            continue
        if not lines:
            _remove(node)
        else:
            _lines(node, lines, bold=facts["name"] if node.xpath("./b") else None)


def _lists(root, facts):
    """The columns of sample links (href ``#``: Our Company, Documentation, Marketplace ...) become the pages of
    the website's menu, as many as there are; a column left with none goes, with its title"""
    entries = list(facts.get("links") or [])
    for ul in root.xpath(".//ul[.//a[@href='#']]"):
        present = set(ul.xpath(".//a/@href"))
        taken = []
        while entries and len(taken) < MAX_LINKS:
            entry = entries.pop(0)
            if entry[1] not in present:  # a page the column already links to is not listed twice
                taken.append(entry)
        items = ul.xpath("./li[.//a[@href='#']]")
        if not taken:
            heading = ul.getprevious()
            _remove(ul)
            if heading is not None and heading.tag in ("h4", "h5", "h6"):
                _remove(heading)
            continue
        template = items[0]
        anchor = template
        for stale in items[1:]:
            _remove(stale)
        for name, url in taken:
            node = etree.fromstring(etree.tostring(template, with_tail=False))
            node.tail = template.tail
            link = node.xpath(".//a")[0]
            link.set("href", url)
            for child in list(link):
                link.remove(child)
            link.text = name
            anchor.addnext(node)
            anchor = node
        _remove(template)


def _social(root, facts):
    keep = facts.get("social") or {}
    for link in root.xpath(".//a[contains(@href, '/website/social/')]"):
        found = SOCIAL.search(link.get("href") or "")
        if found and not keep.get(found.group(1)):
            _remove_row(link)


def _tidy(root):
    """A list left with no item goes with its title, and a column left with nothing in it goes too"""
    for ul in root.xpath(".//ul[not(.//li)]"):
        heading = ul.getprevious()
        _remove(ul)
        if heading is not None and heading.tag in ("h4", "h5", "h6"):
            _remove(heading)
    for column in root.xpath(".//div[contains(concat(' ', normalize-space(@class), ' '), ' col-lg-')]"):
        if not _text(column) and not column.xpath(".//img|.//a|.//i|.//iframe|.//section|.//hr")                 and not column.xpath(".//*[@*[starts-with(name(), 't-')]]"):
            _remove(column)


def _whatsapp(root, facts):
    """A WhatsApp click-to-chat link beside the phone link (header strip and footer) when the site has a number.
    Written once: a view that already has the link is left as it is."""
    url = facts.get("whatsapp_url")
    if not url or root.xpath(".//a[starts-with(@href, 'https://wa.me/')]"):
        return
    phones = root.xpath(".//a[starts-with(@href, 'tel:')]")
    if not phones:
        return
    phone = phones[0]
    link = etree.Element("a", {"href": url, "class": "nb-wa-link ms-2", "target": "_blank",
                               "rel": "noopener noreferrer"})
    etree.SubElement(link, "i", {"class": "fa fa-whatsapp me-1", "role": "img", "aria-hidden": "true"})
    label = etree.SubElement(link, "span")
    label.text = facts.get("whatsapp_label") or "WhatsApp"
    phone.addnext(link)


def _words(root, facts):
    name, about = facts["name"], facts.get("about") or ""
    for node in list(root.iter()):
        if not isinstance(node.tag, str):
            continue
        text = _text(node)
        if node.tag == "p" and SAMPLE_ABOUT.match(text):
            if about:
                for child in list(node):
                    node.remove(child)
                node.text = about
            else:
                _remove(node)
        elif node.tag in ("h2", "h3", "h4") and (text in SAMPLE_HEADLINES or text.startswith(SAMPLE_HEADLINE_START)):
            for child in list(node):
                node.remove(child)
            node.text = name
    for node in root.xpath(".//img[contains(@src, 'website_logo.svg')]"):
        if facts.get("logo_url"):
            node.set("src", facts["logo_url"])
        else:  # no logo of the company: Odoo's "Your Logo" picture does not stay
            _remove(node)
    _sub_text(root, SAMPLE_NAME, name)


def canonical(arch):
    """:return: the arch as :func:`personalise` writes it, with nothing changed: to tell whether anything was"""
    return etree.tostring(etree.fromstring(arch.encode() if isinstance(arch, str) else arch), encoding="unicode")


def personalise(arch, facts):
    """:param str arch: the arch of a footer (or header) view
    :param dict facts: ``name``, ``address_lines``, ``phone``, ``email``, ``about``, ``links`` (``[(name, url)]``
        of the menu), ``social`` (``{network: bool}``) and ``logo_url``, in the language written
    :return: the arch with the company where Odoo's sample was"""
    root = etree.fromstring(arch.encode() if isinstance(arch, str) else arch)
    _address(root, facts)
    _contact(root, facts)
    _lists(root, facts)
    _social(root, facts)
    _words(root, facts)
    _whatsapp(root, facts)
    _tidy(root)
    return etree.tostring(root, encoding="unicode")


def copyright_line(arch, name, word="Copyright"):
    """:return: the arch of Odoo's copyright view with the company's name where it says "Company name" """
    return arch.replace(COPYRIGHT, f"{word} &amp;copy; {escape(name)}")
