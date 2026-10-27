import { expect, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";

import { browser } from "@web/core/browser/browser";
import { user } from "@web/core/user";
import { session } from "@web/session";
import { getService, makeMockEnv, mountWithCleanup, patchWithCleanup } from "@web/../tests/web_test_helpers";

import { NudgePanel } from "@nudge/components/nudge_panel";

function streamOf(...events) {
    const body = events.map((event) => JSON.stringify(event) + "\n").join("");
    return new Response(new TextEncoder().encode(body), { status: 200 });
}

async function startNudge() {
    patchWithCleanup(session, { nudge: { is_configured: true } });
    patchWithCleanup(user, { settings: { ...user.settings, nudge_explain: "text" } });
    const requests = [];
    patchWithCleanup(browser, {
        fetch(url, options) {
            if (url !== "/nudge/chat/stream") {
                return super.fetch(...arguments);
            }
            requests.push(Object.fromEntries(options.body.entries()));
            return streamOf(
                { type: "start", conversation_id: 5, message_id: 1, title: "Hi" },
                { type: "delta", text: "Hello" },
                { type: "done" }
            );
        },
    });
    await makeMockEnv();
    return { nudge: getService("nudge"), requests };
}

test("Try it: the agent and the rehearsal travel with every question, New chat keeps them", async () => {
    const { nudge, requests } = await startNudge();
    nudge.openForAgent({ id: 7, name: "Sales assistant", rehearsal: true });
    expect(nudge.state.isOpen).toBe(true);

    await nudge.send("Hello");
    expect(requests[0].nudge_agent_id).toBe("7");
    expect(requests[0].nudge_rehearsal).toBe("1");

    nudge.newConversation();
    expect(nudge.state.agent.id).toBe(7);
    await nudge.send("Again");
    expect(requests[1].nudge_agent_id).toBe("7");
    expect("conversation_id" in requests[1]).toBe(false);

    // Leaving the agent sends plain questions again
    nudge.leaveAgent();
    await nudge.send("Plain");
    expect("nudge_agent_id" in requests[2]).toBe(false);
    expect("nudge_rehearsal" in requests[2]).toBe(false);
});

test("the panel header shows the agent and the Rehearsal badge", async () => {
    const { nudge } = await startNudge();
    nudge.openForAgent({ id: 7, name: "Sales assistant", rehearsal: true });
    await mountWithCleanup(NudgePanel);
    await animationFrame();
    expect(".o_nudge_agent_name").toHaveText("Sales assistant");
    expect(".o_nudge_agent_badge").toHaveText("Rehearsal");
    // The test browser replaces the address of every image by a blank one: the avatar is there, its src is not ours
    expect("img.o_nudge_agent_avatar").toHaveCount(2);
});
