import json
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError

from .nudge_mcp import GROUP, PROTOCOLS, SCOPE

KEY_PLACEHOLDER = "YOUR_NUDGE_KEY"


class NudgeMcpHelp(models.TransientModel):
    """The page that tells how to connect an assistant, with the text ready to
    paste, and tests a key."""
    _name = "nudge.mcp.help"
    _description = "Nudge MCP Setup"

    server_url = fields.Char(compute="_compute_texts")
    is_enabled = fields.Boolean(compute="_compute_texts")
    can_create_key = fields.Boolean(compute="_compute_texts")
    is_https = fields.Boolean(compute="_compute_texts")
    claude_config = fields.Text(string="Claude Desktop", compute="_compute_texts")
    claude_code_command = fields.Text(string="Claude Code", compute="_compute_texts")
    generic_config = fields.Text(string="Other Assistants", compute="_compute_texts")
    legacy_config = fields.Text(string="With a Key (legacy)", compute="_compute_texts")
    curl_example = fields.Text(string="Check from a Terminal", compute="_compute_texts")
    test_key = fields.Char(
        string="Key to Test", help="Paste a key here to see that it works. It is not kept.")
    test_result = fields.Text(readonly=True)

    @api.depends_context("uid")
    def _compute_texts(self):
        Mcp = self.env["nudge.mcp"]
        url = Mcp._server_url()
        for page in self:
            page.server_url = url
            page.is_enabled = Mcp._is_enabled()
            page.can_create_key = self.env.user.has_group(GROUP)
            page.is_https = url.startswith("https://")
            # Signed in with OAuth: the address is all an assistant needs
            page.claude_config = json.dumps({"mcpServers": {"odoo-nudge": {
                "command": "npx", "args": ["-y", "mcp-remote", url],
            }}}, indent=2)
            page.claude_code_command = f"claude mcp add --transport http odoo-nudge {url}"
            page.generic_config = json.dumps({"mcpServers": {"odoo-nudge": {"type": "http", "url": url}}}, indent=2)
            page.legacy_config = json.dumps({"mcpServers": {"odoo-nudge": {
                "command": "npx",
                "args": ["-y", "mcp-remote", url, "--header", "Authorization:${NUDGE_AUTH}"],
                "env": {"NUDGE_AUTH": f"Bearer {KEY_PLACEHOLDER}"},
            }}}, indent=2)
            page.curl_example = (
                f"curl -s {url} \\\n"
                f"  -H 'Authorization: Bearer {KEY_PLACEHOLDER}' \\\n"
                "  -H 'Content-Type: application/json' \\\n"
                "  -d '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}'")

    @api.model
    def action_open(self):
        page = self.create({})
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("External Assistants (MCP)"),
            "res_model": self._name,
            "res_id": page.id,
            "view_mode": "form",
            "target": "current",
        }

    def action_create_key(self):
        return self.env["nudge.mcp.key"].action_open()

    def action_test(self):
        """Call initialize with a key, the way an assistant does, and say what came back."""
        self.ensure_one()
        env = self.env
        Mcp = env["nudge.mcp"]
        key = (self.test_key or "").strip()
        if not Mcp._is_enabled():
            text = env._("Nudge is closed to external assistants: turn it on in Settings, Nudge.")
        elif not key:
            text = env._("Paste a key first.")
        else:
            user = Mcp._authenticate(key)
            if not user:
                text = env._(
                    "This key is not valid: it is unknown, it expired, or its owner may no longer "
                    "make keys for assistants.")
            elif user != env.user:
                text = env._("This key belongs to somebody else.")
            else:
                answer, _headers = Mcp._dispatch({
                    "jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": PROTOCOLS[0], "clientInfo": {"name": "Nudge test"}},
                })
                listing, _headers = Mcp._dispatch({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                info = answer["result"]
                text = env._(
                    "It works. %(server)s answered, protocol %(protocol)s, as %(user)s. %(count)s tools are "
                    "offered to this person.",
                    server=info["serverInfo"]["name"], protocol=info["protocolVersion"],
                    user=user.name, count=len(listing["result"]["tools"]))
        self.write({"test_result": text, "test_key": False})


class NudgeMcpKey(models.TransientModel):
    """Makes the key of the person who asks, of the scope ``nudge_mcp``: it
    opens the assistants and nothing else of Odoo. Legacy: an assistant that
    signs in with OAuth needs no key; keys stay for those that cannot."""
    _name = "nudge.mcp.key"
    _description = "Nudge MCP Key"

    name = fields.Char(string="Name", required=True, default="Claude Desktop",
                       help="What the key is for: the assistant that will hold it.")
    duration = fields.Selection(
        [("30", "1 Month"), ("90", "3 Months"), ("365", "1 Year")], string="Valid For",
        required=True, default="90")
    key = fields.Char(string="Your Key", readonly=True)

    @api.model
    def action_open(self):
        if not self.env.user.has_group(GROUP):
            raise AccessError(self.env._("Ask an administrator to add you to the group External Assistant."))
        return self._reopen(self.create({}))

    def _reopen(self, wizard):
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("New Key for an Assistant (legacy)"),
            "res_model": self._name,
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_create(self):
        self.ensure_one()
        if not self.env.user.has_group(GROUP):
            raise AccessError(self.env._("Ask an administrator to add you to the group External Assistant."))
        if not self.env["nudge.mcp"]._is_enabled():
            raise UserError(self.env._(
                "Nudge is closed to external assistants: an administrator turns it on in Settings."))
        expires = fields.Datetime.now() + timedelta(days=int(self.duration))
        # sudo only lifts the cap of days of the user's groups: the key is theirs
        self.key = self.env["res.users.apikeys"].sudo()._generate(SCOPE, self.name, expires)
        return self._reopen(self)
