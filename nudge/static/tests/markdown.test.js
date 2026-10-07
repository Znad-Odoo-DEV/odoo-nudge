import { describe, expect, test } from "@odoo/hoot";

import { isSafeUrl, markdownToHtml, renderMarkdown } from "@nudge/core/markdown";

describe.current.tags("headless");

test("paragraphs and line breaks", () => {
    expect(markdownToHtml("One\nTwo\n\nThree")).toBe("<p>One<br>Two</p><p>Three</p>");
    expect(markdownToHtml("")).toBe("");
    expect(markdownToHtml(null)).toBe("");
});

test("inline styles", () => {
    expect(markdownToHtml("Open **Sales** and *then* press `New`, ~~not~~ Save")).toBe(
        "<p>Open <strong>Sales</strong> and <em>then</em> press <code>New</code>, <del>not</del> Save</p>"
    );
    expect(markdownToHtml("2 * 3 * 4 and snake_case_name")).toBe(
        "<p>2 * 3 * 4 and snake_case_name</p>"
    );
});

test("arabic text is kept as written", () => {
    expect(markdownToHtml("افتح **المبيعات** ثم `جديد`")).toBe(
        "<p>افتح <strong>المبيعات</strong> ثم <code>جديد</code></p>"
    );
});

test("lists, headings, quotes and rules", () => {
    expect(markdownToHtml("# Title\n- one\n- two\n\n1. first\n2) second")).toBe(
        "<h4>Title</h4><ul><li>one</li><li>two</li></ul><ol><li>first</li><li>second</li></ol>"
    );
    expect(markdownToHtml("###### Small\n> quoted\n> again\n\n---")).toBe(
        "<h6>Small</h6><blockquote>quoted<br>again</blockquote><hr>"
    );
    expect(markdownToHtml("Steps:\n- one")).toBe("<p>Steps:</p><ul><li>one</li></ul>");
});

test("code blocks", () => {
    expect(markdownToHtml("```python\nx = '<b>'\n**not bold**\n```\nafter")).toBe(
        "<pre><code>x = &#39;&lt;b&gt;&#39;\n**not bold**</code></pre><p>after</p>"
    );
    // An answer still being written has no closing fence yet
    expect(markdownToHtml("```\nhalf")).toBe("<pre><code>half</code></pre>");
});

test("tables", () => {
    expect(markdownToHtml("| Field | Value |\n|---|:--:|\n| **Name** | A |\n| Qty | 2 |")).toBe(
        "<table><thead><tr><th>Field</th><th>Value</th></tr></thead><tbody>" +
            "<tr><td><strong>Name</strong></td><td>A</td></tr><tr><td>Qty</td><td>2</td></tr>" +
            "</tbody></table>"
    );
    expect(markdownToHtml("| not a table |")).toBe("<p>| not a table |</p>");
});

test("links", () => {
    expect(markdownToHtml("[Docs](https://www.odoo.com/documentation?a=1&b=2)")).toBe(
        '<p><a href="https://www.odoo.com/documentation?a=1&amp;b=2" target="_blank" ' +
            'rel="noopener noreferrer">Docs</a></p>'
    );
    expect(markdownToHtml("[Orders](/odoo/action-12)")).toBe(
        '<p><a href="/odoo/action-12">Orders</a></p>'
    );
    expect(markdownToHtml("See https://www.odoo.com/page.")).toBe(
        '<p>See <a href="https://www.odoo.com/page" target="_blank" ' +
            'rel="noopener noreferrer">https://www.odoo.com/page</a>.</p>'
    );
});

test("unsafe links keep their label only", () => {
    expect(isSafeUrl("javascript:alert(1)")).toBe(false);
    expect(isSafeUrl("JaVaScRiPt:alert(1)")).toBe(false);
    expect(isSafeUrl("data:text/html;base64,AAAA")).toBe(false);
    expect(isSafeUrl("//evil.example/x")).toBe(false);
    expect(isSafeUrl("vbscript:x")).toBe(false);
    expect(isSafeUrl("/odoo/action-1")).toBe(true);
    expect(isSafeUrl("mailto:someone@example.com")).toBe(true);

    expect(markdownToHtml("[click](javascript:alert(1))")).toBe("<p>click)</p>");
    expect(markdownToHtml("[click](//evil.example)")).toBe("<p>click</p>");
});

test("html in the text is shown, never interpreted", () => {
    const attacks = [
        "<img src=x onerror=alert(1)>",
        "<script>alert(1)</script>",
        "**<svg onload=alert(1)>**",
        "`<iframe src=javascript:alert(1)>`",
        "# <a href=javascript:alert(1)>x</a>",
        "- <details open ontoggle=alert(1)>",
        "| <b onmouseover=alert(1)>x</b> | y |\n|---|---|\n| <i>z</i> | w |",
        '[x](https://a.example/"onmouseover="alert(1))',
        "> <style>*{display:none}</style>",
    ];
    for (const attack of attacks) {
        // Parsed in a document of its own: nothing loads or runs there, even
        // if the renderer let something through
        const html = String(renderMarkdown(attack));
        const { body } = new DOMParser().parseFromString(html, "text/html");
        const dangerous = body.querySelectorAll(
            "script, img, svg, iframe, style, details, b, i, [onerror], [onload], [onmouseover], [ontoggle]"
        );
        expect([...dangerous]).toHaveLength(0, { message: attack });
        for (const link of body.querySelectorAll("a")) {
            expect(link.getAttribute("href")).toMatch(/^(https?:\/\/|\/(?!\/)|mailto:)/, {
                message: attack,
            });
            expect(link.getAttributeNames().sort()).toEqual(["href", "rel", "target"], {
                message: attack,
            });
        }
    }
});
