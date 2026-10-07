"""Reading the Odoo documentation, which is written in reStructuredText for
Sphinx, into plain text pieces.

No parser of reStructuredText is used: the pages of the documentation are
regular, and what matters for an assistant is the text of each section,
not the layout. Images, tables of contents and the markup that gives the
site its look are left out; notes, tips, examples and tables are kept as
text, since they are what the person needs.

This module imports nothing from Odoo: the script that builds the corpus
runs it outside Odoo.
"""
import re

# The characters Sphinx accepts under a heading; the order of first use in a
# page gives the level, as in Sphinx
UNDERLINE = re.compile(r"^([=\-~^\"'`:.*+#_])\1{3,}\s*$")
DIRECTIVE = re.compile(r"^(\s*)\.\. ([a-z][a-z0-9_-]*)::\s*(.*)$")
TARGET = re.compile(r"^\s*\.\. _([^:]+):\s*$")
COMMENT = re.compile(r"^\s*\.\.(\s|$)")
OPTION = re.compile(r"^\s*:[a-z-]+:(\s|$)")
ROLE = re.compile(r":([a-z][a-z-]*):`([^`]*)`")
LINKED = re.compile(r"^(.*?)\s*<([^<>]+)>$")
# ``code``, **bold**, *emphasis*: the markers go, the words stay
CODE = re.compile(r"``(.+?)``")
BOLD = re.compile(r"\*\*(.+?)\*\*")
EMPHASIS = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])")
SUBSTITUTION = re.compile(r"\|([^|\s][^|]*)\|")
LIST_ITEM = re.compile(r"^(\s*)([-*•]|\d+[.)]|#\.)\s+")
SPACES = re.compile(r"[ \t]+")

# What a directive becomes
SKIPPED = {
    "image", "figure", "toctree", "meta", "role", "rst-class", "raw", "only", "index",
    "video", "youtube", "vimeo", "include", "literalinclude", "contents", "sectionauthor",
    "codeauthor", "glossary", "productionlist", "default-domain", "highlight", "todo",
    # Documentation drawn from the Python source by Sphinx: nothing is written here
    "automodule", "autoclass", "automethod", "autofunction", "autoattribute", "autofield",
    "autodata", "autoexception", "autoproperty", "currentmodule", "module",
}
LABELLED = {
    "note": "Note", "tip": "Tip", "important": "Important", "warning": "Warning",
    "danger": "Warning", "caution": "Caution", "attention": "Attention", "hint": "Tip",
    "example": "Example", "seealso": "See also", "exercise": "Exercise",
    "spoiler": "", "highlights": "", "admonition": "", "card": "", "cards": "",
    "tabs": "", "tab": "", "group-tab": "", "code-block": "", "code": "", "math": "",
    "code-tab": "", "list-table": "", "csv-table": "", "table": "",
    # What the developer documentation describes, named by the argument
    "attribute": "", "option": "", "method": "", "function": "", "class": "", "program": "",
    "data": "", "property": "", "exception": "",
}
# Directives whose argument names what follows
NAMED = (
    "tab", "group-tab", "admonition", "card", "spoiler", "code-tab",
    "attribute", "option", "method", "function", "class", "program", "data", "property",
    "exception",
)
# Directives whose content is code, kept as written
VERBATIM = ("code-block", "code", "math", "code-tab")
# Marks a line of code, so that the markup inside it is left alone
CODE_MARK = "\x00"
MAX_PASSAGE_CHARS = 1800


def _inline(text):
    """Markup of Sphinx and reStructuredText inside a line, to words."""
    def role(match):
        name, content = match.group(1), match.group(2)
        if name == "icon":
            return ""
        if content.startswith("~"):
            # A Python name shown by its last part, as Sphinx does
            return content[1:].rsplit(".", 1)[-1]
        if name == "menuselection":
            return " ▸ ".join(part.strip() for part in content.split("-->"))
        if name in ("doc", "ref", "download", "term"):
            linked = LINKED.match(content)
            if linked and linked.group(1):
                return linked.group(1)
            # A path or a label: its last part, as words
            return content.rsplit("/", 1)[-1].replace("_", " ").replace("-", " ").strip("<>")
        if name == "abbr":
            return content
        return content

    text = ROLE.sub(role, text)
    text = CODE.sub(r"\1", text)
    text = BOLD.sub(r"\1", text)
    text = EMPHASIS.sub(r"\1", text)
    text = SUBSTITUTION.sub(r"\1", text)
    # `text <url>`_ links and `text`_ references
    text = re.sub(r"`([^`<]+?)\s*<[^>]+>`_{1,2}", r"\1", text)
    text = re.sub(r"`([^`]+?)`_{1,2}", r"\1", text)
    text = text.replace("\\*", "*").replace("\\_", "_")
    return SPACES.sub(" ", text).strip()


