"""Designed emails: the body of a template, laid out for mail clients.

What the administrator asked for comes as parts: a title, a greeting, the
text, the values of the record in a table, its lines, a box of what the
reader must answer, a closing, a button to the record, the company under it
all. Each part is written here, in one of four styles, as QWeb the way the
templates of Odoo are: tables and inline styles, which every mail client
reads, 600 pixels wide at most.

Nothing the model wrote reaches the template as code. Its text is escaped;
a value of the record is named by a path of fields, checked for the person,
and printed with t-field, which formats it in the reader's language: a date
as a date, an amount with its currency, a choice by its label.
"""
import re

from markupsafe import escape

from odoo.addons.nudge.tools.records import Refused, target_field

STYLES = {
    "classic": "A coloured band with the title, a clean table, a square button",
    "modern": "A large coloured header, rounded cards, a round button",
    "minimal": "Quiet: a thin line of colour, plain text, a link",
    "formal": "A letter: the logo on top, a reference line, a bordered table, serif type",
}
THEMES = {
    "classic": {
        "font": "Arial,Helvetica,sans-serif", "page": "#f4f5f7", "card_border": "1px solid #e3e5e8",
        "radius": "8px", "header": "band", "title_size": "20px", "label_bg": "#f6f8fa",
        "cell_border": "border-bottom:1px solid #eaeef2;", "box": "tinted", "button_radius": "4px",
    },
    "modern": {
        "font": "'Segoe UI',Roboto,Helvetica,Arial,sans-serif", "page": "#eef0f6", "card_border": "0",
        "radius": "16px", "header": "hero", "title_size": "26px", "label_bg": "#ffffff",
        "cell_border": "border-bottom:1px solid #eef0f4;", "box": "card", "button_radius": "999px",
    },
    "minimal": {
        "font": "Arial,Helvetica,sans-serif", "page": "#ffffff", "card_border": "0",
        "radius": "0", "header": "line", "title_size": "19px", "label_bg": "#ffffff",
        "cell_border": "border-bottom:1px solid #f0f0f0;", "box": "bordered", "button_radius": "0",
    },
    "formal": {
        "font": "Georgia,'Times New Roman',serif", "page": "#ffffff", "card_border": "1px solid #d0d4d9",
        "radius": "0", "header": "letterhead", "title_size": "19px", "label_bg": "#f3f4f6",
        "cell_border": "border:1px solid #d0d4d9;", "box": "bordered", "button_radius": "2px",
    },
}
TEXT = "#1f2328"
MUTED = "#57606a"
FALLBACK_ACCENT = "#875A7B"
PLACEHOLDER = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
FIELD_PLACEHOLDER = re.compile(r"^object(\.[a-z_][a-z0-9_]*)+$")
ARABIC = re.compile("[؀-ۿ]")
# What a value of the record cannot be in an email
UNPRINTABLE = ("binary", "image", "json", "properties", "properties_definition", "reference", "many2one_reference")
WORDS = {
    False: {"yes": "Yes", "no": "No", "ref": "Reference"},
    True: {"yes": "نعم", "no": "لا", "ref": "المرجع"},
}


def literal(written):
    """:return: QWeb that prints ``written`` as text.

    Through t-out, which escapes what it prints: QWeb prints the static text
    of a template as it was parsed, so text the model wrote never stands in
    the template as static text."""
    written = written or ""
    if not written:
        return ""
    return f'<t t-out="{escape(repr(written))}"/>'


def lines_of(written):
    """:return: QWeb of a text whose line breaks are kept"""
    return "<br/>".join(literal(line) for line in (written or "").split("\n"))


def is_right_to_left(*texts):
    return any(ARABIC.search(text or "") for text in texts)


def check_path(model, path, owner="object"):
    """:return: the field at the end of ``path``, read by this person, through
    single records only
    :raise Refused: when it cannot be printed"""
    current = model
    names = path.split(".")
    for position, name in enumerate(names):
        field = target_field(current, name)
        last = position == len(names) - 1
        if not last and field.type != "many2one":
            raise Refused(
                f"{owner}.{path}: {name} holds several records; only a single record is gone "
                "through. Lines are shown as lines.")
        if field.type in UNPRINTABLE:
            raise Refused(f"{owner}.{path} is a {field.type}: it is not printed in an email.")
        if field.comodel_name:
            current = current.env[field.comodel_name]
    return field


