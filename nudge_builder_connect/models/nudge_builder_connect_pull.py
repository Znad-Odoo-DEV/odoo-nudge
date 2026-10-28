"""The pull wizard: ask the provider with the person's own sign-in, show what was found, write only what is ticked."""
import base64
import io
import json
import logging
import re

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from odoo.addons.nudge.tools.records import Refused
from odoo.addons.nudge_builder.models.nudge_builder_content import (
    MAX_PICTURE_BYTES, google_host, safe_url)
from odoo.addons.nudge_connect.models.nudge_connector_oauth import START_PATH

from ..tools import parse
from ..tools.rest import Limit, Rest

_logger = logging.getLogger(__name__)

GOOGLE_KEY = "builder_google"
INSTAGRAM_KEY = "builder_instagram"
KEYS = {"drive": GOOGLE_KEY, "business": GOOGLE_KEY, "instagram": INSTAGRAM_KEY}
SCOPES = {
    "drive": ("https://www.googleapis.com/auth/drive.readonly",),
    "business": ("https://www.googleapis.com/auth/business.manage",),
    "instagram": ("instagram_business_basic",),
}
DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
ACCOUNTS = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
LOCATIONS = "https://mybusinessbusinessinformation.googleapis.com/v1/%s/locations"
REVIEWS = "https://mybusiness.googleapis.com/v4/%s/%s/reviews"
INSTAGRAM_MEDIA = "https://graph.instagram.com/me/media"
INSTAGRAM_HOST = "https://graph.instagram.com/"
READ_MASK = "name,title,storefrontAddress,phoneNumbers,regularHours,latlng,metadata"
ACCOUNT_NAME = re.compile(r"accounts/\d{1,30}")
LOCATION_NAME = re.compile(r"locations/\d{1,30}")
MEDIA_ID = re.compile(r"\d{5,30}")

# The bounds of a pull: what the person looks at stays readable, and a provider is asked a few times only
MAX_LIST_FILES = 100
MAX_PICTURES = 50
MAX_PLACES = 50
MAX_REVIEW_PLACES = 10
MAX_REVIEWS = 100
MAX_TEXTS = 150
PAGES = 3
MAX_PIXELS = 60000000
TAKE_SECONDS = 90


def _image_ok(data):
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(data))
        return image.format in ("JPEG", "PNG", "WEBP") and image.size[0] * image.size[1] <= MAX_PIXELS
    except Exception:  # noqa: BLE001 - whatever Pillow cannot read is not a picture
        return False


