/**
 * Nudge agents in Discuss: a direct chat with an agent the person may use (Odoo 19).
 *
 * Discuss offers a chat only with people who have a user, and an agent's partner has none. The server gives the
 * ids of the live agents this person may write to (``nudge_agent_chat_partner_ids``: when the web client starts,
 * and again with each search of "Start a conversation", which also brings the agents found). Here those agents
 * are offered by that search, and their messages and their place among the members of a channel open the chat
 * with them. The chat is opened by the server only for the people the agent is open to.
 */
import { Message } from "@mail/core/common/message";
import "@mail/core/web/message_patch";
import { ChannelMemberList } from "@mail/discuss/core/common/channel_member_list";
import "@mail/discuss/core/web/channel_member_list_patch";
import { DiscussCommandPalette } from "@mail/discuss/core/public_web/discuss_command_palette";
import "@mail/discuss/core/web/discuss_command_palette_patch";
import { cleanTerm } from "@mail/utils/common/format";

import { markEventHandled } from "@web/core/utils/misc";
import { patch } from "@web/core/utils/patch";

/**
 * @param {import("models").Store} store
 * @param {import("models").ResPartner} [partner]
 * @returns {boolean} whether ``partner`` is the partner of an agent this person may write to
 */
export function isNudgeAgentChat(store, partner) {
    return Boolean(
        partner?.id &&
            !partner.main_user_id &&
            store.nudge_agent_chat_partner_ids?.includes(partner.id)
    );
}

patch(DiscussCommandPalette.prototype, {
    buildResults() {
        super.buildResults(...arguments);
        if (!this.store.self_partner) {
            return;
        }
        // Left out by Discuss, which lists the partners of users only
        const shown = new Set(this.commands.map((command) => command.props?.persona?.id));
        const agents = Object.values(this.store["res.partner"].records).filter(
            (partner) =>
                isNudgeAgentChat(this.store, partner) &&
                !shown.has(partner.id) &&
                cleanTerm(partner.displayName).includes(this.cleanedTerm)
        );
        for (const partner of agents) {
            this.commands.push(this.makeDiscussCommand(partner));
        }
    },
});

patch(Message.prototype, {
    hasAuthorClickable() {
        return super.hasAuthorClickable() || isNudgeAgentChat(this.store, this.message.author_id);
    },
    onClickAuthor(ev) {
        const author = this.message.author_id;
        if (isNudgeAgentChat(this.store, author)) {
            // No card for an agent: it has no user; its chat opens
            markEventHandled(ev, "Message.ClickAuthor");
            this.store.openChat({ partnerId: author.id });
            return;
        }
        return super.onClickAuthor(...arguments);
    },
});

patch(ChannelMemberList.prototype, {
    canOpenChatWith(member) {
        return (
            super.canOpenChatWith(member) ||
            (!this.store.inPublicPage && isNudgeAgentChat(this.store, member.partner_id))
        );
    },
    onClickAvatar(ev, member) {
        if (!this.store.inPublicPage && isNudgeAgentChat(this.store, member.partner_id)) {
            this.store.openChat({ partnerId: member.partner_id.id });
            return;
        }
        return super.onClickAvatar(...arguments);
    },
});