def value(model, path, owner, words):
    """:return: QWeb that prints the value at ``path`` of ``owner``"""
    field = check_path(model, path, owner)
    expression = f"{owner}.{path}"
    if field.type == "boolean":
        return f'<t t-if="{expression}">{literal(words["yes"])}</t><t t-else="">{literal(words["no"])}</t>'
    if field.type in ("one2many", "many2many"):
        return f"<t t-out=\"', '.join({expression}.mapped('display_name'))\"/>"
    return f'<span t-field="{expression}"/>'


def text(model, written, words, owner="object"):
    """:return: QWeb of a text the model wrote: escaped, the line breaks kept,
    each {{ object.path }} a value of the record"""
    parts, end = [], 0
    for match in PLACEHOLDER.finditer(written or ""):
        parts.append(lines_of(written[end:match.start()]))
        inner = match.group(1)
        if not FIELD_PLACEHOLDER.match(inner):
            raise Refused(
                f"The placeholder {{{{ {inner} }}}} is not a field of the record: only "
                "{{ object.field }}, or {{ object.field.field }}, goes between braces.")
        parts.append(value(model, inner[len("object."):], owner, words))
        end = match.end()
    parts.append(lines_of((written or "")[end:]))
    return "".join(parts)


def subject(model, written):
    """:return: the subject as an inline template: a value of the record as
    text, a related record by its name"""
    def placeholder(match):
        inner = match.group(1)
        if not FIELD_PLACEHOLDER.match(inner):
            raise Refused(f"The placeholder {{{{ {inner} }}}} of the subject is not a field of the record.")
        field = check_path(model, inner[len("object."):])
        if field.type == "selection":
            raise Refused(
                f"{inner} is a choice: in the subject it would show its technical value. "
                "Put it in the body, where its label is shown.")
        if field.type in ("one2many", "many2many", "html"):
            raise Refused(f"{inner} is a {field.type}: it does not go in a subject.")
        if field.type == "many2one":
            inner += ".display_name"
        return "{{ %s }}" % inner

    return PLACEHOLDER.sub(placeholder, written.strip())


def paragraphs(model, written, words, style):
    blocks = [block.strip() for block in re.split(r"\n\s*\n", written or "") if block.strip()]
    return "".join(
        f'<p style="margin:0 0 14px;font-size:{"15px" if style == "modern" else "14px"};line-height:1.6;">'
        f"{text(model, block, words)}</p>" for block in blocks)


def label_of(model, path, given):
    if (given or "").strip():
        return literal(given.strip())
    field = check_path(model, path)
    return literal(field.string)


def section_title(written, theme):
    if not (written or "").strip():
        return ""
    if theme["header"] == "hero":
        style = f"margin:26px 0 10px;font-size:15px;font-weight:700;color:{TEXT};"
    elif theme["header"] == "letterhead":
        style = f"margin:24px 0 8px;font-size:15px;font-weight:700;color:{TEXT};text-decoration:underline;"
    else:
        style = f"margin:24px 0 8px;font-size:12px;font-weight:700;letter-spacing:.6px;text-transform:uppercase;color:{MUTED};"
    return f'<div style="{style}">{literal(written.strip())}</div>'


def details(model, spec, theme, align, words):
    rows = spec.get("rows") or []
    if not rows:
        return ""
    cells = []
    for row in rows:
        label = label_of(model, row["field"], row.get("label"))
        shown = value(model, row["field"], "object", words)
        cells.append(
            f'<tr><td style="padding:9px 12px;width:38%;vertical-align:top;background-color:{theme["label_bg"]};'
            f'{theme["cell_border"]}color:{MUTED};font-size:13px;text-align:{align};">{label}</td>'
            f'<td style="padding:9px 12px;vertical-align:top;{theme["cell_border"]}font-size:14px;'
            f'font-weight:600;color:{TEXT};text-align:{align};">{shown}</td></tr>')
    table = ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
             f'style="border-collapse:collapse;{"border-radius:10px;" if theme["header"] == "hero" else ""}">'
             + "".join(cells) + "</table>")
    return section_title(spec.get("title"), theme) + table


def lines_table(model, spec, theme, align, words):
    if not spec:
        return ""
    field = check_path(model, spec["field"])
    if field.type not in ("one2many", "many2many"):
        raise Refused(f"{spec['field']} is a {field.type}: lines are a one2many or many2many field.")
    comodel = model.env[field.comodel_name]
    columns = spec.get("columns") or []
    if not columns:
        raise Refused("Lines name their columns.")
    head_border = (f'background-color:{theme["label_bg"]};{theme["cell_border"]}' if theme["header"] == "letterhead"
                   else "border-bottom:2px solid #d0d7de;")
    head = "".join(
        f'<th style="padding:8px 10px;font-size:12px;font-weight:700;color:{MUTED};text-align:{align};'
        f'{head_border}">{label_of(comodel, column["field"], column.get("label"))}</th>'
        for column in columns)
    body = "".join(
        f'<td style="padding:8px 10px;font-size:13px;color:{TEXT};text-align:{align};{theme["cell_border"]}">'
        f'{value(comodel, column["field"], "line", words)}</td>'
        for column in columns)
    return (section_title(spec.get("title") or field.string, theme)
            + '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="border-collapse:collapse;">'
            f"<thead><tr>{head}</tr></thead>"
            f'<tbody><tr t-foreach="object.{spec["field"]}" t-as="line">{body}</tr></tbody></table>')