def _slug(heading):
    """The id Sphinx gives a heading: lower case, words joined by hyphens."""
    slug = re.sub(r"[^\w\s-]", "", heading.lower(), flags=re.UNICODE)
    return re.sub(r"[\s_-]+", "-", slug).strip("-")


def _indent(line):
    return len(line) - len(line.lstrip(" "))


class _Page:
    def __init__(self):
        self.title = ""
        self.description = ""
        self.levels = []
        # [heading text by level], for the path of a section
        self.path = []
        self.sections = []
        self.current = None
        self.pending_target = None

    def heading(self, text, char):
        if char not in self.levels:
            self.levels.append(char)
        level = self.levels.index(char)
        if level == 0 and not self.title:
            self.title = text
            return
        self.path = self.path[: max(level - 1, 0)] + [text]
        anchor = self.pending_target or _slug(text)
        self.pending_target = None
        self.current = {"heading": " ▸ ".join(self.path), "anchor": anchor, "lines": []}
        self.sections.append(self.current)

    def write(self, text):
        if not text:
            return
        if self.current is None:
            self.current = {"heading": "", "anchor": "", "lines": []}
            self.sections.append(self.current)
        self.current["lines"].append(text)

    def blank(self):
        if self.current and self.current["lines"] and self.current["lines"][-1] != "":
            self.current["lines"].append("")


def _block_end(lines, start, indent):
    """:return: the index of the first line after the block of ``lines``
    starting at ``start`` whose lines are indented more than ``indent``"""
    position = start
    while position < len(lines):
        line = lines[position]
        if line.strip() and _indent(line) <= indent:
            break
        position += 1
    return position


def _table_rows(block):
    """Rows of a list-table: ``* - cell`` then ``  - cell`` lines."""
    rows, row = [], None
    for line in block:
        stripped = line.strip()
        if stripped.startswith("* -") or stripped == "*":
            if row is not None:
                rows.append(row)
            row = [stripped[3:].strip()] if stripped.startswith("* -") else []
        elif stripped.startswith("- ") and row is not None:
            row.append(stripped[2:].strip())
        elif stripped and row:
            row[-1] = f"{row[-1]} {stripped}"
    if row is not None:
        rows.append(row)
    return rows


