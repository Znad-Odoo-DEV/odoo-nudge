"""The facts and pictures of the company, one record per project, product, service, team member,
testimonial, question and place. The builder writes only from these records (and the company data):
it never invents a project, a client, a price or a picture. A kind with no record is not placed.

Every record belongs to a site, is seen by the website designers of the site's company and by the
system managers, and carries its text in every language of the site (name, summary, description are
translated fields). The Documents app and Website Sale are optional: nothing here depends on them.
"""
import base64
import io
import logging
import re
from urllib.parse import quote, urlparse

from odoo import api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import html_sanitize

_logger = logging.getLogger(__name__)

ALL_KINDS = ("project", "product", "service", "member", "testimonial", "faq", "location")
E164 = re.compile(r"\+[1-9][0-9]{6,14}")
EMAIL = re.compile(r"[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+")
MAX_PICTURE_BYTES = 12 * 1024 * 1024
GOOGLE_HOSTS = ("google.com", "maps.google.com", "www.google.com", "maps.app.goo.gl", "goo.gl", "g.page")
COORDINATES = re.compile(r"@(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)")
PLACE = re.compile(r"/maps/place/([^/@?]+)")


def normalise_whatsapp(value):
    """:return: ``+9715...`` from what a person types ("00971 50-123 4567", "+971 (50) 123 4567"), or the
    text unchanged when it holds anything but digits and the usual separators"""
    text = (value or "").strip()
    if not text:
        return ""
    if re.search(r"[^0-9+\s().\-]", text):
        return text
    digits = re.sub(r"[^0-9]", "", text)
    if text.startswith("00"):
        return "+" + digits[2:]
    return "+" + digits


def valid_whatsapp(value):
    return bool(E164.fullmatch(value or ""))


def safe_url(value):
    """:return: True for an empty value or an http(s) address with a host"""
    if not value:
        return True
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and not re.search(r"[\s<>\"']", value)


def google_host(value):
    try:
        host = (urlparse(value).hostname or "").lower()
    except ValueError:
        return False
    return any(host == known or host.endswith("." + known) for known in GOOGLE_HOSTS)


def strip_metadata(data):
    """:param bytes data: an image
    :return: the same picture, turned upright and without EXIF/GPS data; ``data`` itself when it cannot be read"""
    try:
        from PIL import Image, ImageOps
        image = Image.open(io.BytesIO(data))
        fmt = image.format
        if fmt not in ("JPEG", "PNG", "WEBP"):
            return data
        image = ImageOps.exif_transpose(image)
        out = io.BytesIO()
        kwargs = {"quality": 90} if fmt in ("JPEG", "WEBP") else {}
        image.save(out, format=fmt, **kwargs)
        return out.getvalue()
    except Exception:  # noqa: BLE001 - a picture Pillow cannot read is left to Odoo's own image field
        return data


class NudgeBuilderContent(models.AbstractModel):
    _name = "nudge.builder.content"
    _description = "Nudge Builder content record"
    _order = "featured desc, sequence, id"

    site_id = fields.Many2one("nudge.builder.site", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="site_id.company_id", store=True, index=True)
    sequence = fields.Integer(default=10)
    name = fields.Char(translate=True, required=True)
    summary = fields.Char(translate=True)
    # whole-value translation (a term-by-term one overwrites the English when another language is written); the text is
    # cleaned in create/write below instead of by the field
    description = fields.Html(translate=True, sanitize=False)
    featured = fields.Boolean(help="Shown first.")
    published = fields.Boolean(default=True, help="Untick to keep the record without showing it on the website.")
    cover_image = fields.Image(compute="_compute_cover_image", max_width=256, max_height=256)
    listing_mode = fields.Boolean(related="site_id.listing_mode")
    show_client = fields.Boolean(related="site_id.show_client")

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._clean_text(values) for values in vals_list])

    def write(self, values):
        return super().write(self._clean_text(values))

    @api.model
    def _clean_text(self, values):
        """:return: ``values`` with the description cleaned of scripts and unsafe markup"""
        if values.get("description"):
            values = dict(values, description=html_sanitize(values["description"]))
        return values

    def _pictures(self):
        """:return: the pictures of the record, in order; the models that have pictures override it"""
        return self.env["nudge.builder.picture"]

    def _compute_cover_image(self):
        for record in self:
            first = record._pictures()[:1]
            record.cover_image = first.image if first else False

    def _public(self):
        """:return: the published records, featured first (the order of the model)"""
        return self.filtered("published")


