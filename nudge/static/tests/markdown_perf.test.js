import { describe, expect, test } from "@odoo/hoot";

import { MarkdownRenderer, markdownToHtml, renderMarkdown } from "@nudge/core/markdown";

describe.current.tags("headless");

// A generous budget: the work is linear, the machine running the suite may not be fast
const BUDGET = 1000;

function timed(callback) {
    const start = performance.now();
    const result = callback();
    return [performance.now() - start, result];
}

const PATHOLOGICAL = {
    "a line of 200,000 characters without a space": "a".repeat(200000),
    "many opening brackets": "[".repeat(200000),
    "many unfinished links": "[a](".repeat(50000),
    "every mark of the inline syntax": "*_`|[~".repeat(33000),
    "an address ending in a long run of punctuation": "http://x" + ".".repeat(100000) + "x",
    "a heading of blanks": "# " + " ".repeat(100000) + "x",
    "a table rule of blanks": "|a|\n|--" + " ".repeat(100000) + "x",
    "many pipes": "|".repeat(200000) + "x",
    "many unfinished bold": "**a ".repeat(50000),
    "many unfinished code spans": "`a ".repeat(50000) + "\n" + "``".repeat(50000),
};

test("a pathological long line renders in a bounded time", () => {
    for (const [name, text] of Object.entries(PATHOLOGICAL)) {
        const [elapsed] = timed(() => markdownToHtml(text));
        expect(elapsed).toBeLessThan(BUDGET, { message: name });
    }
});

test("a pathological long line stays sanitized", () => {
    for (const [name, text] of Object.entries(PATHOLOGICAL)) {
        const [elapsed, html] = timed(() => String(renderMarkdown(text + "<img src=x onerror=alert(1)>")));
        expect(elapsed).toBeLessThan(BUDGET * 2, { message: name });
        const { body } = new DOMParser().parseFromString(html, "text/html");
        expect(body.querySelectorAll("img, script, [onerror]")).toHaveLength(0, { message: name });
    }
});

test("the text of a long line is not lost", () => {
    const html = markdownToHtml("word ".repeat(5000) + "**end**");
    expect(html.startsWith("<p>word word")).toBe(true);
    expect(html).toInclude("<strong>end</strong>");
    expect(markdownToHtml("x".repeat(20000)).length).toBe("<p></p>".length + 20000);
});

test("a stream of 5,000 deltas is rendered a few times, not 5,000", () => {
    let now = 0;
    const renderer = new MarkdownRenderer(() => now);
    let text = "";
    let last;
    const [elapsed] = timed(() => {
        for (let i = 0; i < 5000; i++) {
            text += i % 7 === 0 ? "\n- **item " + i + "** with `code` " : "more words here ";
            now += 1;
            last = renderer.render(text, true);
        }
    });
    expect(elapsed).toBeLessThan(BUDGET);
    expect(renderer.renders).toBeLessThan(200);
    // Behind by a few milliseconds while streaming, then exact once the answer is over
    expect(renderer.stale).toBe(true);
    expect(String(renderer.render(text, false))).toBe(String(renderMarkdown(text)));
    expect(renderer.stale).toBe(false);
    expect(last).not.toBe(undefined);
});

test("the same text is rendered once", () => {
    const renderer = new MarkdownRenderer();
    const first = renderer.render("**one**", false);
    expect(renderer.render("**one**", false)).toBe(first);
    expect(renderer.renders).toBe(1);
    expect(String(first)).toBe("<p><strong>one</strong></p>");
});
