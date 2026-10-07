import { Component, onWillRender, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";

import { browser } from "@web/core/browser/browser";
import { usePosition } from "@web/core/position/position_hook";
import { useService } from "@web/core/utils/hooks";

import { NudgeFace } from "../components/nudge_face";

const RING_MARGIN = 6;
// How long the pointer takes to reach the element, then to press it
export const TRAVEL_MS = 700;
export const PRESS_MS = 420;
// Where the tip of the pointer lands on the element, as fractions of its box
const LANDING = { x: 0.5, y: 0.55 };
// The tip of the arrow, in the box of the pointer
const TIP = { x: 2, y: 2 };

function easeInOut(t) {
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

/**
 * @returns {boolean} whether the person asked their system for less motion
 */
export function prefersReducedMotion() {
    try {
        return Boolean(browser.matchMedia("(prefers-reduced-motion: reduce)").matches);
    } catch {
        return false;
    }
}

/**
 * What is drawn on the screen for the step being shown: the rest of the
 * screen shaded, a ring around the element, Nudge's pointer going to the
 * element and pressing it for show, then a box with the text next to it.
 */
export class NudgeGuide extends Component {
    static template = "nudge.Guide";
    static components = { NudgeFace };
    static props = {};

    setup() {
        this.guidance = useService("nudge_guidance");
        this.state = useState(this.guidance.state);
        // Bumped by the frames when the pointer changes what it does
        this.motion = useState({ tick: 0 });
        this.refs = {
            ring: useRef("ring"),
            cursor: useRef("cursor"),
            top: useRef("shadeTop"),
            bottom: useRef("shadeBottom"),
            left: useRef("shadeLeft"),
            right: useRef("shadeRight"),
        };
        this.frame = null;
        // Where the pointer is, kept from one step to the next
        this.cursor = null;
        // The way of the pointer to the element on the screen
        this.journey = null;

        usePosition("bubble", () => this.state.element, {
            position: "bottom-start",
            margin: RING_MARGIN + 10,
        });
        onWillRender(() => this.prepareJourney());
        // The element moves when the page scrolls or when its form changes,
        // and the pointer moves on its own
        const follow = (time) => {
            this.place(time);
            this.frame = browser.requestAnimationFrame(follow);
        };
        useEffect(
            (element) => {
                if (element) {
                    this.place(null);
                    this.frame = browser.requestAnimationFrame(follow);
                }
                return () => browser.cancelAnimationFrame(this.frame);
            },
            () => [this.state.element]
        );
        // Between two guides the pointer is back with Nudge
        useEffect(
            (isActive) => {
                if (!isActive) {
                    this.cursor = null;
                    this.journey = null;
                }
            },
            () => [this.state.isActive]
        );
        onWillUnmount(() => browser.cancelAnimationFrame(this.frame));
    }

    get step() {
        return this.state.steps[this.state.index];
    }

    get text() {
        return this.state.isDetour ? this.state.detourText : this.step?.text;
    }

    get isLast() {
        return this.state.index + 1 >= this.state.steps.length;
    }

    /**
     * A new element on the screen is a new journey of the pointer, decided
     * before the element is drawn so that the first drawing is right.
     */
    prepareJourney() {
        const element = this.state.element;
        if (element && this.journey?.element !== element) {
            this.journey = {
                element,
                from: this.cursor || this.home(),
                startedAt: null,
                pressedAt: null,
                phase: prefersReducedMotion() ? "shown" : "travel",
            };
        }
    }

    /**
     * What the pointer is doing for the element on the screen: on its way
     * ("travel"), pressing ("press"), or done ("shown").
     */
    get phase() {
        // Read so that a change made by the frames is drawn
        this.motion.tick;
        return this.journey?.phase ?? "shown";
    }

    /**
     * @returns {{ x: number, y: number }} where Nudge lives on the screen
     */
    home() {
        const box = document.querySelector(".o_nudge_avatar")?.getBoundingClientRect();
        if (box?.width) {
            return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
        }
        return { x: 40, y: document.documentElement.clientHeight - 40 };
    }

    /**
     * @param {number|null} time of the frame; null before the first one
     */
    place(time) {
        const element = this.state.element;
        const journey = this.journey;
        if (!element?.isConnected || journey?.element !== element) {
            return;
        }
        const box = element.getBoundingClientRect();
        this.placeRing(box);
        this.placeShade(box);

        const target = { x: box.left + box.width * LANDING.x, y: box.top + box.height * LANDING.y };
        let point = target;
        if (journey.phase === "travel") {
            if (time === null) {
                point = journey.from;
            } else {
                if (journey.startedAt === null) {
                    journey.startedAt = time;
                }
                const progress = Math.min(1, (time - journey.startedAt) / TRAVEL_MS);
                const eased = easeInOut(progress);
                point = {
                    x: journey.from.x + (target.x - journey.from.x) * eased,
                    y: journey.from.y + (target.y - journey.from.y) * eased,
                };
                if (progress >= 1) {
                    journey.pressedAt = time;
                    this.setPhase("press");
                }
            }
        } else if (journey.phase === "press" && time !== null && time - journey.pressedAt >= PRESS_MS) {
            this.setPhase("shown");
        }
        this.cursor = point;
        this.placeCursor(point);
    }

    setPhase(phase) {
        this.journey.phase = phase;
        this.motion.tick++;
    }

    placeRing(box) {
        this.placeBox(
            this.refs.ring.el,
            box.top - RING_MARGIN,
            box.left - RING_MARGIN,
            box.width + 2 * RING_MARGIN,
            box.height + 2 * RING_MARGIN
        );
    }

    /**
     * Four shades around the hole, which is the ring and what is inside it.
     */
    placeShade(box) {
        const root = document.documentElement;
        const width = root.clientWidth;
        const height = root.clientHeight;
        const top = box.top - RING_MARGIN;
        const left = box.left - RING_MARGIN;
        const right = box.right + RING_MARGIN;
        const bottom = box.bottom + RING_MARGIN;
        this.placeBox(this.refs.top.el, 0, 0, width, Math.max(0, top));
        this.placeBox(this.refs.bottom.el, bottom, 0, width, Math.max(0, height - bottom));
        this.placeBox(this.refs.left.el, top, 0, Math.max(0, left), bottom - top);
        this.placeBox(this.refs.right.el, top, right, Math.max(0, width - right), bottom - top);
    }

    placeCursor(point) {
        const cursor = this.refs.cursor.el;
        if (cursor) {
            cursor.style.top = `${point.y - TIP.y}px`;
            cursor.style.left = `${point.x - TIP.x}px`;
        }
    }

    placeBox(el, top, left, width, height) {
        if (!el) {
            return;
        }
        el.style.top = `${top}px`;
        el.style.left = `${left}px`;
        el.style.width = `${width}px`;
        el.style.height = `${height}px`;
    }
}
