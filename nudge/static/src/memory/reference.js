import { Component, onWillStart, onWillUpdateProps, useState } from "@odoo/owl";

import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";

import { NudgeMessage } from "../components/nudge_message";

// What was said under each answer, asked once per answer for the life of the page (per service: a
// message drawn again, or reopened from the history, does not ask again)
const said = new WeakMap();

function saidOf(service) {
    if (!said.has(service)) {
        said.set(service, new Map());
    }
    return said.get(service);
}

/**
 * Under an answer of Nudge (S15): for a manager, Save as reference; under an answer served from
 * Nudge's memory, who approved it and when; under an offer of close references, the choices.
 * Nothing here calls an AI provider.
 */
export class NudgeReference extends Component {
    static template = "nudge.Reference";
    static props = {
        // The id of the message once it is stored; a text while the answer streams
        messageId: { type: [Number, String], optional: true },
        remembered: { type: Boolean, optional: true },
        state: { type: String, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.nudge = useService("nudge");
        this.info = useState({ label: "", choices: [], loadedFor: null, busy: false });
        // Not awaited: the answer is drawn at once, what is said under it follows
        onWillStart(() => {
            this.load(this.props);
        });
        onWillUpdateProps((next) => {
            this.load(next);
        });
    }

    get isStored() {
        return typeof this.props.messageId === "number";
    }

    get canSave() {
        return Boolean(
            this.isStored &&
                !this.props.remembered &&
                this.props.state === "complete" &&
                this.nudge.state.isManager
        );
    }

    async load(props) {
        if (!props.remembered || typeof props.messageId !== "number" || this.info.loadedFor === props.messageId) {
            return;
        }
        this.info.loadedFor = props.messageId;
        const cache = saidOf(this.nudge);
        if (!cache.has(props.messageId)) {
            // The service itself, not the one bound to this component: the call is kept for the whole page,
            // and a bound one never settles once this component is destroyed, so a later one would wait for ever
            const call = this.env.services.orm.call("nudge.memory", "reference_info", [props.messageId]).catch(
                // Gone, or not of this person: nothing is said under it, and the next one asks again
                () => {
                    cache.delete(props.messageId);
                    return { label: "", choices: [] };
                }
            );
            cache.set(props.messageId, call);
        }
        const info = await cache.get(props.messageId);
        this.info.label = info?.label || "";
        this.info.choices = Array.isArray(info?.choices) ? info.choices : [];
    }

    async save() {
        this.info.busy = true;
        try {
            const action = await this.orm.call("nudge.memory", "action_save_from_message", [this.props.messageId]);
            if (action) {
                await this.action.doAction(action);
            }
        } catch (error) {
            this.notification.add(error?.data?.message || _t("This answer cannot be saved as a reference."), {
                type: "warning",
            });
        } finally {
            this.info.busy = false;
        }
    }

    async choose(memoryId) {
        this.info.busy = true;
        try {
            const result = await this.orm.call("nudge.memory", "answer_choice", [this.props.messageId, memoryId]);
            if (result?.conversation_id) {
                await this.nudge.openConversation(result.conversation_id);
            }
        } catch {
            this.notification.add(_t("This answer cannot be given here any more. Ask the AI instead."), {
                type: "warning",
            });
        } finally {
            this.info.busy = false;
        }
    }
}

// The message knows its id, and shows what is said under it
NudgeMessage.components = { ...NudgeMessage.components, NudgeReference };
NudgeMessage.props = {
    ...NudgeMessage.props,
    messageId: { type: [Number, String], optional: true },
};
