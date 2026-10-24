"""The printed documents with the identity of the company, in Developer mode:
the quotation, the invoice, the delivery slip, the payslip.

Nothing of the standard template is edited. The design is an extension view
of it, an xpath on the page of the report, which Odoo combines with the
original the way Studio does: a header band with the logo and the company, the
colours of the company, a totals block, a footer with the bank, the VAT and the
legal text, a watermark for drafts. It reads the company when the report is
printed (so a second company prints its own), and bakes into the view only
what was chosen: the colours, the preset, the direction.

Before the card, the extension is made in a savepoint that is rolled back and
the report is rendered for a real record the person may read; the card shows
the difference it makes to the template and the page as it prints.
"""
from odoo import _
import difflib
import re

from lxml import etree

from odoo.exceptions import UserError, ValidationError

from . import base
from .building import MAX_DIFF_LINES, DryRun, pretty, register
from .developer import Building, check_developing
from .records import Refused, cut, get_model, get_records, refusing

STYLES = ("clean", "bold", "classic", "minimal", "custom")
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
DEFAULT_COLORS = ("#1f4e79", "#5b6b7b")
MAX_CSS_CHARS = 3000
MAX_PREVIEW_HTML = 400000
# What a style sheet written by a model may not hold: it is data of the page,
# never a way to fetch or run anything
FORBIDDEN_CSS = re.compile(
    r"@import|url\s*\(|expression\s*\(|javascript:|behaviou?r\s*:|-moz-binding|</?style|<|>|&|\\", re.IGNORECASE)
# The only functions a value may call: colours and simple sizes, never something that fetches
ALLOWED_CSS_FUNCTIONS = {"rgb", "rgba", "hsl", "hsla", "calc", "var", "translate", "rotate", "scale", "min", "max"}
CSS_SRC = re.compile(r"(^|[^a-zA-Z-])src\s*:", re.IGNORECASE)
CSS_CALL = re.compile(r"([a-zA-Z_-]*)\s*\(")
NAME_PREFIX = "Nudge print design"
# The documents: the report, the template whose page is designed, the name of the
# record in it, and the title it takes (Arabic, English)
REPORTS = {
    "quotation": {
        "action": "sale.action_report_saleorder", "template": "sale.report_saleorder_document", "var": "doc",
        "title": ("عرض سعر", "Quotation"), "label": "Quotation"},
    "invoice": {
        "action": "account.account_invoices", "template": "account.report_invoice_document", "var": "o",
        "title": ("فاتورة", "Invoice"), "label": "Invoice"},
    "delivery_slip": {
        "action": "stock.action_report_delivery", "template": "stock.report_delivery_document", "var": "o",
        "title": ("إذن تسليم", "Delivery slip"), "label": "Delivery slip"},
    "payslip": {
        "action": "hr_payroll.action_report_payslip", "template": "hr_payroll.report_payslip", "var": "o",
        "title": ("قسيمة راتب", "Payslip"), "label": "Payslip"},
}
LABELS = {
    "bank": ("البنك", "Bank"), "iban": ("الآيبان", "IBAN"), "vat": ("الرقم الضريبي", "VAT"),
    "total": ("الإجمالي", "Total"), "draft": ("مسودة", "DRAFT"), "phone": ("هاتف", "Phone"),
    "email": ("البريد", "Email"), "qr": ("رمز الاستجابة السريعة", "QR code"),
}
PAGE = "//div[hasclass('page')]"


# ---------------------------------------------------------------- the company

def theme_colors(env):
    """:return: the main colours of the website theme, when there is one and it can be read"""
    if "website.assets" not in env:
        return ()
    try:
        content = env["website.assets"].sudo()._get_content_from_url(
            "/website/static/src/scss/options/colors/user_color_palette.scss")
    except Exception:  # noqa: BLE001 - a theme that cannot be read is no theme
        return ()
    text = content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content or "")
    found = []
    for number in (1, 2):
        match = re.search(rf"['\"]?o-color-{number}['\"]?\s*:\s*(#[0-9a-fA-F]{{6}})", text)
        if match:
            found.append(match.group(1))
    return tuple(found)