class NudgeBuilderPicture(models.Model):
    _name = "nudge.builder.picture"
    _description = "Nudge Builder picture"
    _order = "sequence, id"

    name = fields.Char(help="The file name or a short label.")
    image = fields.Image(max_width=1920, max_height=1920)
    alt = fields.Char(string="Alternative text", translate=True, help="What the picture shows, for people who cannot see it and for search engines.")
    sequence = fields.Integer(default=10)
    # An integer, not a link: the Documents app is optional. Use document_record() to read it.
    document_id = fields.Integer(string="Document", help="The document of the Documents app this picture was taken from.")
    project_id = fields.Many2one("nudge.builder.project", ondelete="cascade", index=True)
    product_id = fields.Many2one("nudge.builder.product", ondelete="cascade", index=True)
    service_id = fields.Many2one("nudge.builder.service", ondelete="cascade", index=True)
    member_id = fields.Many2one("nudge.builder.member", ondelete="cascade", index=True)
    location_id = fields.Many2one("nudge.builder.location", ondelete="cascade", index=True)
    site_id = fields.Many2one("nudge.builder.site", compute="_compute_site_id", store=True, index=True, ondelete="cascade")
    company_id = fields.Many2one(related="site_id.company_id", store=True, index=True)

    OWNERS = ("project_id", "product_id", "service_id", "member_id", "location_id")

    @api.depends("project_id.site_id", "product_id.site_id", "service_id.site_id", "member_id.site_id",
                 "location_id.site_id")
    def _compute_site_id(self):
        for picture in self:
            owner = next((picture[name] for name in self.OWNERS if picture[name]), False)
            picture.site_id = owner.site_id if owner else False

    @api.constrains("project_id", "product_id", "service_id", "member_id", "location_id", "image")
    def _check_one_owner(self):
        for picture in self:
            if sum(1 for name in self.OWNERS if picture[name]) != 1:
                raise ValidationError(self.env._("A picture belongs to exactly one record."))

    @api.constrains("image")
    def _check_size(self):
        for picture in self:
            if picture.image and len(picture.image) * 3 // 4 > MAX_PICTURE_BYTES:
                raise ValidationError(self.env._("A picture may not be larger than 12 MB."))

    @api.model
    def _clean(self, values):
        if values.get("image"):
            try:
                raw = base64.b64decode(values["image"])
            except (ValueError, TypeError):
                return values
            if len(raw) > MAX_PICTURE_BYTES:
                raise ValidationError(self.env._("A picture may not be larger than 12 MB."))
            values = dict(values, image=base64.b64encode(strip_metadata(raw)))
        return values

    @api.model_create_multi
    def create(self, vals_list):
        return super().create([self._clean(values) for values in vals_list])

    def write(self, values):
        return super().write(self._clean(values))

    def document_record(self):
        """:return: the document of the Documents app, or an empty set when the app is not installed"""
        self.ensure_one()
        if "documents.document" not in self.env or not self.document_id:
            return self.env["nudge.builder.picture"]
        try:
            return self.env["documents.document"].browse(self.document_id).exists()
        except AccessError:
            return self.env["nudge.builder.picture"]

    def action_pull_document(self):
        """Take the picture from the linked document (what the person may read)"""
        for picture in self:
            document = picture.document_record()
            attachment = document.attachment_id if document and "attachment_id" in document._fields else False
            if attachment and (attachment.mimetype or "").startswith("image/"):
                picture.image = attachment.datas
                picture.name = picture.name or document.display_name
        return True