def box(model, spec, theme, align, words, x):
    if not spec or not (spec.get("items") or spec.get("title")):
        return ""
    kind = theme["box"]
    side = "right" if align == "right" else "left"
    if kind == "card":
        style = f"background-color:#f6f8fa;border-radius:12px;padding:16px 20px;border-{side}:4px solid {x['accent']};"
    elif kind == "tinted":
        style = f"background-color:#fff8e6;padding:14px 18px;border-{side}:4px solid {x['accent']};"
    else:
        style = "padding:14px 18px;border:1px solid #d0d7de;"
    items = "".join(f'<li style="margin:6px 0;">{text(model, item, words)}</li>' for item in spec.get("items") or [])
    title = (f'<div style="font-weight:700;font-size:14px;margin-bottom:6px;color:{TEXT};">'
             f'{literal(spec["title"].strip())}</div>') if (spec.get("title") or "").strip() else ""
    listed = f'<ol style="margin:0;padding-{side}:20px;font-size:14px;line-height:1.6;">{items}</ol>' if items else ""
    # One style attribute: QWeb would let the computed one replace a fixed one
    return f'<div t-attf-style="margin:22px 0 4px;text-align:{align};{style}">{title}{listed}</div>'


def button(model, spec, theme, x, align="left"):
    if not spec or not (spec.get("label") or "").strip():
        return ""
    href = '{{ object.get_base_url() }}/mail/view?model=%s&amp;res_id={{ object.id }}' % model._name
    label = literal(spec["label"].strip())
    if theme["header"] == "line":
        return (f'<p style="margin:22px 0 0;"><a t-attf-href="{href}" '
                f't-attf-style="color:{x["accent"]};font-weight:700;text-decoration:none;">{label} '
                f'{"&#8592;" if align == "right" else "&#8594;"}</a></p>')
    return (f'<p style="margin:26px 0 0;"><a t-attf-href="{href}" '
            f't-attf-style="display:inline-block;background-color:{x["accent"]};color:{x["accent_text"]};'
            f'padding:11px 24px;border-radius:{theme["button_radius"]};font-size:14px;font-weight:700;'
            f'text-decoration:none;">{label}</a></p>')


def header(theme, title_markup, align, words, x):
    kind = theme["header"]
    radius = theme["radius"]
    company = x["company"]
    if kind == "band":
        return (f'<tr><td t-attf-style="background-color:{x["accent"]};padding:20px 32px;'
                f'border-radius:{radius} {radius} 0 0;text-align:{align};">'
                f'<div t-attf-style="font-size:{theme["title_size"]};font-weight:700;color:{x["accent_text"]};">'
                f"{title_markup}</div></td></tr>")
    if kind == "hero":
        return (f'<tr><td t-attf-style="background-color:{x["accent"]};padding:34px 32px 30px;'
                f'border-radius:{radius} {radius} 0 0;text-align:{align};">'
                '<div t-attf-style="font-size:12px;letter-spacing:1.2px;text-transform:uppercase;'
                f'color:{x["accent_text"]};opacity:.85;" t-out="{company}.name"/>'
                f'<div t-attf-style="margin-top:8px;font-size:{theme["title_size"]};line-height:1.25;'
                f'font-weight:700;color:{x["accent_text"]};">{title_markup}</div></td></tr>')
    if kind == "line":
        return (f'<tr><td t-attf-style="border-top:4px solid {x["accent"]};padding:26px 32px 0;text-align:{align};">'
                f'<div style="font-size:{theme["title_size"]};font-weight:700;color:{TEXT};">{title_markup}</div>'
                "</td></tr>")
    # A letterhead: the logo and the company, then the reference
    far = "left" if align == "right" else "right"
    return ('<tr><td style="padding:24px 32px 14px;border-bottom:2px solid #1f2328;">'
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td style="text-align:{align};vertical-align:middle;">'
            f'<img t-attf-src="/logo.png?company={{{{ {company}.id }}}}" t-att-alt="{company}.name" '
            'style="max-height:52px;max-width:200px;"/></td>'
            f'<td style="text-align:{far};vertical-align:middle;font-size:12px;color:{MUTED};line-height:1.6;">'
            f'<strong style="color:{TEXT};" t-out="{company}.name"/><br/>'
            f'{literal(words["ref"] + ": ")}<t t-out="object.display_name"/></td>'
            "</tr></table></td></tr>"
            f'<tr><td style="padding:22px 32px 0;text-align:{align};">'
            f'<div style="font-size:{theme["title_size"]};font-weight:700;color:{TEXT};">{title_markup}</div>'
            "</td></tr>")


