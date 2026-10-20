import { expect, test } from "@odoo/hoot";
import { click, press, queryOne } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";

import {
    defineModels,
    fields,
    getService,
    models,
    mountView,
    webModels,
} from "@web/../tests/web_test_helpers";

import { speechTag } from "@nudge/voice/languages";

import { FakeRecognition, setupVoice } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });
    email = fields.Char({ string: "Email" });

    _records = [{ id: 1, name: "Raghad", email: "" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const FORM = /* xml */ `<form><sheet><group><field name="name"/><field name="email"/></group></sheet></form>`;

const PRIVATE_BROWSER = {
    voice_private: true,
    voice_engine: "browser",
    can_transcribe: false,
    browser_speech_ok: true,
};
const PRIVATE_BLOCKED = {
    voice_private: true,
    voice_engine: "provider",
    can_transcribe: false,
    browser_speech_ok: false,
    voice_blocked_reason: "Private mode is on and no AI provider on your own server listens.",
};

async function openForm(options) {
    const asked = setupVoice(options);
    await mountView({ resModel: "partner", type: "form", arch: FORM, resId: 1 });
    return { asked, nudge: getService("nudge"), voice: getService("nudge_voice") };
}

test("private mode with the switch on: the browser is the engine and the text reaches the field", async () => {
    const { voice } = await openForm({ config: PRIVATE_BROWSER });
    expect(voice.state.engine).toBe("browser");
    expect(voice.state.isPrivate).toBe(true);

    const input = queryOne(".o_field_widget[name=email] input");
    await click(input);
    await press(["alt", "control", "d"]);
    await animationFrame();
    expect(voice.state.mode).toBe("dictation");
    expect(".o_nudge_dictation").toHaveCount(1);
    FakeRecognition.last.hear("raghad@example.com");
    await animationFrame();
    expect(input).toHaveValue("raghad@example.com");
    await press(["alt", "control", "d"]);
    await animationFrame();
    expect(".o_nudge_dictation").toHaveCount(0);
});

test("private mode with the switch off and no provider of ours: the microphone says why", async () => {
    const { asked, nudge, voice } = await openForm({ config: PRIVATE_BLOCKED });
    expect(voice.state.engine).toBe(null);
    expect(voice.state.blockedReason).toInclude("Private mode is on");
    expect(voice.listen({ mode: "question", onFinal() {} })).toBe(false);
    expect(FakeRecognition.made).toHaveLength(0, { message: "The browser is never asked to listen" });

    nudge.open();
    await animationFrame();
    expect(".o_nudge_mic.o_nudge_mic_off").toHaveCount(1);
    expect(".o_nudge_mic.o_nudge_mic_off").toHaveAttribute("title", voice.state.blockedReason);
    await click(".o_nudge_mic_off");
    await animationFrame();
    expect(".o_notification").toHaveCount(1);
    expect(".o_notification").toHaveText(voice.state.blockedReason);
    expect(asked.recordings).toHaveLength(0);
});

test("private mode: the voices of the browser read nothing unless the switch is on", async () => {
    const off = await openForm({
        config: { ...PRIVATE_BLOCKED, can_transcribe: true },
        settings: { nudge_explain: "text", nudge_speak_answers: true },
        voices: ["en-US"],
    });
    expect(off.voice.state.canUseBrowserSpeech).toBe(false);
    expect(off.voice.speak("Welcome Raghad")).toBe(false);
    expect(off.asked.synthesis.said).toHaveLength(0);
});

test("private mode: the voices of the browser read when the switch is on", async () => {
    const { asked, voice } = await openForm({
        config: PRIVATE_BROWSER,
        settings: { nudge_explain: "text", nudge_speak_answers: true },
        voices: ["en-US"],
    });
    expect(voice.state.canUseBrowserSpeech).toBe(true);
    expect(voice.speak("Welcome Raghad")).toBe(true);
    expect(asked.synthesis.said.map((utterance) => utterance.text)).toEqual(["Welcome Raghad"]);
});

test("a browser without speech recognition: the button is hidden and says why", async () => {
    const { nudge, voice } = await openForm({ recognition: false, config: PRIVATE_BROWSER });
    expect(voice.state.engine).toBe(null);
    expect(voice.state.isBlockedHidden).toBe(true);
    nudge.open();
    await animationFrame();
    expect("button.o_nudge_mic").toHaveCount(0);
    expect(".o_nudge_mic_missing").toHaveCount(1);
    expect(".o_nudge_mic_missing").toHaveAttribute("title", /Firefox/);
});

test("outside private mode nothing changes: the browser listens and speaks", async () => {
    const { voice } = await openForm({});
    expect(voice.state.engine).toBe("browser");
    expect(voice.state.isPrivate).toBe(false);
    expect(voice.state.canUseBrowserSpeech).toBe(true);
    expect(voice.state.blockedReason).toBe("");
});

test("the dialect chosen is the one the browser listens in", async () => {
    expect(speechTag("ar", ["en-US"], "ar-EG")).toBe("ar-EG");
    expect(speechTag("ar", ["en-US"], "ar")).toBe("ar-SA");
    expect(speechTag("ar", ["ar-PS"], "auto")).toBe("ar-PS");
    expect(speechTag("en", [], "ar-EG")).toBe("en-US", { message: "A dialect of another language is ignored" });

    const { voice } = await openForm({ config: PRIVATE_BROWSER });
    await voice.setLanguage("ar-SY");
    expect(voice.state.language).toBe("ar");
    const input = queryOne(".o_field_widget[name=email] input");
    await click(input);
    voice.dictate(input);
    await animationFrame();
    expect(FakeRecognition.last.lang).toBe("ar-SY");
});
