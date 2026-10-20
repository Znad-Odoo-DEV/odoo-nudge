/** @odoo-module **/

import { Component, onMounted, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export const REFRESH_MS = 5 * 60 * 1000;

/**
 * The last seven days of a tile as small bars. Chart.js of the web client
 * draws them when it loads; until then (and where it cannot load) plain bars
 * say the same.
 */
export class NudgeSparkline extends Component {
    static template = "nudge.NudgeSparkline";
    static props = { series: Array, label: String };

    setup() {
        this.canvas = useRef("canvas");
        this.state = useState({ drawn: false });
        this.chart = null;
        onMounted(() => this.draw());
        onWillUnmount(() => this.chart?.destroy());
    }

    get bars() {
        const peak = Math.max(1, ...this.props.series);
        return this.props.series.map((value) => ({ value, height: Math.round((value / peak) * 100) }));
    }

    async draw() {
        try {
            await loadBundle("web.chartjs_lib");
            const Chart = window.Chart;
            if (!Chart || !this.canvas.el) {
                return;
            }
            const style = getComputedStyle(this.canvas.el);
            this.chart = new Chart(this.canvas.el, {
                type: "bar",
                data: {
                    labels: this.props.series.map((_value, index) => String(index + 1)),
                    datasets: [{ data: this.props.series, backgroundColor: style.color || "#6c4df6", borderRadius: 2 }],
                },
                options: {
                    animation: false,
                    responsive: false,
                    events: [],
                    plugins: { legend: { display: false }, tooltip: { enabled: false } },
                    scales: { x: { display: false }, y: { display: false, beginAtZero: true } },
                },
            });
            this.state.drawn = true;
        } catch {
            // the plain bars stay
        }
    }
}

export class NudgeHome extends Component {
    static template = "nudge.NudgeHome";
    static components = { NudgeSparkline };
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            loading: true,
            sections: [],
            says: [],
            layout: { order: [], hidden: [] },
            loginHome: false,
            showHidden: false,
            dragging: null,
        });
        this.timer = null;
        onWillStart(() => this.load(false));
        onMounted(() => {
            this.timer = setInterval(() => this.load(true), REFRESH_MS);
        });
        onWillUnmount(() => clearInterval(this.timer));
    }

    async load(force) {
        const data = await this.orm.call("nudge.home", "get_home", [Boolean(force)]);
        this.state.sections = data.sections;
        this.state.says = data.says;
        this.state.layout = data.layout;
        this.state.loginHome = data.is_login_home;
        this.state.loading = false;
    }

    refresh() {
        return this.load(true);
    }

    isHidden(tile) {
        return this.state.layout.hidden.includes(tile.key);
    }

    orderOf(tile, section) {
        const position = this.state.layout.order.indexOf(tile.key);
        return position >= 0 ? position : this.state.layout.order.length + section.tiles.indexOf(tile);
    }

    visibleTiles(section) {
        return section.tiles
            .filter((tile) => !this.isHidden(tile))
            .sort((a, b) => this.orderOf(a, section) - this.orderOf(b, section));
    }

    get hiddenTiles() {
        return this.state.sections.flatMap((section) => section.tiles).filter((tile) => this.isHidden(tile));
    }

    get hasTiles() {
        return this.state.sections.some((section) => section.tiles.length);
    }

    trendText(tile) {
        if (tile.trend === null || tile.trend === undefined) {
            return "";
        }
        return `${tile.trend > 0 ? "+" : ""}${tile.trend}%`;
    }

    trendClass(tile) {
        if (!tile.trend) {
            return "text-muted";
        }
        return tile.trend > 0 ? "o_nudge_home_up" : "o_nudge_home_down";
    }

    async save(layout) {
        this.state.layout = layout;
        this.state.layout = await this.orm.call("nudge.home", "save_layout", [layout]);
    }

    keysOf(section) {
        return this.visibleTiles(section).map((tile) => tile.key);
    }

    /** every tile shown, in the order shown, so that a move is kept whole */
    shownOrder() {
        return this.state.sections.flatMap((section) => this.keysOf(section));
    }

    move(tile, section, delta) {
        const keys = this.keysOf(section);
        const from = keys.indexOf(tile.key);
        const to = from + delta;
        if (from < 0 || to < 0 || to >= keys.length) {
            return;
        }
        keys.splice(to, 0, keys.splice(from, 1)[0]);
        return this.saveSection(section, keys);
    }

    saveSection(section, keys) {
        const own = section.tiles.map((tile) => tile.key);
        const others = this.shownOrder().filter((key) => !own.includes(key));
        return this.save({ order: [...others, ...keys], hidden: this.state.layout.hidden });
    }

    hide(tile) {
        return this.save({
            order: this.state.layout.order,
            hidden: [...this.state.layout.hidden, tile.key],
        });
    }

    unhide(tile) {
        return this.save({
            order: this.state.layout.order,
            hidden: this.state.layout.hidden.filter((key) => key !== tile.key),
        });
    }

    dragStart(ev, tile) {
        this.state.dragging = tile.key;
        ev.dataTransfer?.setData("text/plain", tile.key);
    }

    dragOver(ev) {
        ev.preventDefault();
    }

    drop(ev, target, section) {
        ev.preventDefault();
        const key = this.state.dragging;
        this.state.dragging = null;
        if (!key || key === target.key) {
            return;
        }
        const keys = this.keysOf(section);
        if (!keys.includes(key)) {
            return;
        }
        keys.splice(keys.indexOf(key), 1);
        keys.splice(keys.indexOf(target.key), 0, key);
        return this.saveSection(section, keys);
    }

    async show(tile) {
        const windowAction = await this.orm.call("nudge.home", "show_tile", [tile.key]);
        return this.action.doAction(windowAction);
    }

    openSaid(item) {
        return this.action.doAction({
            type: "ir.actions.act_window",
            name: item.text,
            res_model: item.model,
            res_id: item.res_id,
            views: [[false, "form"]],
        });
    }

    async toggleLoginHome(ev) {
        this.state.loginHome = await this.orm.call("nudge.home", "set_login_home", [ev.target.checked]);
    }

    kindLabel(item) {
        return {
            approval: _t("Waiting for your approval"),
            proposal: _t("Asked again and again"),
            health: _t("Health check"),
        }[item.kind];
    }
}

registry.category("actions").add("nudge_home", NudgeHome);