def footer(x):
    company = x["company"]
    return ('<tr><td style="padding:22px 32px 26px;border-top:1px solid #eaeef2;text-align:center;'
            'font-size:12px;line-height:1.6;color:#6e7781;">'
            f'<img t-attf-src="/logo.png?company={{{{ {company}.id }}}}" t-att-alt="{company}.name" '
            'style="max-height:40px;max-width:160px;"/>'
            f'<div style="margin-top:8px;font-size:13px;font-weight:700;color:{TEXT};" t-out="{company}.name"/>'
            f"<div dir=\"ltr\" t-out=\"' · '.join([part for part in ({company}.phone, {company}.email, {company}.website) if part])\"/>"
            "</td></tr>")


def expressions(model):
    """:return: QWeb of the company the email speaks for (the record's, else
    the sender's) and of its email colours, from the fields this version of
    Odoo has.

    Written out where they are used rather than set once with t-set: in an
    email template, QWeb prints the text that follows a t-set as markup, which
    would let the written text through unescaped."""
    field = model._fields.get("company_id")
    if field is not None and field.type == "many2one" and field.comodel_name == "res.company":
        company = "(object.company_id or user.company_id)"
    else:
        company = "user.company_id"
    fields = model.env["res.company"]._fields
    accent = " or ".join([f"{company}.{name}" for name in ("email_secondary_color", "primary_color") if name in fields]
                         + [repr(FALLBACK_ACCENT)])
    accent_text = " or ".join([f"{company}.{name}" for name in ("email_primary_color",) if name in fields] + ["'#ffffff'"])
    return {"company": company, "accent": "{{ %s }}" % accent, "accent_text": "{{ %s }}" % accent_text}


def build_body(model, design):
    """:param design: the parts, as checked by the tool
    :return: the QWeb of the body of the template"""
    style = design["style"]
    theme = THEMES[style]
    right_to_left = design["right_to_left"]
    align = "right" if right_to_left else "left"
    words = WORDS[right_to_left]
    x = expressions(model)
    title_markup = text(model, design.get("title") or design["subject"], words)

    body = []
    if (design.get("greeting") or "").strip():
        body.append(f'<p style="margin:0 0 16px;font-size:15px;color:{TEXT};">{text(model, design["greeting"].strip(), words)}</p>')
    body.append(paragraphs(model, design.get("intro"), words, style))
    body.append(details(model, design.get("details") or {}, theme, align, words))
    body.append(lines_table(model, design.get("lines"), theme, align, words))
    body.append(box(model, design.get("box"), theme, align, words, x))
    body.append(button(model, design.get("button"), theme, x, align))
    if (design.get("closing") or "").strip() or design.get("signature"):
        closing = text(model, (design.get("closing") or "").strip(), words)
        signature = (f'<br/><strong style="color:{TEXT};" t-out="user.name"/>' if design.get("signature") else "")
        body.append(f'<p style="margin:26px 0 0;font-size:14px;line-height:1.6;color:{TEXT};">{closing}{signature}</p>')

    top = "26px" if theme["header"] in ("line", "letterhead") else "28px"
    parts = [
        f'<div dir="{"rtl" if right_to_left else "ltr"}" style="margin:0;padding:24px 12px;'
        f'background-color:{theme["page"]};font-family:{theme["font"]};">',
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="max-width:600px;margin:0 auto;background-color:#ffffff;border:{theme["card_border"]};'
        f'border-radius:{theme["radius"]};border-collapse:separate;color:{TEXT};'
        f'{"box-shadow:0 6px 24px rgba(31,35,40,.08);" if style == "modern" else ""}">',
        header(theme, title_markup, align, words, x),
        f'<tr><td style="padding:{top} 32px 30px;text-align:{align};">{"".join(body)}</td></tr>',
    ]
    if design.get("company_footer", True):
        parts.append(footer(x))
    parts += ["</table>", "</div>"]
    return "".join(parts)