def read_company(env, company, arguments):
    """:return: what the design needs to know of the company, and where each came from"""
    primary = arguments.get("primary_color")
    secondary = arguments.get("secondary_color")
    source = "asked"
    if not primary:
        if COLOR.match(company.primary_color or ""):
            primary, source = company.primary_color, "company"
        else:
            theme = theme_colors(env)
            if theme:
                primary, source = theme[0], "website"
            else:
                primary, source = DEFAULT_COLORS[0], "default"
    if not secondary:
        if COLOR.match(company.secondary_color or ""):
            secondary = company.secondary_color
        else:
            theme = theme_colors(env)
            secondary = theme[1] if len(theme) > 1 else DEFAULT_COLORS[1]
    lang = company.partner_id.lang or ""
    arabic = arguments["arabic"] if "arabic" in arguments else lang.startswith("ar")
    bank = company.partner_id.bank_ids[:1]
    return {
        "primary": primary, "secondary": secondary, "source": source, "arabic": bool(arabic),
        "has_logo": bool(company.logo), "has_vat": bool(company.vat), "has_bank": bool(bank),
        "has_footer": bool(company.report_footer), "name": company.name,
    }


# ------------------------------------------------------------------ the design

def label(key, arabic):
    ar, en = LABELS[key]
    return f"{ar} / {en}" if arabic else en


def style_sheet(style, primary, secondary, arabic, custom_css=""):
    """:return: the style sheet of the design: plain CSS, for the engine of the
    PDF, which is an old browser: tables, no flex, no variables"""
    start, end = ("right", "left") if arabic else ("left", "right")
    base_css = f"""
.o_nudge_rd {{ font-size: 12px; color: #222222; direction: {'rtl' if arabic else 'ltr'}; text-align: {start}; }}
.o_nudge_rd_head {{ width: 100%; border-collapse: collapse; margin-bottom: 14px; }}
.o_nudge_rd_head td {{ vertical-align: middle; padding: 6px 10px; }}
.o_nudge_rd_logo img {{ max-height: 70px; max-width: 170px; }}
.o_nudge_rd_title {{ text-align: {end}; }}
.o_nudge_rd_title h1 {{ margin: 0; font-size: 24px; }}
.o_nudge_rd_company {{ font-size: 11px; line-height: 1.5; }}
.o_nudge_rd_totals {{ margin-top: 12px; width: 45%; float: {end}; border-collapse: collapse; }}
.o_nudge_rd_totals td {{ padding: 6px 10px; font-size: 14px; font-weight: bold; }}
.o_nudge_rd_footer {{ clear: both; margin-top: 18px; padding-top: 8px; font-size: 10px; line-height: 1.6; }}
.o_nudge_rd_qr {{ width: 84px; height: 84px; border: 1px dashed #888888; color: #888888; font-size: 9px;
    text-align: center; line-height: 84px; float: {end}; margin-{start}: 12px; }}
.o_nudge_rd_watermark {{ position: fixed; top: 38%; left: 8%; width: 84%; text-align: center; font-size: 120px;
    font-weight: bold; color: rgba(0, 0, 0, 0.07); transform: rotate(-28deg); -webkit-transform: rotate(-28deg);
    z-index: -1; }}
"""
    if arabic:
        base_css += """
.o_nudge_rd_page, div.page { direction: rtl; text-align: right; }
div.page table { direction: rtl; }
"""
    if style == "bold":
        specific = f"""
.o_nudge_rd_head {{ background-color: {primary}; color: #ffffff; }}
.o_nudge_rd_head td, .o_nudge_rd_title h1 {{ color: #ffffff; }}
div.page table thead th {{ background-color: {primary}; color: #ffffff; border: 1px solid {primary}; }}
.o_nudge_rd_totals td {{ background-color: {secondary}; color: #ffffff; }}
.o_nudge_rd_footer {{ border-top: 4px solid {primary}; }}
"""
    elif style == "classic":
        specific = f"""
.o_nudge_rd {{ font-family: Georgia, 'Times New Roman', serif; }}
.o_nudge_rd_head {{ border-top: 3px double {primary}; border-bottom: 3px double {primary}; }}
.o_nudge_rd_title h1 {{ color: {primary}; font-variant: small-caps; letter-spacing: 1px; }}
div.page table thead th {{ border-top: 2px solid {primary}; border-bottom: 2px solid {primary}; background: none; }}
.o_nudge_rd_totals td {{ border-top: 1px solid {primary}; border-bottom: 3px double {primary}; }}
.o_nudge_rd_footer {{ border-top: 3px double {primary}; }}
"""
    elif style == "minimal":
        specific = """
.o_nudge_rd_head { border-bottom: 1px solid #333333; }
.o_nudge_rd_title h1 { font-size: 18px; font-weight: normal; letter-spacing: 3px; text-transform: uppercase; }
div.page table thead th { border-bottom: 1px solid #333333; background: none; font-weight: normal; }
.o_nudge_rd_totals td { border-top: 1px solid #333333; }
.o_nudge_rd_footer { border-top: 1px solid #999999; color: #555555; }
"""
    else:  # clean, and the base of a custom one
        specific = f"""
.o_nudge_rd_head {{ border-bottom: 3px solid {primary}; }}
.o_nudge_rd_title h1 {{ color: {primary}; }}
div.page table thead th {{ background-color: #f3f5f7; color: {primary}; border-bottom: 2px solid {primary}; }}
.o_nudge_rd_totals td {{ border-top: 2px solid {primary}; color: {primary}; }}
.o_nudge_rd_footer {{ border-top: 2px solid {primary}; }}
"""
    return base_css + specific + (custom_css or "")


