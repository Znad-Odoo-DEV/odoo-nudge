"""Odoo's own snippets, rendered by Odoo and filled with text and a picture.

Nothing here invents markup: the section is what the website editor would
drop on the page; only the words, the links and the picture change.
"""
import copy as copymod
import re
from urllib.parse import quote

from . import compat
from .safe import clean_content

BUTTON = ".//a[contains(concat(' ', normalize-space(@class), ' '), ' btn ')]"
MAX_ITEMS = 12
HEADING = ".//h3|.//h4|.//h5"


def has_class(name):
    return f"contains(concat(' ', normalize-space(@class), ' '), ' {name} ')"


def is_icon(element):
    return element.tag in ("i", "span") and "fa" in (element.get("class") or "").split()


def set_text(element, text):
    """Replace the words of an element and what is formatted inside them; its icons stay"""
    if element is None:
        return
    icons = [child for child in element if is_icon(child)]
    for child in list(element):
        if child not in icons:
            element.remove(child)
    element.text = text
    for icon in icons:
        icon.tail = None


def first(section, xpath):
    found = section.xpath(xpath)
    return found[0] if found else None


def title_of(section):
    """:return: the heading that titles the whole block, or None when the block has none.

    The title is an h1 or h2 where the snippet has one. Otherwise it is the first h3 or h4 when that
    one is told apart from the rest (another level or class: Odoo 18's features and team blocks), or
    the only one; headings that all look alike are the items, and the block has no title of its own.
    Never ``a or b`` on elements: an empty element is false."""
    found = first(section, ".//h1|.//h2")
    if found is not None:
        return found
    headings = section.xpath(".//h3|.//h4")
    if not headings:
        return None
    lead, others = headings[0], headings[1:]
    signature = lambda node: (node.tag, " ".join(sorted((node.get("class") or "").split())))  # noqa: E731
    if not others or all(signature(other) != signature(lead) for other in others):
        return lead
    return None


def add_title(section, text):
    """Give a block that has no title slot one, at the top of its container, the way the editor's
    title would sit: only for blocks that are a grid of items."""
    holder = first(section, f".//div[{has_class('container')} or {has_class('container-fluid')}]")
    if holder is None:
        holder = section
    heading = holder.makeelement("h2", {"class": "text-center"})
    heading.text = text
    heading.tail = "\n"
    holder.insert(0, heading)
    return heading


def text_of(section):
    lead = first(section, f".//p[{has_class('lead')}]")
    if lead is not None:
        return lead
    for paragraph in section.xpath(".//p"):
        if not paragraph.xpath(BUTTON) and not paragraph.xpath(".//img") and (paragraph.text_content() or "").strip():
            return paragraph
    return None


def _paragraphs(element, text):
    """Fill a container with paragraphs of the text: a line starts one"""
    parts = [part.strip() for part in re.split(r"\n+", text or "") if part.strip()] or [""]
    template = first(element, ".//p")
    for child in list(element):
        element.remove(child)
    for part in parts:
        paragraph = copymod.deepcopy(template) if template is not None else element.makeelement("p", {})
        for child in list(paragraph):
            paragraph.remove(child)
        paragraph.text = part
        paragraph.tail = None
        element.append(paragraph)


def _resize(containers, count):
    """Exactly ``count`` containers: the last is cloned, the extra dropped"""
    if not containers:
        return []
    count = max(1, min(count, MAX_ITEMS))
    while len(containers) < count:
        clone = copymod.deepcopy(containers[-1])
        containers[-1].addnext(clone)
        containers.append(clone)
    for extra in containers[count:]:
        extra.getparent().remove(extra)
    return containers[:count]


def _pair(item, *names):
    for name in names:
        if item.get(name) not in (None, ""):
            return str(item[name])
    return ""


def _fill_features(section, items):
    containers = section.xpath(
        f".//div[({has_class('col-lg-4')} or {has_class('col-lg-3')} or {has_class('col-lg-6')}) "
        f"and ./descendant::*[self::h3 or self::h4 or self::h5] "
        f"and not(.//div[{has_class('row')}])]")
    for node, item in zip(_resize(containers, len(items)), items):
        set_text(first(node, HEADING), _pair(item, "title", "name", "question"))
        paragraph = first(node, ".//p")
        if paragraph is not None:
            set_text(paragraph, _pair(item, "text", "role", "answer"))