def _read(lines, page, base_indent=0, label=""):
    """Walk ``lines``, writing text into ``page``. Called again for the
    content of a directive, with its label."""
    position = 0
    if label:
        page.write(f"{label}:")
    while position < len(lines):
        line = lines[position]
        stripped = line.strip()

        if not stripped:
            page.blank()
            position += 1
            continue

        target = TARGET.match(line)
        if target:
            page.pending_target = target.group(1).strip()
            position += 1
            continue

        directive = DIRECTIVE.match(line)
        if directive:
            indent = _indent(line)
            name, argument = directive.group(2), _inline(directive.group(3))
            end = _block_end(lines, position + 1, indent)
            block = lines[position + 1:end]
            # Options such as :align: or :widths: come first in the block
            while block and (OPTION.match(block[0]) or not block[0].strip()):
                if OPTION.match(block[0]) and name == "meta" and ":description:" in block[0]:
                    text = block[0].split(":description:", 1)[1]
                    rest = []
                    for extra in block[1:]:
                        if OPTION.match(extra) or not extra.strip():
                            break
                        rest.append(extra)
                    page.description = _inline(" ".join([text, *rest]))
                block = block[1:]
            if name in SKIPPED:
                pass
            elif name in ("list-table", "csv-table"):
                for row in _table_rows(block):
                    cells = [cell.strip() for cell in row]
                    if len(cells) >= 2:
                        page.write(f"{cells[0]}: {' | '.join(cells[1:])}")
                    elif cells:
                        page.write(cells[0])
                page.blank()
            elif name in VERBATIM:
                # Code is kept as written, its indentation included
                if name == "code-tab" and argument:
                    page.write(f"{argument}:")
                margins = [_indent(code) for code in block if code.strip()]
                margin = min(margins) if margins else 0
                for code in block:
                    page.write(CODE_MARK + code[margin:].rstrip())
                page.blank()
            else:
                heading = argument if name in NAMED else ""
                prefix = LABELLED.get(name, name.replace("-", " ").capitalize())
                text = " ".join(part for part in (prefix, heading) if part)
                page.blank()
                _read(block, page, indent, text)
                page.blank()
            position = end
            continue

        if COMMENT.match(line):
            position = _block_end(lines, position + 1, _indent(line))
            continue

        # A heading: text on one line, a line of one character under it
        if position + 1 < len(lines) and UNDERLINE.match(lines[position + 1]) \
                and len(lines[position + 1].strip()) >= len(stripped) and not LIST_ITEM.match(line):
            # A title with a line over it as well is its own level, as in Sphinx
            over = position > 0 and bool(UNDERLINE.match(lines[position - 1]))
            page.heading(_inline(stripped), lines[position + 1].strip()[0] + ("o" if over else ""))
            position += 2
            continue
        if UNDERLINE.match(line):
            position += 1
            continue

        # Text is kept as written here: the markup is read once the lines of
        # a paragraph are joined, since a role may span two lines
        # A paragraph ending with "::" is followed by a block of code, kept
        # as written; the "::" reads as ":" and a lone "::" is not read at all
        literal = stripped == "::" or stripped.endswith("::")
        if literal:
            stripped = "" if stripped == "::" else stripped[:-1]
        item = LIST_ITEM.match(stripped)
        previous = page.current["lines"][-1] if page.current and page.current["lines"] else ""
        if not stripped:
            pass
        elif item:
            page.write("- " + stripped[item.end():].strip())
        elif stripped.startswith(("+-", "+=", "|")):
            # A row of a grid table stays a line of its own
            page.write(stripped)
        elif previous.startswith("- ") and _indent(line) > base_indent:
            # The item goes on, on the next line
            page.current["lines"][-1] += " " + stripped
        elif previous and (not previous.endswith(":") or previous.count("`") % 2 == 1) \
                and _indent(line) == _indent(lines[position - 1]) \
                and lines[position - 1].strip() and not UNDERLINE.match(lines[position - 1]):
            # A paragraph goes on; so does a role left open at the end of a line
            page.current["lines"][-1] += " " + stripped
        else:
            page.write(stripped)
        position += 1
        if literal:
            end = _block_end(lines, position, _indent(line))
            block = lines[position:end]
            while block and not block[0].strip():
                block = block[1:]
            while block and not block[-1].strip():
                block = block[:-1]
            margins = [_indent(code) for code in block if code.strip()]
            margin = min(margins) if margins else 0
            page.blank()
            for code in block:
                page.write(CODE_MARK + code[margin:].rstrip())
            page.blank()
            position = end


def _pack(parts, separator):
    """Join ``parts`` into pieces of at most MAX_PASSAGE_CHARS."""
    pieces, current = [], ""
    for part in parts:
        if current and len(current) + len(part) + len(separator) > MAX_PASSAGE_CHARS:
            pieces.append(current)
            current = part
        else:
            current = f"{current}{separator}{part}" if current else part
    if current:
        pieces.append(current)
    return pieces


def _passages(section):
    """A section as pieces of text of a readable size."""
    text = "\n".join(
        line[len(CODE_MARK):] if line.startswith(CODE_MARK) else _inline(line) if line else ""
        for line in section["lines"]
    ).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text:
        return []
    if len(text) <= MAX_PASSAGE_CHARS:
        return [text]
    pieces = []
    for paragraph in _pack(text.split("\n\n"), "\n\n"):
        if len(paragraph) <= MAX_PASSAGE_CHARS:
            pieces.append(paragraph)
        else:
            # A long list, its items one under the other
            pieces.extend(_pack(paragraph.split("\n"), "\n"))
    return pieces


def read_page(source):
    """:param source: the text of one .rst file
    :return: {"title", "description", "sections": [{"heading", "anchor", "text"}]}"""
    page = _Page()
    _read(source.splitlines(), page)
    sections = []
    for section in page.sections:
        for text in _passages(section):
            sections.append({"heading": section["heading"], "anchor": section["anchor"], "text": text})
    return {"title": page.title, "description": page.description, "sections": sections}