def check_css(css):
    css = (css or "").strip()
    if len(css) > MAX_CSS_CHARS:
        raise Refused(f"The style sheet is too long: {MAX_CSS_CHARS} characters at most.")
    if FORBIDDEN_CSS.search(css) or "@" in css or CSS_SRC.search(css) or any(
            name.lower() not in ALLOWED_CSS_FUNCTIONS for name in CSS_CALL.findall(css)):
        raise Refused(
            "The style sheet may hold plain rules only: no @import, no url(), no expression(), "
            "no script, no markup, only colours and sizes.")
    return css


def design_arch(kind, model, root_arch, arguments, facts):
    """:return: the arch of the extension view
    :raise Refused: when the template has no page to put the design around"""
    spec = REPORTS[kind]
    var = spec["var"]
    arabic = facts["arabic"]
    if not root_arch.xpath("//div[contains(concat(' ', normalize-space(@class), ' '), ' page ')]"):
        raise Refused(
            f"The template {spec['template']} has no <div class=\"page\">: this version of the report "
            "cannot be designed around its page.")
    company = f"{var}.company_id" if "company_id" in model._fields else "res_company"
    direction = "rtl" if arabic else "ltr"
    title = f"{spec['title'][0]} / {spec['title'][1]}" if arabic else spec["title"][1]
    css = style_sheet(arguments.get("style") or "clean", facts["primary"], facts["secondary"], arabic,
                      check_css(arguments.get("custom_css")))
    number = f'<span t-out="{var}.name"/>' if "name" in model._fields else ""
    drafts = [state for state in ("draft", "sent") if "state" in model._fields and state in dict(
        model._fields["state"]._description_selection(model.env))]
    parts_head = f"""
<div class="o_nudge_rd" dir="{direction}">
  <style>{css}</style>
  <t t-set="nrd_company" t-value="{company}"/>
  <table class="o_nudge_rd_head"><tr>
    <td class="o_nudge_rd_logo" style="width: 28%;"><img t-if="nrd_company.logo" t-att-src="image_data_uri(nrd_company.logo)" alt="Logo"/></td>
    <td class="o_nudge_rd_company">
      <strong t-out="nrd_company.name"/><br/>
      <span t-if="nrd_company.street" t-out="nrd_company.street"/>
      <span t-if="nrd_company.city"> - <t t-out="nrd_company.city"/></span>
      <span t-if="nrd_company.country_id"> - <t t-out="nrd_company.country_id.name"/></span><br/>
      <span t-if="nrd_company.phone">{label('phone', arabic)}: <t t-out="nrd_company.phone"/> </span>
      <span t-if="nrd_company.email">{label('email', arabic)}: <t t-out="nrd_company.email"/></span>
      <div t-if="nrd_company.vat">{label('vat', arabic)}: <t t-out="nrd_company.vat"/></div>
    </td>
    <td class="o_nudge_rd_title"><h1>{title}</h1>{number}</td>
  </tr></table>"""
    if arguments.get("watermark", True) and drafts:
        states = ", ".join(f"'{state}'" for state in drafts)
        parts_head += f"""
  <div t-if="{var}.state in ({states},)" class="o_nudge_rd_watermark">{label('draft', arabic)}</div>"""
    parts_head += "\n</div>"
    pieces = []
    if "amount_total" in model._fields and arguments.get("totals", True):
        currency = f"{var}.currency_id" if "currency_id" in model._fields else "nrd_company.currency_id"
        pieces.append(
            f"""<table class="o_nudge_rd_totals"><tr><td>{label('total', arabic)}</td>
    <td style="text-align: {'left' if arabic else 'right'};"><span t-out="{var}.amount_total" t-options="{{'widget': 'monetary', 'display_currency': {currency}}}"/></td></tr></table>""")
    footer = ""
    if arguments.get("footer", True):
        footer = f"""
  <div class="o_nudge_rd_footer">
    <t t-set="nrd_bank" t-value="nrd_company.partner_id.bank_ids[:1]"/>"""
        if arguments.get("qr_placeholder") and not root_arch.xpath("//*[@id='qrcode']"):
            footer += f"""
    <div class="o_nudge_rd_qr">{label('qr', arabic)}</div>"""
        footer += f"""
    <div t-if="nrd_bank"><strong>{label('bank', arabic)}:</strong> <span t-out="nrd_bank.bank_id.name"/>
      <t t-if="nrd_bank.acc_number"> - {label('iban', arabic)}: <span t-out="nrd_bank.acc_number"/></t></div>
    <div t-if="nrd_company.vat"><strong>{label('vat', arabic)}:</strong> <span t-out="nrd_company.vat"/></div>
    <div t-if="nrd_company.report_footer" t-out="nrd_company.report_footer"/>
  </div>"""
    parts_foot = (f'\n<div class="o_nudge_rd" dir="{direction}">\n<t t-set="nrd_company" t-value="{company}"/>\n'
                  + "\n".join(pieces) + footer + "\n</div>")
    arch = (
        f"<data>\n<xpath expr=\"{PAGE}\" position=\"before\">{parts_head}\n</xpath>\n"
        f"<xpath expr=\"{PAGE}\" position=\"after\">{parts_foot}\n</xpath>\n</data>")
    try:
        etree.fromstring(arch.encode("utf-8"))
    except etree.XMLSyntaxError as error:
        raise Refused(f"The design is not well-formed: {cut(str(error), 200)}") from None
    return arch


