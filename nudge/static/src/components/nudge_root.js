import { Component, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";

import { browser } from "@web/core/browser/browser";
import { localization } from "@web/core/l10n/localization";
import { useService } from "@web/core/utils/hooks";

import { makeDraggable, placement } from "../core/drag";
import { NudgeGuide } from "../guidance/nudge_guide";
import { NudgeDictation } from "../voice/nudge_dictation";
import { NudgeFace } from "./nudge_face";
import { NudgePanel } from "./nudge_panel";

const AVATAR_SIZE = 56;

export class NudgeRoot extends Component {
    static template = "nudge.Root";
    static components = { NudgeDictation, NudgeFace, NudgeGuide, NudgePanel };
    static props = {};

    setup() {
        this.nudge = useService("nudge");
        this.state = useState(this.nudge.state);
        this.voiceState = useState(useService("nudge_voice").state);
        this.avatarRef = useRef("avatar");
        // Where the avatar is while it is dragged, before the drop is kept
        this.drag = useState({ position: null });
        this.viewport = useState({ width: browser.innerWidth, height: browser.innerHeight });

        const onResize = () => {
            this.viewport.width = browser.innerWidth;
            this.viewport.height = browser.innerHeight;
        };
        browser.addEventListener("resize", onResize);
        onWillUnmount(() => browser.removeEventListener("resize", onResize));

        useEffect(
            (avatar) => {
                if (!avatar) {
                    return;
                }
                let origin = null;
                return makeDraggable(avatar, {
                    onMove: (dx, dy) => {
                        origin = origin || this.originOf(avatar);
                        this.drag.position = {
                            x: (origin.left + dx) / this.viewport.width,
                            y: (origin.top + dy) / this.viewport.height,
                        };
                        avatar.dataset.dragged = "true";
                    },
                    onDrop: () => {
                        const position = this.drag.position;
                        this.drag.position = null;
                        origin = null;
                        this.nudge.setPosition(position);
                    },
                });
            },
            () => [this.avatarRef.el]
        );
    }

    originOf(avatar) {
        const box = avatar.getBoundingClientRect();
        return { left: box.left, top: box.top };
    }

    get direction() {
        return localization.direction;
    }

    get isListening() {
        return Boolean(this.voiceState.mode);
    }

    /** What ended in the background, or waits, that the person has not looked at */
    get unseenCount() {
        return this.state.news.filter((news) => news.is_unseen).length;
    }

    get placement() {
        // On a phone the sheet covers the screen, and the avatar stays where it is
        if (this.state.isMobile) {
            return null;
        }
        return placement(this.drag.position || this.state.position, this.viewport, AVATAR_SIZE);
    }
}