def _fill_team(section, items):
    containers = section.xpath(".//div[@data-name='Team Member']")
    if not containers:  # a version whose members carry no data-name: a column with a name in it
        containers = section.xpath(".//div[.//h4 and .//img and not(.//div[.//h4 and .//img])]")
    for node, item in zip(_resize(containers, len(items)), items):
        set_text(first(node, HEADING), _pair(item, "title", "name"))
        role = first(node, f".//p[{has_class('text-muted')}]")
        set_text(role, _pair(item, "role", "subtitle"))
        bio = [p for p in node.xpath(".//p") if p is not role]
        if bio:
            set_text(bio[-1], _pair(item, "text", "bio"))
        if item.get("image"):
            image = first(node, ".//img")
            if image is not None:
                image.set("src", item["image"])
                image.set("alt", _pair(item, "title", "name"))


def _fill_numbers(section, items):
    containers = section.xpath(f".//div[.//span[{has_class('s_number')}]]")
    containers = [node for node in containers if not node.xpath(f".//div[.//span[{has_class('s_number')}]]")]
    for node, item in zip(_resize(containers, len(items)), items):
        set_text(first(node, f".//span[{has_class('s_number')}]"), _pair(item, "number", "title"))
        set_text(first(node, f".//span[not({has_class('s_number')})]"), _pair(item, "label", "text"))


def _fill_faq(section, items):
    # 18 and 19 make an accordion of items with a button; 17 makes cards with a link
    containers = section.xpath(f".//div[{has_class('accordion-item')}]") or section.xpath(
        f".//div[@data-name='Item'][{has_class('card')}]")
    containers = _resize(containers, len(items))
    for node, item in zip(containers, items):
        button = first(node, ".//button|.//a[@data-bs-toggle='collapse']")
        if button is not None:
            for child in list(button):
                if not is_icon(child):
                    button.remove(child)
            button.text = _pair(item, "question", "title")
        body = first(node, f".//div[{has_class('accordion-body')} or {has_class('card-body')}]")
        if body is not None:
            _paragraphs(body, _pair(item, "answer", "text"))
    # clones must not share ids, or two answers open together
    base = compat.unique_id()
    for index, node in enumerate(containers):
        suffix = f"{base}_{index + 1}"
        button = first(node, ".//button|.//a[@data-bs-toggle='collapse']")
        collapse = first(node, f".//div[{has_class('accordion-collapse')} or {has_class('collapse')}]")
        if button is None or collapse is None:
            continue
        shown = index == 0
        button.set("id", f"accordion-button{suffix}")
        collapse.set("id", f"myCollapseTab{suffix}")
        button.set("data-bs-target", f"#myCollapseTab{suffix}")
        button.set("aria-controls", f"myCollapseTab{suffix}")
        collapse.set("aria-labelledby", f"accordion-button{suffix}")
        button.set("aria-expanded", "true" if shown else "false")
        classes = [name for name in (button.get("class") or "").split() if name != "collapsed"]
        button.set("class", " ".join(classes + ([] if shown else ["collapsed"])))
        classes = [name for name in (collapse.get("class") or "").split() if name != "show"]
        collapse.set("class", " ".join(classes + (["show"] if shown else [])))


def _fill_contact(section, content, facts):
    phone = content.get("phone") or facts.get("phone")
    email = content.get("email") or facts.get("email")
    address = content.get("address") or facts.get("address")
    nodes = section.xpath(".//div[@data-name='Info block']")
    if not nodes:  # the text block that stands in for the contact snippet on Odoo 17
        paragraphs = section.xpath(".//p")
        if paragraphs:
            last = paragraphs[-1]
            for value in (phone, email, address):
                if value:
                    more = copymod.deepcopy(paragraphs[0])
                    more.text = value
                    for child in list(more):
                        more.remove(child)
                    last.addnext(more)
                    last = more
        return
    for node in nodes:
        icon = first(node, ".//i[contains(@class, 'fa-')]")
        kind = (icon.get("class") or "") if icon is not None else ""
        link = first(node, ".//a")
        if "envelope" in kind and link is not None:
            if email:
                link.set("href", f"mailto:{email}")
                set_text(link, email)
        elif "phone" in kind and link is not None:
            if phone:
                link.set("href", "tel:" + re.sub(r"[^\d+]", "", phone))
                set_text(link, phone)
        elif ("building" in kind or "map" in kind) and address:
            set_text(first(node, ".//p"), address)