# ---------------------------------------------------------------- the trial

def render_html(env, report, sample):
    try:
        html, _kind = report._render_qweb_html(report, sample.ids)
    except TypeError:  # versions where the report renders itself
        html, _kind = report._render_qweb_html(sample.ids)
    return html.decode("utf-8") if isinstance(html, bytes) else html


def try_design(env, root, report, sample, values, earlier):
    """Make the extension view in a savepoint that is rolled back, and print
    the report for the record.
    The designs made before for the template are switched off first, as they
    will be once the person says yes.
    :return: ``(diff lines, html of the page, the PDF engine's word on it)``"""
    html, diff, pdf = "", [], ""
    try:
        with env.cr.savepoint():
            try:
                earlier.write({"active": False})
                env.registry.clear_cache("templates")
                before_text = etree.tostring(root._get_combined_arch(), encoding="unicode")
                env["ir.ui.view"].create(values)
                env.registry.clear_cache("templates")
                after = etree.tostring(root._get_combined_arch(), encoding="unicode")
                diff = [
                    line.rstrip("\n") for line in difflib.unified_diff(
                        pretty(before_text), pretty(after), "before", "after", n=1, lineterm="")
                ][2:]
                if sample:
                    html = render_html(env, report, sample)
                    pdf = pdf_state(env, report, sample)
            except (ValidationError, UserError, ValueError) as error:
                raise Refused(f"Odoo refuses this design: {cut(' '.join(str(error).split()), 500)}") from None
            except Refused:
                raise
            except Exception as error:  # noqa: BLE001
                raise Refused(f"The report cannot be printed with this design: "
                              f"{cut(' '.join(str(error).split()), 500)}") from None
            raise DryRun()
    except DryRun:
        pass
    finally:
        env.invalidate_all()
        env.registry.clear_cache("templates")
    if len(diff) > MAX_DIFF_LINES:
        diff = diff[:MAX_DIFF_LINES] + [f"… {len(diff) - MAX_DIFF_LINES} more line(s)"]
    return diff, html, pdf


