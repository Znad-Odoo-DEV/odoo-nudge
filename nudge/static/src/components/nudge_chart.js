import { Component, onWillStart, onWillUnmount, useEffect, useRef } from "@odoo/owl";

import { loadBundle } from "@web/core/assets";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";

const PALETTE = [
    "#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948", "#b07aa1", "#ff9da7",
    "#9c755f", "#86bcb6", "#d37295", "#8cd17d",
];

/**
 * How an amount is written: the symbol of the currency of the company, before
 * or after the number as that currency does it, and its decimals.
 */
export function formatChartValue(value, currency, lang) {
    const digits = currency ? currency.digits : 2;
    let text;
    try {
        text = new Intl.NumberFormat(lang || undefined, {
            minimumFractionDigits: currency ? digits : 0,
            maximumFractionDigits: digits,
        }).format(value || 0);
    } catch {
        text = String(value || 0);
    }
    if (!currency || !currency.symbol) {
        return text;
    }
    return currency.position === "before" ? `${currency.symbol} ${text}` : `${text} ${currency.symbol}`;
}

/** The background the chart sits on, so that a downloaded picture is not transparent. */
function backgroundOf(element) {
    for (let node = element; node; node = node.parentElement) {
        const color = getComputedStyle(node).backgroundColor;
        if (color && !/^rgba\(.*,\s*0\)$/.test(color) && color !== "transparent") {
            return color;
        }
    }
    return "#ffffff";
}

/**
 * A chart in a message: bars, a line or a pie, drawn with the Chart.js of
 * Odoo. The person can open the same figures as a report, or keep the picture.
 */
export class NudgeChart extends Component {
    static template = "nudge.Chart";
    static props = { chart: Object };

    setup() {
        this.action = useService("action");
        this.canvasRef = useRef("canvas");
        this.rootRef = useRef("root");
        this.instance = null;
        onWillStart(() => loadBundle("web.chartjs_lib"));
        useEffect(
            () => {
                this.drawChart();
                return () => this.destroy();
            },
            () => [JSON.stringify(this.props.chart)]
        );
        onWillUnmount(() => this.destroy());
    }

    get isRtl() {
        return getComputedStyle(this.rootRef.el || document.body).direction === "rtl";
    }

    get lang() {
        return (document.documentElement.lang || "").replace("_", "-") || undefined;
    }

    destroy() {
        if (this.instance) {
            this.instance.destroy();
            this.instance = null;
        }
    }

    format(value) {
        return formatChartValue(value, this.props.chart.currency, this.lang);
    }

    drawChart() {
        const chart = this.props.chart;
        const canvas = this.canvasRef.el;
        if (!canvas || typeof Chart === "undefined") {
            return;
        }
        const root = this.rootRef.el;
        const text = getComputedStyle(root).color;
        const grid = "rgba(128, 128, 128, 0.25)";
        const rtl = this.isRtl;
        const pie = chart.chart_type === "pie";
        const line = chart.chart_type === "line";
        const datasets = chart.datasets.map((dataset, index) => {
            const color = PALETTE[index % PALETTE.length];
            if (pie) {
                return {
                    label: dataset.label,
                    data: dataset.data,
                    backgroundColor: chart.labels.map((_label, i) => PALETTE[i % PALETTE.length]),
                    borderColor: backgroundOf(root),
                };
            }
            return {
                label: dataset.label,
                data: dataset.data,
                backgroundColor: color,
                borderColor: color,
                fill: false,
                tension: 0.25,
                borderWidth: line ? 2 : 0,
            };
        });
        const options = {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                legend: {
                    display: pie || chart.datasets.length > 1,
                    rtl,
                    textDirection: rtl ? "rtl" : "ltr",
                    labels: { color: text },
                },
                tooltip: {
                    rtl,
                    textDirection: rtl ? "rtl" : "ltr",
                    callbacks: {
                        label: (item) => {
                            const value = pie ? item.raw : item.parsed.y;
                            return `${pie ? item.label : item.dataset.label}: ${this.format(value)}`;
                        },
                    },
                },
            },
        };
        if (!pie) {
            options.scales = {
                x: { reverse: rtl, ticks: { color: text }, grid: { color: grid } },
                y: {
                    position: rtl ? "right" : "left",
                    beginAtZero: true,
                    ticks: { color: text, callback: (value) => this.format(value) },
                    grid: { color: grid },
                },
            };
        }
        this.destroy();
        this.instance = new Chart(canvas, {
            type: chart.chart_type,
            data: { labels: chart.labels, datasets },
            options,
        });
    }

    openReport() {
        if (this.props.chart.action) {
            return this.action.doAction(this.props.chart.action);
        }
    }

    /** The picture of the chart, on the background it is shown on */
    async pngBlob() {
        const source = this.canvasRef.el;
        const out = document.createElement("canvas");
        out.width = source.width;
        out.height = source.height;
        const context = out.getContext("2d");
        context.fillStyle = backgroundOf(this.rootRef.el);
        context.fillRect(0, 0, out.width, out.height);
        context.drawImage(source, 0, 0);
        return new Promise((resolve) => out.toBlob(resolve, "image/png"));
    }

    async downloadPng() {
        const blob = await this.pngBlob();
        if (!blob) {
            return;
        }
        const link = document.createElement("a");
        const url = URL.createObjectURL(blob);
        link.href = url;
        link.download = `${(this.props.chart.title || _t("chart")).replace(/[^\p{L}\p{N}]+/gu, "_")}.png`;
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
}
