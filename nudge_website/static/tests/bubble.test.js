import { expect, getFixture, test } from "@odoo/hoot";
import { click, edit, press, queryAllTexts } from "@odoo/hoot-dom";
import { animationFrame, mockFetch } from "@odoo/hoot-mock";

// The markup of the bubble, as templates.xml (assistant_bubble) renders it
const BUBBLE = /* html */ `
    <div id="nudge_web_root" class="o_nudge_web">
        <button type="button" class="o_nudge_web_open" aria-label="Ask a question">Ask</button>
        <span class="o_nudge_web_failed" hidden="hidden">Sorry, something went wrong. Please try again.</span>
        <section class="o_nudge_web_panel" role="dialog" hidden="hidden">
            <header><button type="button" class="o_nudge_web_close">x</button></header>
            <div class="o_nudge_web_log"><p class="o_nudge_web_bot">Hello, how can I help?</p></div>
            <form class="o_nudge_web_form">
                <input type="text" name="message" required="required"/>
                <button type="submit">Send</button>
            </form>
            <footer><button type="button" class="o_nudge_web_person">Talk to a person</button></footer>
            <form class="o_nudge_web_handover" hidden="hidden">
                <input type="text" name="name"/>
                <input type="email" name="email"/>
                <input type="checkbox" name="consent"/>
                <button type="submit">Send to the team</button>
            </form>
        </section>
    </div>`;

// Hoot refuses a request that is not mocked, and this one is not a route of the
// assistant: a frame of its own has the fetch of the browser, as it was
const ORIGIN = window.location.origin;
let source;

async function readSource() {
    const frame = document.createElement("iframe");
    document.body.appendChild(frame);
    try {
        const response = await frame.contentWindow.fetch(`${ORIGIN}/nudge_website/static/src/js/bubble.js`);
        return await response.text();
    } finally {
        frame.remove();
    }
}

/**
 * The bubble is plain DOM, wired once when the page is ready, and exports nothing:
 * its source is read from the server and run on the markup of the test.
 */
async function startBubble(routes) {
    if (!source) {
        source = await readSource();
    }
    const calls = [];
    mockFetch((url, options) => {
        const params = JSON.parse(options.body).params;
        calls.push({ url, params });
        const result = routes[url]?.(params);
        return { jsonrpc: "2.0", id: null, result };
    });
    getFixture().insertAdjacentHTML("beforeend", BUBBLE);
    new Function(source)();
    return calls;
}

async function ask(text) {
    await click(".o_nudge_web_form input");
    await edit(text);
    await press("Enter");
    await animationFrame();
    await animationFrame();
}

test("the button opens the panel and a question goes to /nudge_website/ask", async () => {
    const calls = await startBubble({
        "/nudge_website/ask": () => ({
            text: "Shipping to Dubai takes three days.",
            products: [
                { name: "Oak Chair for Raghad", price: "AED 120.00", availability: "In stock", url: "/shop/oak-chair" },
            ],
            links: [
                { label: "Shipping policy", url: "/shipping" },
                { label: "Elsewhere", url: "//evil.example" },
            ],
            contact: "/contactus",
        }),
    });
    expect(".o_nudge_web_panel").not.toBeVisible();
    await click(".o_nudge_web_open");
    expect(".o_nudge_web_panel").toBeVisible();

    await ask("How long does shipping take?");

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe("/nudge_website/ask");
    expect(calls[0].params).toEqual({ message: "How long does shipping take?" });
    expect(queryAllTexts(".o_nudge_web_log .o_nudge_web_me")).toEqual(["How long does shipping take?"]);
    expect(queryAllTexts(".o_nudge_web_log .o_nudge_web_bot")).toInclude("Shipping to Dubai takes three days.");
    expect(".o_nudge_web_product a").toHaveAttribute("href", "/shop/oak-chair");
    // An address that would leave the site is not made into a link
    expect(queryAllTexts("a.o_nudge_web_link")).toEqual(["Oak Chair for Raghad", "Shipping policy"]);
    expect(".o_nudge_web_wait").toHaveCount(0);
});

test("talk to a person asks the server and shows the form to leave details", async () => {
    const calls = await startBubble({
        "/nudge_website/handover": () => ({ type: "form", url: "/contactus" }),
    });
    await click(".o_nudge_web_open");
    await click(".o_nudge_web_person");
    await animationFrame();
    expect(calls.map((call) => call.url)).toEqual(["/nudge_website/handover"]);
    expect(".o_nudge_web_handover").toBeVisible();
});

test("the details go to the team with the questions asked, and the conversation only if the visitor agrees", async () => {
    const calls = await startBubble({
        "/nudge_website/ask": () => ({ text: "Let me get the team." }),
        "/nudge_website/handover": (params) =>
            params.name
                ? { text: "Thank you Ebrahim, someone will write to you." }
                : { type: "form", url: "/contactus" },
    });
    await click(".o_nudge_web_open");
    await ask("I want a quote");
    await click(".o_nudge_web_person");
    await animationFrame();

    await click(".o_nudge_web_handover input[name=name]");
    await edit("Ebrahim");
    await click(".o_nudge_web_handover input[name=email]");
    await edit("ebrahim@nudge.example");
    await click(".o_nudge_web_handover button[type=submit]");
    await animationFrame();
    await animationFrame();

    const sent = calls.at(-1).params;
    expect(sent.name).toBe("Ebrahim");
    expect(sent.email).toBe("ebrahim@nudge.example");
    expect(sent.consent).toBe(false);
    expect(sent.transcript).toEqual([]);
    expect(sent.message).toBe("I want a quote");
    expect(queryAllTexts(".o_nudge_web_bot").at(-1)).toBe("Thank you Ebrahim, someone will write to you.");
    expect(".o_nudge_web_handover").not.toBeVisible();
});
