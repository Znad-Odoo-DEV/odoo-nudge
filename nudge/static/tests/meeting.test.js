import { expect, test } from "@odoo/hoot";
import { click } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

import {
    defineModels,
    fields,
    getService,
    models,
    mountView,
    webModels,
} from "@web/../tests/web_test_helpers";

import { FakeRecognition, setupVoice } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });

    _records = [{ id: 1, name: "Raghad" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const FORM = /* xml */ `<form><sheet><group><field name="name"/></group></sheet></form>`;

async function openPanel(options) {
    const asked = setupVoice(options);
    await mountView({ resModel: "partner", type: "form", arch: FORM, resId: 1 });
    const nudge = getService("nudge");
    nudge.open();
    await animationFrame();
    return { asked, nudge };
}

test("a provider that listens: the recording is attached as sound, ready to be summarised", async () => {
    const { nudge } = await openPanel({ config: { can_transcribe: true, voice_engine: "provider" } });
    expect(".o_nudge_meeting").toHaveCount(1);
    await click(".o_nudge_meeting");
    await animationFrame();
    expect(".o_nudge_meeting_on").toHaveCount(1);
    await click(".o_nudge_meeting");
    await animationFrame();
    expect(".o_nudge_meeting_on").toHaveCount(0);
    expect(nudge.state.attachment.name).toMatch(/^meeting-\d+\.webm$/);
    expect(nudge.state.attachment.file.type).toBe("audio/webm");
    expect(nudge.state.draft).toBe("Summarise this meeting");
    expect(".o_nudge_attachment_name").toHaveText(nudge.state.attachment.name);
});

test("no provider listens: the words the browser heard are what is sent", async () => {
    const { nudge } = await openPanel({ config: { can_transcribe: false, voice_engine: "browser" } });
    await click(".o_nudge_meeting");
    await animationFrame();
    FakeRecognition.last.hear("Raghad said we launch on Sunday");
    FakeRecognition.last.hear("Ebrahim sends the offer");
    await click(".o_nudge_meeting");
    await animationFrame();
    const file = nudge.state.attachment.file;
    expect(nudge.state.attachment.name).toBe("meeting-transcript.txt");
    expect(await file.text()).toBe("Raghad said we launch on Sunday Ebrahim sends the offer");
});

test("private mode without a provider of ours that listens and without the switch: no way to record", async () => {
    await openPanel({
        config: {
            voice_private: true,
            voice_engine: "provider",
            can_transcribe: false,
            browser_speech_ok: false,
        },
    });
    expect(".o_nudge_meeting").toHaveCount(0);
});

test("a blocked microphone is said, nothing is recorded", async () => {
    const { nudge } = await openPanel({
        config: { can_transcribe: true, voice_engine: "provider" },
        microphone: "blocked",
    });
    await click(".o_nudge_meeting");
    await animationFrame();
    expect(".o_notification").toHaveCount(1);
    expect(".o_notification").toHaveText(/microphone is blocked/);
    expect(".o_nudge_meeting_on").toHaveCount(0);
    expect(nudge.state.attachment).toBe(null);
});

test("a recording file is accepted, over 10 MB it is told to be split", async () => {
    const { nudge } = await openPanel({ config: { can_transcribe: true } });
    expect(nudge.setAttachment(new File(["x"], "talk.m4a", { type: "audio/mp4" }))).toBe("");
    expect(nudge.state.attachment.name).toBe("talk.m4a");
    const big = new File([new Uint8Array(11 * 1048576)], "long.mp3", { type: "audio/mpeg" });
    expect(nudge.setAttachment(big)).toMatch(/split the file/);
});
