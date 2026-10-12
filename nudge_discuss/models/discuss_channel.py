from odoo import api, models


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    @api.model_create_multi
    def create(self, vals_list):
        channels = super().create(vals_list)
        channels._nudge_join()
        return channels

    def _nudge_join(self):
        """Nudge is a member of the channels people write in: typing "@" in
        #general did not offer it, a channel suggesting its members only"""
        nudge = self.env.ref("nudge_discuss.partner_nudge", raise_if_not_found=False)
        channels = self.filtered(lambda channel: channel.channel_type == "channel")
        if not nudge or not channels:
            return
        Member = self.env["discuss.channel.member"].sudo()
        joined = Member.search([("channel_id", "in", channels.ids), ("partner_id", "=", nudge.id)]).channel_id
        # Created as they are, without the notice and the bus message of an invitation
        Member.create([{"channel_id": channel.id, "partner_id": nudge.id} for channel in channels - joined])

    @api.model
    def _nudge_join_all(self):
        """Called on install and update: the channels made before"""
        self.sudo().with_context(active_test=False).search([("channel_type", "=", "channel")])._nudge_join()
