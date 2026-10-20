import { Component, xml } from "@odoo/owl";

import { expect, test, before } from "@odoo/hoot";
import { click, queryAllTexts, queryOne } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

import {
    makeMockEnv,
    mockService,
    mountWithCleanup,
    patchWithCleanup,
    preloadBundle,
} from "@web/../tests/web_test_helpers";

import { NudgeChart, formatChartValue } from "@nudge/components/nudge_chart";
import { NudgeMessage } from "@nudge/components/nudge_message";

preloadBundle("web.chartjs_lib");
before(() => {
    patchWithCleanup(Chart.defaults, { animation: false });
});

const ACTION = {
    type: "ir.actions.act_window",
    name: "Total by Salesperson",
    res_model: "sale.order",
    views: [[false, "graph"], [false, "list"], [false, "form"]],
    view_mode: "graph,list,form",
    domain: [["state", "=", "sale"]],
    context: { graph_mode: "bar", graph_groupbys: ["user_id"], graph_measure: "amount_total" },
};

function chart(extra = {}) {
    return {
        chart_type: "bar",
        title: "Total by Salesperson",
        labels: ["Raghad", "Ebrahim"],
        datasets: [{ label: "Total", data: [150, 30] }],
        measure: "Total",
        currency: { symbol: "AED", position: "after", digits: 2, name: "AED" },
        action: ACTION,
        model: "sale.order",
        note: "",
        ...extra,
    };
}

test("the amounts are written in the currency of the company", () => {
    expect(formatChartValue(1234.5, { symbol: "AED", position: "after", digits: 2 }, "en")).toBe(
        "1,234.50 AED"
    );
    expect(formatChartValue(1234.5, { symbol: "$", position: "before", digits: 2 }, "en")).toBe(
        "$ 1,234.50"
    );
    expect(formatChartValue(7, null, "en")).toBe("7");
});

test("a chart is drawn with its labels and its series", async () => {
    await makeMockEnv();
    await mountWithCleanup(NudgeChart, { props: { chart: chart() } });
    await animationFrame();
    const instance = Chart.getChart(queryOne(".o_nudge_chart canvas"));
    expect(instance.config.type).toBe("bar");
    expect(instance.data.labels).toEqual(["Raghad", "Ebrahim"]);
    expect(instance.data.datasets[0].data).toEqual([150, 30]);
    expect(queryAllTexts(".o_nudge_chart_title")).toEqual(["Total by Salesperson"]);
    // The axis speaks in the currency
    expect(instance.options.scales.y.ticks.callback(100)).toBe("100.00 AED");
});

test("a pie and a line are drawn as asked", async () => {
    await makeMockEnv();
    await mountWithCleanup(NudgeChart, { props: { chart: chart({ chart_type: "pie" }) } });
    await animationFrame();
    expect(Chart.getChart(queryOne(".o_nudge_chart canvas")).config.type).toBe("pie");
    expect(Chart.getChart(queryOne(".o_nudge_chart canvas")).options.plugins.legend.display).toBe(true);
});

test("the labels follow the direction of the page", async () => {
    class Page extends Component {
        static template = xml`<div dir="rtl"><NudgeChart chart="props.chart"/></div>`;
        static components = { NudgeChart };
        static props = { chart: Object };
    }
    await makeMockEnv();
    await mountWithCleanup(Page, { props: { chart: chart() } });
    await animationFrame();
    const instance = Chart.getChart(queryOne(".o_nudge_chart canvas"));
    expect(instance.options.plugins.legend.rtl).toBe(true);
    expect(instance.options.plugins.tooltip.rtl).toBe(true);
    expect(instance.options.scales.y.position).toBe("right");
    expect(instance.options.scales.x.reverse).toBe(true);
});

test("Open as report opens the graph view with the domain and the groups", async () => {
    mockService("action", {
        doAction(action) {
            expect.step(`open ${action.res_model} ${action.view_mode} ${JSON.stringify(action.domain)}`);
            expect(action.context.graph_groupbys).toEqual(["user_id"]);
        },
    });
    await makeMockEnv();
    await mountWithCleanup(NudgeChart, { props: { chart: chart() } });
    await animationFrame();
    await click(".o_nudge_chart_open");
    expect.verifySteps(['open sale.order graph,list,form [["state","=","sale"]]']);
});

test("a chart with no report to open has no such button", async () => {
    await makeMockEnv();
    await mountWithCleanup(NudgeChart, { props: { chart: chart({ action: null }) } });
    await animationFrame();
    expect(".o_nudge_chart_open").toHaveCount(0);
    expect(".o_nudge_chart_download").toHaveCount(1);
});

test("Download PNG gives the picture of the canvas", async () => {
    let download = null;
    patchWithCleanup(HTMLAnchorElement.prototype, {
        click() {
            download = this.download;
            expect.step("download");
        },
    });
    await makeMockEnv();
    await mountWithCleanup(NudgeChart, { props: { chart: chart() } });
    await animationFrame();
    await click(".o_nudge_chart_download");
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect.verifySteps(["download"]);
    expect(download).toBe("Total_by_Salesperson.png");
});

test("a message shows its chart", async () => {
    await makeMockEnv();
    await mountWithCleanup(NudgeMessage, {
        props: { role: "assistant", content: "Here is the chart.", state: "complete", chart: chart() },
    });
    await animationFrame();
    expect(".o_nudge_message .o_nudge_chart canvas").toHaveCount(1);
    expect(".o_nudge_chart_open").toHaveCount(1);
});