def _picture_field(model_field):
    return fields.One2many("nudge.builder.picture", model_field, string="Pictures")


class NudgeBuilderProject(models.Model):
    _name = "nudge.builder.project"
    _inherit = ["nudge.builder.content"]
    _order = "featured desc, sequence, id"
    _description = "Nudge Builder project (or listing)"

    picture_ids = _picture_field("project_id")
    location = fields.Char(translate=True)
    year = fields.Char(size=4)
    client = fields.Char(help="Shown on the website only when the site says so.")
    category = fields.Char(translate=True)
    status = fields.Selection([("planned", "Planned"), ("ongoing", "Ongoing"), ("done", "Done")], default="done")
    before_picture_id = fields.Many2one("nudge.builder.picture", domain="[('project_id', '=', id)]", ondelete="set null")
    after_picture_id = fields.Many2one("nudge.builder.picture", domain="[('project_id', '=', id)]", ondelete="set null")
    # Real estate listings
    currency_id = fields.Many2one("res.currency", default=lambda self: self.env.company.currency_id)
    price = fields.Monetary()
    area = fields.Float(string="Area (m2)")
    bedrooms = fields.Integer()
    listing_status = fields.Selection([("sale", "For sale"), ("rent", "For rent"), ("sold", "Sold")])

    def _pictures(self):
        return self.picture_ids

    @api.depends("picture_ids.image", "picture_ids.sequence")
    def _compute_cover_image(self):
        super()._compute_cover_image()

    @api.constrains("year")
    def _check_year(self):
        for record in self:
            if record.year and not re.fullmatch(r"(19|20)[0-9]{2}", record.year):
                raise ValidationError(self.env._("The year must have four digits, like 2025."))

    @api.constrains("price", "area", "bedrooms")
    def _check_numbers(self):
        for record in self:
            if record.price < 0 or record.area < 0 or record.bedrooms < 0:
                raise ValidationError(self.env._("The price, the area and the bedrooms cannot be negative."))


class NudgeBuilderProduct(models.Model):
    _name = "nudge.builder.product"
    _inherit = ["nudge.builder.content"]
    _order = "featured desc, sequence, id"
    _description = "Nudge Builder product (showcase or menu item)"

    picture_ids = _picture_field("product_id")
    currency_id = fields.Many2one("res.currency", default=lambda self: self.env.company.currency_id)
    price = fields.Monetary(help="Optional: leave empty to show no price.")
    category = fields.Char(translate=True)
    # An integer, not a link: Website Sale (and its products) is optional. Use product_template() to read it.
    product_tmpl_id = fields.Integer(string="Product", help="The product of the Sales app this item shows, when there is one.")

    def _pictures(self):
        return self.picture_ids

    @api.depends("picture_ids.image", "picture_ids.sequence")
    def _compute_cover_image(self):
        super()._compute_cover_image()

    @api.constrains("price")
    def _check_price(self):
        for record in self:
            if record.price < 0:
                raise ValidationError(self.env._("The price cannot be negative."))

    def product_template(self):
        """:return: the linked product.template, or an empty set"""
        self.ensure_one()
        if "product.template" not in self.env or not self.product_tmpl_id:
            return self.env["nudge.builder.product"]
        try:
            return self.env["product.template"].browse(self.product_tmpl_id).exists()
        except AccessError:
            return self.env["nudge.builder.product"]


