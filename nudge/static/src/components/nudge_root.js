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

        // The page makes room for the avatar (and for the drawer, docked); removed with the component
        useEffect(
            (enabled, docked) => {
                const body = document.body;
                body.classList.toggle("o_nudge_on", Boolean(enabled));
                body.classList.toggle("o_nudge_dock_open", Boolean(docked));
                return () => body.classList.remove("o_nudge_on", "o_nudge_dock_open");
            },
            () => [this.state.isEnabled, this.isDocked]
        );

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

    /** NP-23: the panel is a drawer at the side of a wide screen; on a phone it stays the sheet */
    get isDocked() {
        return Boolean(this.state.dock && this.state.isOpen && !this.state.isMobile);
    }

    /** The colour of the company as the accent, when the person asked for it and the company has one */
    get companyStyle() {
        const color = this.state.companyAccent ? this.state.companyColor : "";
        if (!color) {
            return "";
        }
        const [red, green, blue] = [1, 3, 5].map((index) => parseInt(color.slice(index, index + 2), 16));
        return `--nudge-company: ${color}; --nudge-company-rgb: ${red}, ${green}, ${blue}`;
    }

    get rootStyle() {
        // A docked drawer has its place; the avatar is not moved around then
        const parts = [this.isDocked ? "" : this.placement?.style, this.companyStyle].filter(Boolean);
        return parts.length ? parts.join("; ") : undefined;
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