class NudgeBuilderConnectPull(models.TransientModel):
    _name = "nudge.builder.connect.pull"
    _description = "Website Builder: pull from a connected account"

    site_id = fields.Many2one("nudge.builder.site", required=True, ondelete="cascade")
    source = fields.Selection(
        [("drive", "Google Drive"), ("business", "Google Business Profile"), ("instagram", "Instagram")],
        required=True)
    state = fields.Selection([("setup", "Choose"), ("preview", "Preview")], default="setup")
    signed_in = fields.Boolean(compute="_compute_signed_in")
    drive_link = fields.Char(string="Folder link", help="The link of a Google Drive folder you can open.")
    owner_kind = fields.Selection(
        [("project", "Project"), ("product", "Product"), ("service", "Service"), ("member", "Team member"),
         ("location", "Location")], string="Pictures go to", default="project")
    project_id = fields.Many2one("nudge.builder.project", domain="[('site_id', '=', site_id)]")
    product_id = fields.Many2one("nudge.builder.product", domain="[('site_id', '=', site_id)]")
    service_id = fields.Many2one("nudge.builder.service", domain="[('site_id', '=', site_id)]")
    member_id = fields.Many2one("nudge.builder.member", domain="[('site_id', '=', site_id)]")
    location_id = fields.Many2one("nudge.builder.location", domain="[('site_id', '=', site_id)]")
    take_places = fields.Boolean(string="Places", default=True)
    take_reviews = fields.Boolean(string="Reviews", default=True)
    notes = fields.Text(readonly=True)
    line_ids = fields.One2many("nudge.builder.connect.pull.line", "wizard_id")

    # ------------------------------------------------------------------ the person, the site, the connector
    def _site(self):
        """:return: the site, when this person may work on it: a designer of its company"""
        self.ensure_one()
        site = self.site_id
        site._check_designer()
        if site.company_id not in self.env.companies:  # reading it also asks the record rule of the site
            raise AccessError(self.env._("This website belongs to another company."))
        return site

    def _connector(self):
        key = KEYS[self.source]
        connector = self.env["nudge.connector"].sudo().search([("key", "=", key)], limit=1)
        if not connector:
            raise UserError(self.env._("This connection is not set up. Ask your administrator to install it."))
        try:
            connector.with_env(self.env)._check_usable()
        except Refused as refused:
            raise UserError(str(refused)) from None
        return connector

    def _rest(self, **kwargs):
        return Rest(self.env, self._connector(), SCOPES[self.source], **kwargs)

    @api.depends_context("uid")
    @api.depends("source")
    def _compute_signed_in(self):
        for wizard in self:
            found = False
            if wizard.source:
                found = bool(self.env["nudge.connector.token"].sudo().search_count([
                    ("connector_id.key", "=", KEYS[wizard.source]), ("user_id", "=", self.env.uid)]))
            wizard.signed_in = found

    # ------------------------------------------------------------------ opening
    @api.model
    def _open_for(self, site, source):
        site._check_designer()
        wizard = self.create({"site_id": site.id, "source": source})
        wizard._site()
        return wizard._reopen()

    def _reopen(self):
        self.ensure_one()
        titles = {"drive": self.env._("Pull from Google Drive"),
                  "business": self.env._("Pull from Google Business Profile"),
                  "instagram": self.env._("Pull from Instagram")}
        return {"type": "ir.actions.act_window", "res_model": self._name, "res_id": self.id,
                "view_mode": "form", "views": [(False, "form")], "target": "new", "name": titles[self.source]}

    # ------------------------------------------------------------------ connect, disconnect
    def action_connect(self):
        """Send the person to the consent screen of the provider, as themselves"""
        self.ensure_one()
        self._site()
        connector = self._connector()
        if not (connector.oauth_client_id and connector.oauth_authorize_url and connector.oauth_token_url):
            raise UserError(self.env._(
                "The administrator has not finished setting up %s (Settings, Nudge, Connectors).", connector.name))
        return {"type": "ir.actions.act_url", "url": connector._oauth_link(START_PATH), "target": "new"}

    def action_disconnect(self):
        """Forget the sign-in of this person for this provider (and tell the provider, when it can be told)"""
        self.ensure_one()
        self._site()
        connector = self.env["nudge.connector"].sudo().search([("key", "=", KEYS[self.source])], limit=1)
        if connector:
            connector._oauth_forget(self.env.user)
        return self._reopen()

    def action_refresh(self):
        """Look again at the sign-in, after coming back from the consent screen"""
        self.ensure_one()
        self.invalidate_recordset(["signed_in"])
        return self._reopen()

    # ------------------------------------------------------------------ the preview
    def _owner(self):
        self.ensure_one()
        owner = self["%s_id" % self.owner_kind] if self.owner_kind else False
        if not owner:
            raise UserError(self.env._("Choose the record the pictures go to."))
        if owner.site_id != self.site_id:
            raise UserError(self.env._("The record the pictures go to belongs to another site."))
        return owner

    def action_preview(self):
        self.ensure_one()
        self._site()
        if not self.signed_in:
            raise UserError(self.env._("Connect your account first (the Connect button)."))
        self.line_ids.unlink()
        lines, notes = getattr(self, "_preview_%s" % self.source)()
        self.write({
            "state": "preview", "notes": "\n".join(notes) or False,
            "line_ids": [(0, 0, line) for line in lines],
        })
        return self._reopen()

    def action_back(self):
        self.ensure_one()
        self._site()
        self.line_ids.unlink()
        self.write({"state": "setup", "notes": False})
        return self._reopen()

    def _preview_drive(self):
        folder = parse.drive_folder_id(self.drive_link)
        if not folder:
            raise UserError(self.env._("That is not the link of a Google Drive folder."))
        owner = self._owner()
        have = set(owner.picture_ids.mapped("name"))
        rest = self._rest()
        lines, notes, ignored, big, token = [], [], 0, 0, None
        try:
            for _page in range(PAGES):
                params = {
                    "q": "'%s' in parents and trashed = false and mimeType contains 'image/'" % folder,
                    "fields": "nextPageToken,files(id,name,mimeType,size)", "pageSize": 100, "orderBy": "name",
                    "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
                if token:
                    params["pageToken"] = token
                answer = rest.json(DRIVE_FILES, params)
                for item in answer.get("files") or []:
                    if not isinstance(item, dict) or not parse.valid_file_id(item.get("id")):
                        continue
                    if item.get("mimeType") not in parse.IMAGE_TYPES:
                        ignored += 1
                        continue
                    try:
                        size = int(item.get("size") or 0)
                    except (TypeError, ValueError):
                        size = 0
                    if size > MAX_PICTURE_BYTES:
                        big += 1
                        continue
                    if len(lines) >= MAX_LIST_FILES:
                        notes.append(self.env._("Only the first %s pictures are shown.", MAX_LIST_FILES))
                        break
                    name = parse.clean_text(item.get("name"), 120) or item["id"]
                    lines.append({
                        "kind": "picture", "name": name, "existing": name in have, "selected": name not in have,
                        "info": "%s, %s KB" % (item["mimeType"].split("/")[1].upper(), max(size // 1024, 1)),
                        "payload": json.dumps({"id": item["id"], "name": name})})
                token = answer.get("nextPageToken")
                if not token or len(lines) >= MAX_LIST_FILES:
                    break
        except Limit:
            notes.append(self.env._("Google was asked as often as allowed in one pull: what is shown is the first part."))
        if ignored:
            notes.append(self.env._("%s files were left out: only JPEG, PNG and WebP pictures are taken.", ignored))
        if big:
            notes.append(self.env._("%s pictures were left out: a picture may not be larger than 12 MB.", big))
        if not lines:
            notes.append(self.env._("No picture was found in that folder."))
        return lines, notes

    def _preview_instagram(self):
        owner = self._owner()
        have = set(owner.picture_ids.mapped("name"))
        rest = self._rest()
        lines, notes, skipped = [], [], 0
        url, params = INSTAGRAM_MEDIA, {
            "fields": "id,caption,media_type,media_url,permalink,timestamp", "limit": 25}
        try:
            for _page in range(2):
                answer = rest.json(url, params)
                for item in answer.get("data") or []:
                    if not isinstance(item, dict):
                        continue
                    media = str(item.get("id") or "")
                    if (item.get("media_type") not in ("IMAGE", "CAROUSEL_ALBUM") or not MEDIA_ID.fullmatch(media)
                            or not parse.instagram_media_url_ok(item.get("media_url"))):
                        skipped += 1
                        continue
                    name = "instagram-" + media
                    caption = parse.clean_text(item.get("caption"), 2200)
                    lines.append({
                        "kind": "picture", "name": name, "existing": name in have, "selected": name not in have,
                        "info": ((item.get("timestamp") or "")[:10] + " " + caption[:60]).strip(),
                        "caption": caption or False,
                        "payload": json.dumps({"id": media, "url": item["media_url"], "name": name})})
                paging = answer.get("paging") if isinstance(answer.get("paging"), dict) else {}
                following = paging.get("next")
                # The token goes only to Instagram's own address, whatever the answer says
                if not (isinstance(following, str) and following.startswith(INSTAGRAM_HOST)):
                    break
                url, params = following, None
        except Limit:
            notes.append(self.env._("Instagram was asked as often as allowed in one pull: what is shown is the first part."))
        if skipped:
            notes.append(self.env._("%s posts were left out: only pictures are taken, not videos.", skipped))
        if not lines:
            notes.append(self.env._("No picture was found on the account."))
        return lines, notes

    def _preview_business(self):
        if not (self.take_places or self.take_reviews):
            raise UserError(self.env._("Tick places, reviews or both."))
        site = self.site_id
        days = (self.env._("Monday"), self.env._("Tuesday"), self.env._("Wednesday"), self.env._("Thursday"),
                self.env._("Friday"), self.env._("Saturday"), self.env._("Sunday"))
        closed = self.env._("Closed")
        have_places = {(name or "").casefold() for name in site.location_ids.mapped("name")}
        have_reviews = {((record.author or "").casefold(), (record.quote or "").strip())
                        for record in site.testimonial_ids}
        rest = self._rest()
        lines, notes, places, empty = [], [], [], 0
        try:
            accounts = [row for row in (rest.json(ACCOUNTS, {"pageSize": 20}).get("accounts") or [])
                        if isinstance(row, dict) and ACCOUNT_NAME.fullmatch(str(row.get("name") or ""))][:3]
            if not accounts:
                raise UserError(self.env._("No Business Profile was found for this Google account."))
            for account in accounts:
                token = None
                for _page in range(2):
                    params = {"readMask": READ_MASK, "pageSize": 100}
                    if token:
                        params["pageToken"] = token
                    answer = rest.json(LOCATIONS % account["name"], params)
                    for row in answer.get("locations") or []:
                        if isinstance(row, dict) and LOCATION_NAME.fullmatch(str(row.get("name") or "")):
                            places.append((account["name"], row))
                    token = answer.get("nextPageToken")
                    if not token or len(places) >= MAX_PLACES:
                        break
            places = places[:MAX_PLACES]
            if self.take_places:
                for _account, row in places:
                    values = parse.place_values(row, days, closed)
                    if not values.get("name"):
                        continue
                    exists = values["name"].casefold() in have_places
                    bits = [values.get(key) for key in ("street", "city", "phone") if values.get(key)]
                    lines.append({
                        "kind": "location", "name": values["name"], "existing": exists, "selected": not exists,
                        "info": ", ".join(bits) + (" - " + self.env._("with hours") if values.get("hours") else ""),
                        "payload": json.dumps(values)})
            if self.take_reviews:
                counted = 0
                for account, row in places[:MAX_REVIEW_PLACES]:
                    answer = rest.json(REVIEWS % (account, row["name"]), {"pageSize": 50, "orderBy": "updateTime desc"})
                    for review in answer.get("reviews") or []:
                        if not isinstance(review, dict) or counted >= MAX_REVIEWS:
                            continue
                        quote = parse.clean_text(review.get("comment"), 1500)
                        reviewer = review.get("reviewer") if isinstance(review.get("reviewer"), dict) else {}
                        author = parse.first_name(reviewer.get("displayName"))
                        rating = parse.stars(review.get("starRating"))
                        if not quote:
                            empty += 1
                            continue
                        counted += 1
                        exists = (author.casefold(), quote) in have_reviews
                        lines.append({
                            "kind": "review", "name": author or self.env._("Customer"), "existing": exists,
                            "selected": False,  # a review is taken only when the person ticks it
                            "info": ("*" * rating + " " if rating else "") + quote[:90],
                            "payload": json.dumps({"author": author, "quote": quote, "rating": rating})})
        except Limit:
            notes.append(self.env._("Google was asked as often as allowed in one pull: what is shown is the first part."))
        if empty:
            notes.append(self.env._("%s reviews without a comment were left out.", empty))
        if self.take_reviews:
            notes.append(self.env._("Reviews are copied word for word and none is ticked: tick the ones to show."))
        if not lines:
            notes.append(self.env._("Nothing was found."))
        return lines, notes

    # ------------------------------------------------------------------ the yes
    def action_take(self):
        self.ensure_one()
        site = self._site()
        picked = self.line_ids.filtered("selected")
        if not picked:
            raise UserError(self.env._("Tick what to take first."))
        pictures = picked.filtered(lambda line: line.kind == "picture")
        if len(pictures) > MAX_PICTURES or len(picked) - len(pictures) > MAX_TEXTS:
            raise UserError(self.env._("Too many are ticked at once: at most %(pictures)s pictures and %(texts)s "
                                       "places and reviews.", pictures=MAX_PICTURES, texts=MAX_TEXTS))
        done = {"picture": 0, "location": 0, "review": 0}
        skipped = 0
        if pictures:
            skipped += self._take_pictures(pictures, done)
        for line in picked - pictures:
            try:
                values = json.loads(line.payload or "{}")
                with self.env.cr.savepoint():
                    if line.kind == "location":
                        self._create_location(site, values)
                    else:
                        self._create_review(site, values)
                done[line.kind] += 1
            except (ValidationError, UserError, ValueError, TypeError):
                skipped += 1
        message = self.env._(
            "Added: %(pictures)s pictures, %(places)s places, %(reviews)s reviews. Left out: %(skipped)s.",
            pictures=done["picture"], places=done["location"], reviews=done["review"], skipped=skipped)
        return {"type": "ir.actions.client", "tag": "display_notification",
                "params": {"type": "success" if sum(done.values()) else "warning",
                           "message": message, "sticky": False,
                           "next": {"type": "ir.actions.act_window_close"}}}

    def _take_pictures(self, lines, done):
        """Download the ticked pictures with the person's sign-in and add them to the record chosen. A picture
        that is too big, not a picture, or fails to come is left out; the rest are taken.

        :return: how many were left out"""
        owner = self._owner()
        field = "%s_id" % self.owner_kind
        rest = self._rest(max_calls=len(lines) + 5, budget=TAKE_SECONDS)
        base = max(owner.picture_ids.mapped("sequence") or [0])
        skipped = 0
        for number, line in enumerate(lines, 1):
            try:
                payload = json.loads(line.payload or "{}")
                if self.source == "drive":
                    if not parse.valid_file_id(payload.get("id")):
                        raise ValueError
                    data = rest.bytes(DRIVE_FILES + "/" + payload["id"],
                                      {"alt": "media", "supportsAllDrives": "true"}, cap=MAX_PICTURE_BYTES)
                else:
                    if not parse.instagram_media_url_ok(payload.get("url")):
                        raise ValueError
                    data = rest.bytes(payload["url"], auth=False, cap=MAX_PICTURE_BYTES)
                if not _image_ok(data):
                    raise ValueError
                values = {field: owner.id, "name": parse.clean_text(payload.get("name"), 120) or False,
                          "image": base64.b64encode(data), "sequence": base + 10 * number}
                if self.source == "instagram" and line.with_caption and line.caption:
                    values["alt"] = line.caption[:300]
                with self.env.cr.savepoint():
                    self.env["nudge.builder.picture"].create(values)
                done["picture"] += 1
            except Limit:
                skipped += len(lines) - number + 1
                break
            except (UserError, ValidationError, ValueError, TypeError):
                skipped += 1
        return skipped

    def _create_location(self, site, values):
        values = dict(values)
        region, state = values.pop("region", False), values.pop("state", False)
        maps_url = values.get("maps_url")
        if maps_url and not (safe_url(maps_url) and google_host(maps_url)):
            values.pop("maps_url")
        country = self.env["res.country"].search([("code", "=", region)], limit=1) if region else False
        if country:
            values["country_id"] = country.id
            if state:
                found = self.env["res.country.state"].search(
                    [("country_id", "=", country.id), "|", ("name", "=ilike", state), ("code", "=ilike", state)],
                    limit=1)
                if found:
                    values["state_id"] = found.id
        return self.env["nudge.builder.location"].create(dict(values, site_id=site.id))

    def _create_review(self, site, values):
        quote = parse.clean_text(values.get("quote"), 1500)
        if not quote:
            raise ValueError
        rating = values.get("rating")
        return self.env["nudge.builder.testimonial"].create({
            "site_id": site.id, "author": parse.first_name(values.get("author")) or self.env._("Customer"),
            "quote": quote, "rating": rating if isinstance(rating, int) and 0 <= rating <= 5 else 0})


class NudgeBuilderConnectPullLine(models.TransientModel):
    _name = "nudge.builder.connect.pull.line"
    _description = "Website Builder: one thing found by a pull"
    _order = "id"

    wizard_id = fields.Many2one("nudge.builder.connect.pull", required=True, ondelete="cascade")
    kind = fields.Selection([("picture", "Picture"), ("location", "Place"), ("review", "Review")], required=True)
    selected = fields.Boolean(string="Take")
    name = fields.Char(readonly=True)
    info = fields.Char(readonly=True)
    existing = fields.Boolean(string="Already there", readonly=True)
    caption = fields.Text(readonly=True)
    with_caption = fields.Boolean(string="Keep the caption", help="Keep the Instagram caption as the text of the picture.")
    payload = fields.Text()
