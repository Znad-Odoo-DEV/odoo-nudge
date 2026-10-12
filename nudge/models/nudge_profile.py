import logging
from datetime import timedelta

from odoo import api, fields, models

from ..profile import builder

_logger = logging.getLogger(__name__)

MAX_AGE = timedelta(days=1)
MAX_NOTES_CHARS = 3000


class NudgeProfile(models.Model):
    """One reading of the database: its apps, its custom modules, what they
    add, how it is configured. Nudge answers from the current one."""
    _name = "nudge.profile"
    _description = "Nudge Database Profile"
    _order = "id desc"
    _rec_name = "create_date"

    is_current = fields.Boolean(string="Current", index=True)
    trigger = fields.Selection(
        [
            ("install", "Installation of Nudge"),
            ("modules", "Change of the installed modules"),
            ("scheduled", "Scheduled refresh"),
            ("manual", "Refresh asked by a manager"),
        ],
        required=True, default="manual", readonly=True)
    fingerprint = fields.Char(readonly=True, help="Changes when the installed modules change.")
    duration_ms = fields.Integer(string="Duration (ms)", readonly=True)
    overview_text = fields.Text(
        string="Overview", readonly=True,
        help="What Nudge is told about this database at the start of every conversation.")
    changes = fields.Json(string="Changes Since the Previous Version", readonly=True)
    changes_text = fields.Text(string="What Changed", compute="_compute_changes_text")
    slice_ids = fields.One2many("nudge.profile.slice", "profile_id", string="Content")
    slice_count = fields.Integer(string="Pieces", compute="_compute_slice_count")

    @api.depends("slice_ids")
    def _compute_slice_count(self):
        for profile in self:
            profile.slice_count = len(profile.slice_ids)

    @api.depends("changes")
    def _compute_changes_text(self):
        labels = {
            "added": self.env._("Added"),
            "removed": self.env._("Removed"),
            "changed": self.env._("Changed"),
        }
        for profile in self:
            changes = profile.changes or {}
            lines = [
                f"{labels[kind]}: {', '.join(changes[kind])}"
                for kind in ("added", "removed", "changed") if changes.get(kind)
            ]
            if not lines and profile.changes is not False and profile.changes is not None:
                lines = [self.env._("Nothing changed.")]
            profile.changes_text = "\n".join(lines)

    @api.model
    def _get_current(self):
        return self.sudo().search([("is_current", "=", True)], limit=1)

    @api.model
    def _ensure_current(self, trigger="install"):
        """:return: the current profile, read now if the database has none"""
        return self._get_current() or builder.build(self.env, trigger)

    @api.model
    def _is_outdated(self):
        current = self._get_current()
        if not current:
            return "install"
        if current.fingerprint != builder.fingerprint(self.env):
            return "modules"
        if current.create_date < fields.Datetime.now() - MAX_AGE:
            return "scheduled"
        return False

    @api.model
    def _cron_refresh(self):
        # The documentation of Odoo, when the file that came with the module changed
        self.env["nudge.doc.page"]._load_bundled()
        trigger = self._is_outdated()
        if trigger:
            profile = builder.build(self.env, trigger)
            _logger.info("Nudge: database profile refreshed (%s) in %s ms",
                         trigger, profile.duration_ms)

    def action_refresh(self):
        self.env["nudge.profile"].check_access("write")
        profile = builder.build(self.env, "manual")
        return {
            "type": "ir.actions.act_window",
            "res_model": "nudge.profile",
            "res_id": profile.id,
            "view_mode": "form",
            "target": "current",
        }

    @api.model
    def _get_overview(self):
        """:return: what every conversation starts with: the overview of the
        database and the notes of the business. The same for everybody, so
        that it is read once and reused."""
        profile = self._get_current()
        parts = []
        overview = profile.slice_ids.filtered(lambda piece: piece.key == "overview")
        if overview:
            parts.append("## This database\n" + overview._get_summary())
        notes = self.env["nudge.note"].sudo()._get_text()
        if notes:
            parts.append(
                "## Notes from the people who run this database\n"
                "Written by its administrators. They tell how this business works and names "
                "things: follow them, without quoting them or saying they exist. A name they give "
                "in one language is used in that language: in another, use the usual name.\n" + notes)
        return "\n\n".join(parts)


class NudgeProfileSlice(models.Model):
    _name = "nudge.profile.slice"
    _description = "Nudge Database Profile Piece"
    _order = "kind, key"
    _rec_name = "title"

    profile_id = fields.Many2one("nudge.profile", required=True, index=True, ondelete="cascade")
    kind = fields.Selection(
        [
            ("overview", "Overview"),
            ("module", "Custom Module"),
            ("model", "Records and Fields"),
            ("configuration", "Configuration"),
            ("automation", "Automation"),
            ("security", "Users and Access"),
            ("activity", "Use of the Data"),
        ],
        required=True, readonly=True, index=True)
    key = fields.Char(required=True, readonly=True, index=True)
    title = fields.Char(required=True, readonly=True)
    summary = fields.Text(readonly=True, help="What was read from the database.")
    override_summary = fields.Text(
        string="Correction",
        help="Replaces the summary in what Nudge reads. Kept when the profile is refreshed.")
    data = fields.Json(readonly=True)
    model_name = fields.Char(
        string="Model", readonly=True,
        help="Only people who can read this model are told about this piece.")
    restricted = fields.Boolean(
        string="Managers Only", readonly=True,
        help="Only the managers of Nudge are told about this piece.")
    content_hash = fields.Char(readonly=True)

    def _get_summary(self):
        self.ensure_one()
        return self.override_summary or self.summary or ""


class NudgeNote(models.Model):
    """What the people who run the database want Nudge to know: how they
    name things, how they work."""
    _name = "nudge.note"
    _description = "Nudge Business Note"
    _order = "sequence, id"

    name = fields.Char(string="Subject", required=True)
    body = fields.Text(
        string="Note", required=True,
        help='For instance: We call quotations "offers". Orders above 10,000 need the approval '
             "of the sales manager before confirmation.")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    # A note is written by a manager and counts at once; a lesson is what
    # Nudge wrote down when a person corrected it, and counts once approved
    kind = fields.Selection(
        [("note", "Note"), ("lesson", "Lesson")], default="note", required=True, readonly=True)
    state = fields.Selection(
        [("proposed", "Proposed"), ("approved", "Approved"), ("refused", "Refused")],
        default="approved", required=True, index=True)
    reason = fields.Text(string="Learnt From", readonly=True, help="What the person said.")
    proposed_by = fields.Many2one("res.users", readonly=True)
    approved_by = fields.Many2one("res.users", readonly=True)
    conversation_id = fields.Many2one("nudge.conversation", readonly=True, ondelete="set null")

    def action_approve(self):
        self.check_access("write")
        self.write({"state": "approved", "approved_by": self.env.uid})

    def action_refuse(self):
        self.check_access("write")
        self.write({"state": "refused"})

    @api.model
    def _get_text(self):
        lines, size = [], 0
        for note in self.search([("state", "=", "approved")]):
            line = f"- {note.name}: {' '.join((note.body or '').split())}"
            if size + len(line) > MAX_NOTES_CHARS:
                break
            lines.append(line)
            size += len(line)
        return "\n".join(lines)