class NudgeBuilderService(models.Model):
    _name = "nudge.builder.service"
    _inherit = ["nudge.builder.content"]
    _order = "featured desc, sequence, id"
    _description = "Nudge Builder service"

    picture_ids = _picture_field("service_id")
    icon = fields.Char(help="A FontAwesome class, like fa-wrench.", default="fa-check")
    quote = fields.Boolean(string="Request a quote", help="Show a 'Request a quote' link on this service.")

    def _pictures(self):
        return self.picture_ids

    @api.depends("picture_ids.image", "picture_ids.sequence")
    def _compute_cover_image(self):
        super()._compute_cover_image()

    @api.constrains("icon")
    def _check_icon(self):
        for record in self:
            if record.icon and not re.fullmatch(r"fa-[a-z0-9\-]{1,40}", record.icon):
                raise ValidationError(self.env._("The icon must be a FontAwesome class, like fa-wrench."))


class NudgeBuilderMember(models.Model):
    _name = "nudge.builder.member"
    _inherit = ["nudge.builder.content"]
    _order = "featured desc, sequence, id"
    _description = "Nudge Builder team member"

    picture_ids = _picture_field("member_id")
    role = fields.Char(translate=True)
    specialty = fields.Char(translate=True)
    bio = fields.Text(translate=True)
    linkedin = fields.Char(string="LinkedIn")
    hours = fields.Text(help="Consultation hours, for the doctors of a clinic.")

    def _pictures(self):
        return self.picture_ids

    @api.depends("picture_ids.image", "picture_ids.sequence")
    def _compute_cover_image(self):
        super()._compute_cover_image()

    @api.constrains("linkedin")
    def _check_linkedin(self):
        for record in self:
            if not safe_url(record.linkedin):
                raise ValidationError(self.env._("The LinkedIn link must be a web address starting with https://."))


class NudgeBuilderTestimonial(models.Model):
    _name = "nudge.builder.testimonial"
    _inherit = ["nudge.builder.content"]
    _order = "featured desc, sequence, id"
    _description = "Nudge Builder testimonial"
    _rec_name = "author"

    name = fields.Char(translate=True, required=False, string="Heading")
    author = fields.Char(required=True)
    author_role = fields.Char(translate=True)
    company_name = fields.Char(string="Company")
    quote = fields.Text(translate=True, required=True)
    rating = fields.Integer(default=5)
    image = fields.Image(max_width=512, max_height=512)

    @api.constrains("rating")
    def _check_rating(self):
        for record in self:
            if not 0 <= record.rating <= 5:
                raise ValidationError(self.env._("The rating is from 1 to 5 (0 for none)."))


class NudgeBuilderFaq(models.Model):
    _name = "nudge.builder.faq"
    _inherit = ["nudge.builder.content"]
    _order = "featured desc, sequence, id"
    _description = "Nudge Builder question and answer"
    _rec_name = "question"

    name = fields.Char(string="Question", translate=True, required=True)
    question = fields.Char(related="name", readonly=False)
    answer = fields.Html(related="description", readonly=False)

    @api.model_create_multi
    def create(self, vals_list):
        """question and answer are other names of name and description; a column cannot wait for an inverse"""
        for values in vals_list:
            if "question" in values:
                values.setdefault("name", values.pop("question"))
            if "answer" in values:
                values.setdefault("description", values.pop("answer"))
        return super().create(vals_list)