def _fill_map(section, content, facts):
    address = content.get("address") or facts.get("address") or ""
    frame = first(section, ".//iframe")
    container = first(section, f".//div[{has_class('map_container')}]")
    if frame is None and container is not None and address:
        # Odoo leaves the frame out when it runs tests: the same one is made
        frame = container.makeelement("iframe", {
            "class": "s_map_embedded o_not_editable", "width": "100%", "height": "100%", "frameborder": "0",
            "scrolling": "no", "marginheight": "0", "marginwidth": "0", "aria-label": "Map"})
        filter_box = first(container, f".//div[{has_class('s_map_color_filter')}]")
        if filter_box is not None:
            filter_box.addprevious(frame)
        else:
            container.append(frame)
    if frame is not None and address:
        frame.set("src", "https://maps.google.com/maps?q=%s&t=&z=13&ie=UTF8&iwloc=&output=embed" % quote(address))
        section.set("data-map-address", address)
        for warning in section.xpath(f".//div[{has_class('missing_option_warning')}]"):
            warning.getparent().remove(warning)


def set_picture(section, url, alt=""):
    """Put a picture where the snippet shows its main one"""
    if not url:
        return False
    background = first(section, f".//span[{has_class('s_parallax_bg')}]")
    if background is not None:
        style = background.get("style") or ""
        if "url(" in style:
            style = re.sub(r"url\([^)]*\)", f"url('{url}')", style)
        else:
            style = f"background-image: url('{url}'); " + style
        background.set("style", style)
        return True
    for image in section.xpath(".//img"):
        classes = (image.get("class") or "").split()
        if "rounded-circle" in classes or "s_reference" in (image.get("src") or ""):
            continue
        image.set("src", url)
        image.set("alt", alt)
        for attribute in ("data-original-id", "data-original-src", "data-mimetype", "data-resize-width"):
            image.attrib.pop(attribute, None)
        return True
    return False


ITEM_FILLERS = {
    "s_company_team": _fill_team,
    "s_numbers": _fill_numbers,
    "s_faq_collapse": _fill_faq,
}


def fill(section, snippet, content, picture_url=None, facts=None):
    """Fill the section of a snippet with the words of one language.

    :param str snippet: the snippet name, ``s_cover`` or ``module.xmlid``
    :param dict content: ``title``, ``text``, ``button``, ``button_url``, ``items`` ...
    :param picture_url: where its main picture comes from
    :param dict facts: the company's ``phone``, ``email``, ``address`` for contact blocks
    :return: the section"""
    content = clean_content(content or {})
    facts = facts or {}
    short = snippet.split(".")[-1]
    has_title_slot = title_of(section) is not None
    if content.get("title"):
        set_text(title_of(section), content["title"])
    elif "title" in content and title_of(section) is not None:
        # a block that has no heading: the sample's heading goes
        heading = title_of(section)
        heading.getparent().remove(heading)
    if content.get("text"):
        paragraph = text_of(section)
        lines = [line.strip() for line in str(content["text"]).split("\n") if line.strip()]
        set_text(paragraph, lines[0] if lines else "")
        for line in reversed(lines[1:]):
            more = copymod.deepcopy(paragraph)
            more.text = line
            paragraph.addnext(more)
    button = first(section, BUTTON)
    if button is not None:
        if content.get("button"):
            set_text(button, content["button"])
        button.set("href", content.get("button_url") or "/contactus")
        button.attrib.pop("t-att-href", None)
    items = [item for item in (content.get("items") or []) if isinstance(item, dict)]
    if items:
        ITEM_FILLERS.get(short, _fill_features)(section, items)
        if content.get("title") and not has_title_slot:
            add_title(section, content["title"])  # a grid of items whose version gives it no title
    if short == "s_contact_info":
        _fill_contact(section, content, facts)
    elif short == "s_map":
        _fill_map(section, content, facts)
    if picture_url:
        set_picture(section, picture_url, content.get("title") or "")
    return section