def pdf_state(env, report, sample):
    """:return: what the PDF engine says of the page: its size, or why it is not made here"""
    state = "unknown"
    try:
        state = report.get_wkhtmltopdf_state()
    except Exception:  # noqa: BLE001
        pass
    if state != "ok":
        return _("The PDF engine (wkhtmltopdf) is not available here: the page is shown as it prints, in HTML.")
    pdf, _kind = report._render_qweb_pdf(report, sample.ids)
    size = f"{len(pdf) / 1024:.0f}"
    return _("PDF made: %s KB.", size)


def document_name(env, kind):
    """:return: what the document is called, for the person"""
    return {
        "quotation": _("Quotation"), "invoice": _("Invoice"), "delivery_slip": _("Delivery slip"),
        "payslip": _("Payslip"),
    }[kind]


@base.register
class PrepareReportDesign(Building):
    name = "prepare_report_design"
    kind = "create"
    description = (
        "Design a printed document with the identity of the company: the quotation, the invoice, "
        "the delivery slip or the payslip. Nudge reads the company (logo, colours from the company "
        "or the website theme, address, VAT, bank, legal footer) and the standard template of the "
        "document, and makes an extension view of it, never an edit of the original: a header band "
        "with the logo and the company, the colours, a totals block, a footer with the bank, the VAT "
        "and the legal text, a watermark on drafts, and when the company speaks Arabic a right to "
        "left page with Arabic first and English under it. Ask the style (clean, bold, classic or "
        "minimal) and the colours, or take the words of the person and write them as custom_css: "
        "plain CSS rules for the classes of the design (.o_nudge_rd_head, .o_nudge_rd_title, "
        ".o_nudge_rd_footer, .o_nudge_rd_totals) and for table th, td. The card shows the "
        "difference it makes to the template and the page as it prints, for a real record "
        "(record_id, else the newest). Nothing is made by this call: the person says yes or no; "
        "what was made can be undone, or switched off in Settings ‣ Technical ‣ Views."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["report"],
        "properties": {
            "report": {"type": "string", "enum": list(REPORTS)},
            "style": {"type": "string", "enum": list(STYLES), "description": "Default: clean."},
            "primary_color": {"type": "string", "pattern": r"#[0-9a-fA-F]{6}",
                              "description": "Hex, such as #1f4e79. Default: the company's, else the website's."},
            "secondary_color": {"type": "string", "pattern": r"#[0-9a-fA-F]{6}"},
            "custom_css": {"type": "string", "maxLength": MAX_CSS_CHARS,
                           "description": "Plain CSS rules that refine the style, from the words of the person."},
            "arabic": {"type": "boolean", "description": "Right to left, Arabic first. Default: the language of the company."},
            "watermark": {"type": "boolean", "description": "DRAFT behind a draft. Default: yes."},
            "totals": {"type": "boolean", "description": "A totals block under the page. Default: yes."},
            "footer": {"type": "boolean", "description": "The bank, the VAT and the legal text. Default: yes."},
            "qr_placeholder": {"type": "boolean", "description": "A box for the QR of e-invoicing. Default: no."},
            "record_id": {"type": "integer", "minimum": 1, "description": "The record to print the trial for."},
        },
    }

    @refusing
    def run(self, env, arguments, screen):
        check_developing(env)
        kind = arguments["report"]
        spec = REPORTS[kind]
        root = env.ref(spec["template"], raise_if_not_found=False)
        action = env.ref(spec["action"], raise_if_not_found=False)
        if not root or root._name != "ir.ui.view" or not action:
            raise Refused(f"The {spec['label'].lower()} is not in this database: its app is not installed.")
        model = get_model(env, action.model)
        if arguments.get("record_id"):
            sample = get_records(model, [arguments["record_id"]])
        else:
            sample = model.search([], order="id desc", limit=1)
        company = sample.company_id if sample and "company_id" in model._fields else env.company
        facts = read_company(env, company, arguments)
        arguments = dict(arguments, arabic=facts["arabic"])
        before_text = etree.tostring(root._get_combined_arch(), encoding="unicode")
        root_arch = etree.fromstring(before_text.encode("utf-8"))
        arch = design_arch(kind, model, root_arch, arguments, facts)
        style = arguments.get("style") or "clean"
        name = f"{NAME_PREFIX}: {spec['label']} ({style})"
        values = {
            "name": name, "type": "qweb", "inherit_id": root.id, "mode": "extension", "priority": 99,
            "arch_db": arch,
        }
        replaced = self._earlier_designs(env, root)
        diff, html, pdf = try_design(env, root, action, sample, values, replaced)
        sources = {
            "asked": _("the colours asked"), "company": _("the colours of the company"),
            "website": _("the colours of the website theme"), "default": _("the default colours"),
        }
        lines = [
            {"label": _("Document"), "value": document_name(env, kind)},
            {"label": _("Extends"), "value": f"{root.key or root.name} (never edited)"},
            {"label": _("Style"), "value": f"{style} · {facts['primary']} / {facts['secondary']} · "
                                               f"{sources[facts['source']]}"},
            {"label": _("Page"), "value": _("Right to left, Arabic first with English under it")
             if facts["arabic"] else _("Left to right")},
            {"label": _("Company"), "value": ", ".join(filter(None, (
                facts["name"],
                _("with logo") if facts["has_logo"] else _("no logo yet"),
                _("VAT") if facts["has_vat"] else _("no VAT number yet"),
                _("bank") if facts["has_bank"] else _("no bank account yet"),
                _("legal footer") if facts["has_footer"] else _("no legal footer yet"),
            )))},
        ]
        if replaced:
            lines.append({"label": _("Replaces"), "value": ", ".join(replaced.mapped("name")) + " " + _(
                "(switched off, not deleted)")})
        lines.append({"label": _("Result"), "value": "\n".join(diff) or _("No visible difference"),
                      "code": True})
        if sample and html:
            lines.append({"label": _("As %s prints", sample.display_name),
                          "value": cut(pdf, 200),
                          **({"html": html} if len(html) <= MAX_PREVIEW_HTML else {})})
        else:
            lines.append({"label": _("Preview"), "value": _(
                "There is no record yet to print: the design is shown for the first one.")})
        return base.ToolResult(
            "The person is being shown the design, the difference it makes and the page as it prints.",
            pending={
                "summary": _("Design the %(document)s: %(style)s", document=document_name(env, kind).lower(),
                                 style=style),
                "res_model": "ir.ui.view",
                "res_ids": [],
                "record_names": name,
                "values": {"view": values, "replaces": replaced.ids},
                "is_sensitive": False,
                "preview": {"records": [], "lines": lines},
            },
        )

    @staticmethod
    def _earlier_designs(env, root):
        """:return: the designs Nudge made before for this template, still on"""
        views = env["ir.ui.view"].sudo().search([("inherit_id", "=", root.id), ("active", "=", True)])
        return views.filtered(lambda view: view.name.startswith(NAME_PREFIX))

    def execute(self, env, step):
        check_developing(env)
        values = (step.values or {}).get("view") or {}
        if not values.get("inherit_id") or not values.get("arch_db"):
            return base.ToolResult("This design cannot be made.", is_error=True)
        root = env["ir.ui.view"].browse(values["inherit_id"]).exists()
        if not root:
            return base.ToolResult("The template this design extends no longer exists.", is_error=True)
        earlier = self._earlier_designs(env, root)
        earlier.write({"active": False})
        view = env["ir.ui.view"].create(values)
        register(env, view, f"report_design_{root.key or root.id}", step)
        env.registry.clear_cache("templates")
        told = (f" The earlier design{'s' if len(earlier) > 1 else ''} "
                f"({', '.join(earlier.mapped('name'))}) {'are' if len(earlier) > 1 else 'is'} switched off."
                if earlier else "")
        return base.ToolResult(
            f"Made: extension view #{view.id} {view.name!r} on the template {root.key}; the standard "
            f"template is untouched.{told} It is in Settings ‣ Technical ‣ Views; switched off with active "
            "false, deleted, or undone.",
            event={"type": "changed", "model": "ir.ui.view", "ids": view.ids},
            records=("ir.ui.view", view.ids, [view.name]),
        )