class NudgeBuilderLocation(models.Model):
    _name = "nudge.builder.location"
    _inherit = ["nudge.builder.content"]
    _order = "featured desc, sequence, id"
    _description = "Nudge Builder location"

    picture_ids = _picture_field("location_id")
    street = fields.Char()
    city = fields.Char()
    state_id = fields.Many2one("res.country.state", domain="[('country_id', '=?', country_id)]")
    country_id = fields.Many2one("res.country")
    zip = fields.Char()
    maps_url = fields.Char(string="Google Maps link")
    latitude = fields.Float(digits=(10, 7))
    longitude = fields.Float(digits=(10, 7))
    phone = fields.Char()
    whatsapp = fields.Char(string="WhatsApp", help="International format: +971501234567.")
    email = fields.Char()
    hours = fields.Text()
    is_main = fields.Boolean(string="Main office")
    maps_embed_src = fields.Char(compute="_compute_maps_embed_src")

    def _pictures(self):
        return self.picture_ids

    @api.depends("picture_ids.image", "picture_ids.sequence")
    def _compute_cover_image(self):
        super()._compute_cover_image()

    def _address_line(self):
        self.ensure_one()
        return ", ".join(part for part in (self.street, self.city, self.state_id.name, self.zip, self.country_id.name) if part)

    @api.depends("maps_url", "latitude", "longitude", "street", "city", "state_id", "zip", "country_id", "name")
    def _compute_maps_embed_src(self):
        """A Google Maps embed address without a key. Every value is percent-encoded, so a name or a
        street with quotes or tags cannot leave the address."""
        for location in self:
            location.maps_embed_src = location._embed_src()

    def _embed_src(self):
        self.ensure_one()
        url = (self.maps_url or "").strip()
        if url and safe_url(url) and google_host(url) and "/maps/embed" in url:
            return url
        if self.latitude or self.longitude:
            return "https://www.google.com/maps?q=%s,%s&output=embed" % (
                quote("%.7f" % self.latitude, safe=""), quote("%.7f" % self.longitude, safe=""))
        if url and safe_url(url) and google_host(url):
            found = COORDINATES.search(url)
            if found:
                return "https://www.google.com/maps?q=%s,%s&output=embed" % (found.group(1), found.group(2))
        query = self._address_line()
        if not query and url and safe_url(url) and google_host(url):
            place = PLACE.search(url)
            query = place.group(1).replace("+", " ") if place else ""
        query = query or self.name or ""
        if not query:
            return False
        return "https://www.google.com/maps?q=%s&output=embed" % quote(query, safe="")

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            if values.get("whatsapp"):
                values["whatsapp"] = normalise_whatsapp(values["whatsapp"])
        return super().create(vals_list)

    def write(self, values):
        if values.get("whatsapp"):
            values = dict(values, whatsapp=normalise_whatsapp(values["whatsapp"]))
        return super().write(values)

    @api.constrains("whatsapp", "email", "maps_url", "latitude", "longitude")
    def _check_contact(self):
        for record in self:
            if record.whatsapp and not valid_whatsapp(record.whatsapp):
                raise ValidationError(self.env._("The WhatsApp number must be in international format, like +971501234567."))
            if record.email and not EMAIL.fullmatch(record.email.strip()):
                raise ValidationError(self.env._("The email address is not valid."))
            if record.maps_url and not (safe_url(record.maps_url) and google_host(record.maps_url)):
                raise ValidationError(self.env._("The map link must be a Google Maps address starting with https://."))
            if not -90 <= record.latitude <= 90 or not -180 <= record.longitude <= 180:
                raise ValidationError(self.env._("The latitude is from -90 to 90 and the longitude from -180 to 180."))

    @api.constrains("is_main", "site_id")
    def _check_one_main(self):
        for record in self.filtered("is_main"):
            if self.search_count([("site_id", "=", record.site_id.id), ("is_main", "=", True)]) > 1:
                raise ValidationError(self.env._("A site has one main office."))


class NudgeBuilderSocial(models.Model):
    _name = "nudge.builder.social"
    _description = "Nudge Builder social link"
    _order = "sequence, id"

    site_id = fields.Many2one("nudge.builder.site", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one(related="site_id.company_id", store=True, index=True)
    sequence = fields.Integer(default=10)
    network = fields.Selection([
        ("facebook", "Facebook"), ("instagram", "Instagram"), ("x", "X"), ("linkedin", "LinkedIn"),
        ("youtube", "YouTube"), ("tiktok", "TikTok"), ("snapchat", "Snapchat"), ("telegram", "Telegram"),
        ("pinterest", "Pinterest")], required=True)
    url = fields.Char(required=True)

    @api.constrains("url")
    def _check_url(self):
        for record in self:
            if not safe_url(record.url):
                raise ValidationError(self.env._("The link must be a web address starting with https://."))
