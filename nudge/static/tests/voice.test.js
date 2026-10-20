import { expect, test } from "@odoo/hoot";
import { click, keyDown, keyUp, press } from "@odoo/hoot-dom";
import { advanceTime, animationFrame, tick } from "@odoo/hoot-mock";

import {
    defineModels,
    fields,
    getService,
    makeMockEnv,
    models,
    mountView,
    webModels,
} from "@web/../tests/web_test_helpers";

import {
    detectLanguage,
    languageOf,
    languagesFor,
    nextLanguage,
    resolveLanguage,
    shortName,
    speechTag,
} from "@nudge/voice/languages";
import { chooseEngine, toSpokenText } from "@nudge/voice/voice_service";
import { encodeWav } from "@nudge/voice/wav";
import { endOfSpeech } from "@nudge/voice/engines";

import { answered, FakeRecognition, FakeRecorder, setupVoice } from "./voice_helpers";

class Partner extends models.Model {
    _name = "partner";

    name = fields.Char({ string: "Name" });
    email = fields.Char({ string: "Email Address" });

    _records = [{ id: 1, name: "Azure Interior", email: "azure@example.com" }];
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

const FORM = /* xml */ `
    <form>
        <header>
            <button name="action_confirm" type="object" string="Confirm"/>
        </header>
        <sheet>
            <group>
                <field name="name"/>
                <field name="email"/>
            </group>
        </sheet>
    </form>`;

async function openForm() {
    await mountView({ resModel: "partner", type: "form", arch: FORM, resId: 1 });
    return { nudge: getService("nudge"), voice: getService("nudge_voice") };
}

test("languages are read from what people and browsers say", () => {
    expect(languageOf("ar-001")).toBe("ar");
    expect(languageOf("en_US")).toBe("en");
    expect(languageOf("FR")).toBe("fr");
    for (const value of ["", null, undefined, "1", "../x"]) {
        expect(languageOf(value)).toBe("");
    }

    expect(resolveLanguage("auto", "ar-001")).toBe("ar");
    expect(resolveLanguage("auto", "fr-BE")).toBe("fr");
    expect(resolveLanguage("en", "ar-001")).toBe("en");
    expect(resolveLanguage("", "")).toBe("en");

    // The dialect is the one of the person, when their browser tells it
    expect(speechTag("ar", ["en-US", "ar-PS", "ar-EG"])).toBe("ar-PS");
    expect(speechTag("ar", ["en-US"])).toBe("ar-SA");
    expect(speechTag("en", [])).toBe("en-US");
    expect(speechTag("fr", ["fr-BE"])).toBe("fr-BE");
    expect(speechTag("tr", [])).toBe("tr");

    expect(languagesFor("ar-001")).toEqual(["ar", "en"]);
    expect(languagesFor("fr-FR")).toEqual(["fr", "ar", "en"]);
    expect(nextLanguage("ar", "ar-001")).toBe("en");
    expect(nextLanguage("en", "ar-001")).toBe("ar");
    expect(nextLanguage("en", "fr-FR")).toBe("fr");

    expect(shortName("ar")).toBe("ع");
    expect(shortName("en")).toBe("EN");
    expect(shortName("fr")).toBe("FR");

    expect(detectLanguage("اضغط على زر Confirm ثم احفظ")).toBe("ar");
    expect(detectLanguage("Press Confirm, then save.")).toBe("en");
    expect(detectLanguage("Press تأكيد")).toBe("ar");
    expect(detectLanguage("123 ...")).toBe("en");
});

test("what is read aloud has no sign of formatting", () => {
    expect(toSpokenText("Open **Sales**, then press `New`.")).toBe("Open Sales, then press New.");
    expect(toSpokenText("## Steps\n\n1. Open the *order*\n2. Press Confirm\n- done")).toBe(
        "Steps Open the order Press Confirm done"
    );
    expect(toSpokenText("See [the guide](https://example.com/a?b=1) or https://example.com")).toBe(
        "See the guide or"
    );
    expect(toSpokenText("Before\n```python\nprint(1)\n```\nafter")).toBe("Before after");
    expect(toSpokenText("> quoted | cell")).toBe("quoted cell");
    expect(toSpokenText(null)).toBe("");
    expect(toSpokenText("x".repeat(5000))).toHaveLength(1200);
});

test("a recording is written as a WAV file", () => {
    const buffer = encodeWav(new Float32Array([0, 1, -1, 0.5, 2, -2]), 16000);
    const view = new DataView(buffer);
    const text = (offset) =>
        String.fromCharCode(...new Uint8Array(buffer.slice(offset, offset + 4)));

    expect(buffer.byteLength).toBe(44 + 12);
    expect(text(0)).toBe("RIFF");
    expect(view.getUint32(4, true)).toBe(36 + 12);
    expect(text(8)).toBe("WAVE");
    expect(text(12)).toBe("fmt ");
    // Plain samples, one channel, 16 bits
    expect(view.getUint16(20, true)).toBe(1);
    expect(view.getUint16(22, true)).toBe(1);
    expect(view.getUint32(24, true)).toBe(16000);
    expect(view.getUint32(28, true)).toBe(32000);
    expect(view.getUint16(34, true)).toBe(16);
    expect(text(36)).toBe("data");
    expect(view.getUint32(40, true)).toBe(12);
    const samples = [0, 1, 2, 3, 4, 5].map((index) => view.getInt16(44 + 2 * index, true));
    // What is louder than the loudest is the loudest
    expect(samples).toEqual([0, 32767, -32768, 16383, 32767, -32768]);
});

test("who listens depends on the administrator and on the browser", async () => {
    const cases = [
        // allowed, wanted, the browser can, the provider can
        [true, "auto", true, true, "browser"],
        [true, "auto", false, true, "provider"],
        [true, "auto", false, false, null],
        [true, "browser", true, true, "browser"],
        [true, "browser", false, true, null],
        [true, "provider", true, true, "provider"],
        [true, "provider", true, false, null],
        [true, "something else", true, true, "browser"],
        [false, "auto", true, true, null],
    ];
    for (const [isAllowed, wanted, byBrowser, byProvider, engine] of cases) {
        expect(chooseEngine({ isAllowed, wanted, byBrowser, byProvider })).toBe(engine);
    }

    // The provider can listen, the browser cannot record for it
    setupVoice({ recognition: false, microphone: false, config: { can_transcribe: true } });
    await makeMockEnv();
    const voice = getService("nudge_voice");
    expect(voice.state.engine).toBe(null);
    expect(voice.listen({ mode: "question", onFinal() {} })).toBe(false);
    expect(voice.state.mode).toBe(null);
});

test("a question is asked by voice", async () => {
    const asked = setupVoice();
    const { nudge, voice } = await openForm();

    nudge.open();
    await animationFrame();
    expect(".o_nudge_mic").toHaveCount(1);
    expect(".o_nudge_language").toHaveText("EN");
    expect(".o_nudge_voice").toHaveCount(0);

    await click(".o_nudge_mic");
    await animationFrame();
    const recognition = FakeRecognition.last;
    expect(recognition.calls).toEqual(["start"]);
    expect(recognition.lang).toBe("en-US");
    expect(recognition.continuous).toBe(false);
    expect(recognition.interimResults).toBe(true);
    expect(voice.state.mode).toBe("question");
    expect(".o_nudge_mic").toHaveClass("o_nudge_mic_on");
    expect(".o_nudge_voice_text").toHaveText("I'm listening…");
    expect(".o_nudge_avatar").toHaveClass("o_nudge_avatar_listening");

    // Words are shown as they are heard
    recognition.hear("how do I", false);
    await animationFrame();
    expect(".o_nudge_input").toHaveValue("how do I");
    expect(".o_nudge_send").not.toBeEnabled();
    recognition.hear("How do I confirm this order?");
    await animationFrame();
    expect(".o_nudge_input").toHaveValue("How do I confirm this order?");
    expect(asked.messages).toHaveLength(0);

    recognition.end();
    await animationFrame();
    await tick();
    expect(asked.messages).toHaveLength(1);
    expect(asked.messages[0].message).toBe("How do I confirm this order?");
    expect(asked.messages[0].spoken).toBe("1");
    expect("read_aloud" in asked.messages[0]).toBe(false);
    expect(JSON.parse(asked.messages[0].snapshot).model).toBe("partner");
    expect(voice.state.mode).toBe(null);
    expect(nudge.state.draft).toBe("");
    expect(nudge.state.messages[0].spoken).toBe(true);
    await animationFrame();
    expect(".o_nudge_message_user .o_nudge_said").toHaveCount(1);
    expect(".o_nudge_mic").not.toHaveClass("o_nudge_mic_on");
});

test("what was typed is not sent by the voice", async () => {
    const asked = setupVoice();
    const { nudge } = await openForm();
    nudge.open();
    nudge.state.draft = "About this customer:";
    await animationFrame();

    await click(".o_nudge_mic");
    FakeRecognition.last.hear("do they pay on time");
    FakeRecognition.last.end();
    await animationFrame();

    expect(asked.messages).toHaveLength(0);
    expect(".o_nudge_input").toHaveValue("About this customer: do they pay on time");
    expect(".o_nudge_send").toBeEnabled();
});

test("a typed message is not told as spoken", async () => {
    const asked = setupVoice();
    const { nudge } = await openForm();
    await nudge.send("Typed");
    expect("spoken" in asked.messages[0]).toBe(false);
    expect(nudge.state.messages[0].spoken).toBe(false);
});

test("what goes wrong is told in plain words", async () => {
    const asked = setupVoice();
    const { nudge, voice } = await openForm();
    nudge.open();
    await animationFrame();
    const cases = {
        "no-speech": "I did not hear anything.",
        "not-allowed": "The microphone is blocked. Allow it in the address bar of your browser.",
        "service-not-allowed": "The microphone is blocked. Allow it in the address bar of your browser.",
        "audio-capture": "No microphone was found.",
        network: "Your browser could not reach its speech service.",
        "language-not-supported": "Your browser cannot listen to this language.",
        "something-new": "What you said could not be turned into text.",
    };
    for (const [error, message] of Object.entries(cases)) {
        await click(".o_nudge_mic");
        FakeRecognition.last.fail(error);
        await animationFrame();
        expect(voice.state.mode).toBe(null);
        expect(".o_nudge_voice_error .o_nudge_voice_text").toHaveText(message);
    }
    expect(asked.messages).toHaveLength(0);

    // The next try starts clean
    await click(".o_nudge_mic");
    await animationFrame();
    expect(".o_nudge_voice_error").toHaveCount(0);
});

test("the shortcut is held to talk, and tapped to call", async () => {
    const asked = setupVoice();
    const { nudge, voice } = await openForm();

    await click(".o_field_widget[name=name] input");
    await press(["control", "shift", "space"]);
    await animationFrame();
    await advanceTime(1000);
    expect(nudge.state.isOpen).toBe(true);
    expect(FakeRecognition.made).toHaveLength(0, { message: "A tap opens the microphone for nobody" });
    nudge.close();

    await keyDown(["control", "shift", "space"]);
    await animationFrame();
    expect(nudge.state.isOpen).toBe(true);
    expect(voice.state.mode).toBe(null);
    await advanceTime(400);
    expect(voice.state.mode).toBe("question");
    const recognition = FakeRecognition.last;
    expect(recognition.continuous).toBe(true, { message: "A pause does not end what is held" });

    recognition.hear("where is");
    recognition.hear("the salesperson");
    await animationFrame();
    expect(".o_nudge_input").toHaveValue("where is the salesperson");
    expect(asked.messages).toHaveLength(0);

    await keyUp(["control", "shift", "space"]);
    await animationFrame();
    await tick();
    expect(recognition.calls).toEqual(["start", "stop"]);
    expect(asked.messages.map((message) => message.message)).toEqual(["where is the salesperson"]);
    expect(asked.messages[0].spoken).toBe("1");
    expect(voice.state.mode).toBe(null);
});

test("a dictation goes on through silences", async () => {
    setupVoice();
    const { voice } = await openForm();
    const heard = [];
    const ended = [];
    voice.listen({
        mode: "question",
        continuous: true,
        onFinal: (text) => heard.push(text),
        onEnd: (error) => ended.push(error),
    });
    const first = FakeRecognition.last;
    first.hear("one");
    // The browser gives up on a silence
    first.fail("no-speech");
    expect(FakeRecognition.made).toHaveLength(2);
    expect(voice.state.mode).toBe("question");
    FakeRecognition.last.hear("two");
    FakeRecognition.last.end();
    expect(FakeRecognition.made).toHaveLength(3);

    // Something else than a silence ends it
    FakeRecognition.last.fail("network");
    expect(FakeRecognition.made).toHaveLength(3);
    expect(heard).toEqual(["one", "two"]);
    expect(ended).toEqual(["network"]);
    expect(voice.state.mode).toBe(null);
});

test("the one who speaks two languages switches between them", async () => {
    const asked = setupVoice();
    const { nudge, voice } = await openForm();
    nudge.open();
    await animationFrame();
    expect(voice.state.language).toBe("en");

    await click(".o_nudge_language");
    await animationFrame();
    expect(voice.state.language).toBe("ar");
    expect(".o_nudge_language").toHaveText("ع");
    expect(asked.saved).toEqual([{ nudge_voice_language: "ar" }]);

    await click(".o_nudge_mic");
    expect(FakeRecognition.last.lang).toBe("ar-PS");

    // While listening: what is being said starts again, in the other language
    const first = FakeRecognition.last;
    await click(".o_nudge_language");
    await animationFrame();
    expect(first.calls).toEqual(["start", "abort"]);
    expect(FakeRecognition.made).toHaveLength(2);
    expect(FakeRecognition.last.lang).toBe("en-US");
    expect(voice.state.mode).toBe("question");
    expect(asked.saved.at(-1)).toEqual({ nudge_voice_language: "auto" });

    FakeRecognition.last.hear("Hello");
    FakeRecognition.last.end();
    await animationFrame();
    await tick();
    expect(asked.messages.map((message) => message.message)).toEqual(["Hello"]);
});

test("escape drops what was being said", async () => {
    const asked = setupVoice();
    const { nudge, voice } = await openForm();
    nudge.open();
    await animationFrame();

    await click(".o_nudge_mic");
    FakeRecognition.last.hear("never mind");
    await animationFrame();
    await press("escape");
    await animationFrame();
    expect(voice.state.mode).toBe(null);
    expect(nudge.state.isOpen).toBe(true, { message: "The first press ends the listening" });
    expect(asked.messages).toHaveLength(0);
    expect(FakeRecognition.last.calls).toEqual(["start", "abort"]);

    // Closing the panel closes the microphone
    await click(".o_nudge_mic");
    expect(voice.state.mode).toBe("question");
    nudge.close();
    expect(voice.state.mode).toBe(null);
    expect(asked.messages).toHaveLength(0);
});

test("the provider listens where the browser cannot", async () => {
    const asked = setupVoice({ recognition: false, config: { can_transcribe: true } });
    const { nudge, voice } = await openForm();
    nudge.open();
    await animationFrame();
    expect(voice.state.engine).toBe("provider");

    await click(".o_nudge_mic");
    await animationFrame();
    expect(FakeRecorder.made).toHaveLength(1);
    expect(FakeRecorder.made[0].state).toBe("recording");
    expect(voice.state.phase).toBe("listening");

    await click(".o_nudge_mic");
    await animationFrame();
    await tick();
    await animationFrame();
    expect(asked.stopped).toBe(1, { message: "The microphone is given back" });
    expect(asked.recordings).toHaveLength(1);
    const sent = asked.recordings[0];
    expect(sent.language).toBe("en");
    expect(sent.audio.type).toBe("audio/wav");
    expect(sent.audio.name).toBe("speech.wav");
    // What is written on the screen, never what the record holds
    expect(JSON.parse(sent.hints)).toEqual(["Confirm", "Name", "Email Address"]);
    expect(sent.hints).not.toInclude("Azure");

    await tick();
    expect(asked.messages.map((message) => message.message)).toEqual(["What the provider heard"]);
    expect(asked.messages[0].spoken).toBe("1");
    expect(voice.state.mode).toBe(null);
});

test("the provider says why it did not listen", async () => {
    const responses = [
        () => new Response(JSON.stringify({ error: "This recording is too long." })),
        () => new Response(JSON.stringify({ text: "" })),
        () => new Response("Forbidden", { status: 403 }),
    ];
    const asked = setupVoice({
        recognition: false,
        config: { can_transcribe: true },
        transcribe: () => responses.shift()(),
    });
    const { nudge, voice } = await openForm();
    nudge.open();
    await animationFrame();
    const messages = [
        "This recording is too long.",
        "I did not hear anything.",
        "What you said could not be turned into text.",
    ];
    for (const message of messages) {
        await click(".o_nudge_mic");
        await animationFrame();
        await click(".o_nudge_mic");
        await animationFrame();
        await tick();
        await animationFrame();
        expect(voice.state.error).toBe(message);
        expect(voice.state.mode).toBe(null);
    }
    expect(asked.messages).toHaveLength(0);
});

test("a microphone that is refused is told", async () => {
    setupVoice({ recognition: false, microphone: "blocked", config: { can_transcribe: true } });
    const { nudge, voice } = await openForm();
    nudge.open();
    await animationFrame();
    await click(".o_nudge_mic");
    await animationFrame();
    expect(voice.state.mode).toBe(null);
    expect(voice.state.error).toBe(
        "The microphone is blocked. Allow it in the address bar of your browser."
    );
    expect(FakeRecorder.made).toHaveLength(0);
});

test("answers are read to those who ask for it", async () => {
    const english = answered({ type: "delta", text: "Open **Sales**, then press `New`." });
    const responses = [
        english,
        english,
        answered({ type: "delta", text: "اضغط على زر Confirm." }),
        english,
    ];
    const asked = setupVoice({ voices: ["en-GB"], answer: () => responses.shift()() });
    const { nudge, voice } = await openForm();
    const { synthesis } = asked;

    await nudge.send("How?");
    expect(synthesis.said).toHaveLength(0, { message: "Nudge speaks to those who ask for it" });

    await voice.setSpeakAnswers(true);
    expect(asked.saved.at(-1)).toEqual({ nudge_speak_answers: true });
    await nudge.send("How?");
    expect(asked.messages[1].read_aloud).toBe("1");
    expect(synthesis.said).toHaveLength(1);
    expect(synthesis.said[0].text).toBe("Open Sales, then press New.");
    expect(synthesis.said[0].lang).toBe("en-GB");
    expect(voice.state.isSpeaking).toBe(true);
    synthesis.finish();
    expect(voice.state.isSpeaking).toBe(false);

    // No voice on this device reads Arabic: read by another, it is not understood
    expect(voice.canSpeak("ar")).toBe(false);
    await nudge.send("بالعربي؟");
    expect(synthesis.said).toHaveLength(1);

    await voice.setSpeakAnswers(false);
    await nudge.send("And now?");
    expect("read_aloud" in asked.messages[3]).toBe(false);
    expect(synthesis.said).toHaveLength(1);
});

test("Nudge is quiet while it listens", async () => {
    const asked = setupVoice({ answer: answered({ type: "delta", text: "A long answer." }) });
    const { nudge, voice } = await openForm();
    await voice.setSpeakAnswers(true);
    await nudge.send("Tell me");
    const { synthesis } = asked;
    expect(synthesis.said).toHaveLength(1);
    const cancelled = synthesis.cancelled;

    nudge.open();
    await animationFrame();
    expect(".o_nudge_quiet").toHaveCount(1);
    await click(".o_nudge_mic");
    expect(synthesis.cancelled).toBeGreaterThan(cancelled);
    expect(voice.state.isSpeaking).toBe(false);
    expect(voice.speak("Not now")).toBe(false);
    voice.cancel();
});

test("preferences of the voice", async () => {
    const asked = setupVoice();
    const { nudge, voice } = await openForm();
    nudge.open();
    nudge.showSettings();
    await animationFrame();

    expect(".o_nudge_voice_language option").toHaveCount(22);
    expect(".o_nudge_dictation_hotkey option").toHaveCount(3);
    expect(".o_nudge_voice_note").toHaveText(/Your browser turns what you say into text/);
    expect(".o_nudge_speak").not.toBeChecked();
    expect(".o_nudge_silent").toHaveCount(0);

    await click(".o_nudge_speak");
    await animationFrame();
    expect(voice.state.speakAnswers).toBe(true);
    expect(".o_nudge_silent").toHaveText(/Arabic/);
    expect(".o_nudge_silent").not.toHaveText(/English/);

    await nudge.setDictationHotkey("control+shift+d");
    expect(asked.saved.at(-1)).toEqual({ nudge_dictation_hotkey: "control+shift+d" });
    await voice.setLanguage("ar");
    expect(voice.state.language).toBe("ar");
});

test("without voice, nothing of it is shown", async () => {
    setupVoice({ config: { can_use_voice: false } });
    const { nudge, voice } = await openForm();
    nudge.open();
    await animationFrame();
    expect(".o_nudge_mic").toHaveCount(0);
    expect(".o_nudge_language").toHaveCount(0);
    expect(nudge.talk()).toBe(false);
    expect(voice.speak("Hello")).toBe(false);

    await keyDown(["control", "shift", "space"]);
    await advanceTime(1000);
    await keyUp(["control", "shift", "space"]);
    expect(FakeRecognition.made).toHaveLength(0);

    nudge.showSettings();
    await animationFrame();
    expect(".o_nudge_voice_note").toHaveText("Your administrator turned voice off.");
    expect(".o_nudge_speak").toHaveCount(0);
    expect(".o_nudge_dictation_hotkey").toHaveCount(0);
});

test("a question is over once the person went quiet", () => {
    const decide = endOfSpeech();
    expect(decide(0.004, 0)).toBe("");
    expect(decide(0.2, 1000)).toBe("");
    expect(decide(0.15, 1500)).toBe("");
    // Quiet, but not for long yet
    expect(decide(0.006, 2000)).toBe("");
    expect(decide(0.006, 2999)).toBe("");
    expect(decide(0.006, 3000)).toBe("done");

    const nobody = endOfSpeech();
    expect(nobody(0.01, 0)).toBe("");
    expect(nobody(0.012, 7999)).toBe("");
    expect(nobody(0.011, 8000)).toBe("nobody");

    // The hum of a noisy room is not speech
    const noisy = endOfSpeech();
    expect(noisy(0.05, 0)).toBe("");
    expect(noisy(0.06, 4000)).toBe("");
    expect(noisy(0.06, 8000)).toBe("nobody");
});

test("the provider's listening ends by itself when the person goes quiet", async () => {
    const asked = setupVoice({ recognition: false, config: { can_transcribe: true } });
    const { nudge, voice } = await openForm();
    nudge.open();
    await animationFrame();

    await click(".o_nudge_mic");
    await animationFrame();
    const recorder = FakeRecorder.made[0];
    expect(recorder.state).toBe("recording");
    asked.hearLevel(0.004);
    asked.hearLevel(0.2);
    await advanceTime(1000);
    asked.hearLevel(0.004);
    expect(recorder.state).toBe("recording", { message: "A pause is not the end" });
    await advanceTime(600);
    asked.hearLevel(0.004);
    await animationFrame();
    await tick();
    await animationFrame();
    expect(recorder.state).toBe("inactive");
    expect(asked.unwatched).toBe(1);
    expect(asked.recordings).toHaveLength(1);
    await tick();
    expect(asked.messages.map((message) => message.message)).toEqual(["What the provider heard"]);
    expect(voice.state.mode).toBe(null);
});

test("nothing is sent to the provider when nobody spoke", async () => {
    const asked = setupVoice({ recognition: false, config: { can_transcribe: true } });
    const { nudge, voice } = await openForm();
    nudge.open();
    await animationFrame();

    await click(".o_nudge_mic");
    await animationFrame();
    asked.hearLevel(0.004);
    await advanceTime(8000);
    asked.hearLevel(0.005);
    await animationFrame();
    await tick();
    await animationFrame();
    expect(FakeRecorder.made[0].state).toBe("inactive");
    expect(asked.recordings).toHaveLength(0);
    expect(voice.state.error).toBe("I did not hear anything.");
    expect(voice.state.mode).toBe(null);
});

test("a dictation is not ended by a silence", async () => {
    const asked = setupVoice({ recognition: false, config: { can_transcribe: true } });
    const { voice } = await openForm();
    voice.listen({ mode: "dictation", continuous: true, onFinal: () => {} });
    await animationFrame();
    expect(asked.hearLevel).toBe(null, { message: "Nobody watches how loud it is" });
    expect(FakeRecorder.made[0].state).toBe("recording");
    voice.cancel();
});

test("a browser that never answers is not waited for", async () => {
    const asked = setupVoice();
    const { nudge, voice } = await openForm();
    nudge.open();
    await animationFrame();

    // Words that come keep it listening
    await click(".o_nudge_mic");
    await animationFrame();
    FakeRecognition.last.hear("how do");
    await advanceTime(10000);
    expect(FakeRecognition.last.calls).toEqual(["start"]);
    await click(".o_nudge_mic");
    await animationFrame();
    await tick();
    await animationFrame();

    // This one hears nothing, and does not even end when told to stop
    await click(".o_nudge_mic");
    await animationFrame();
    const recognition = FakeRecognition.last;
    recognition.stop = function () {
        this.calls.push("stop");
    };
    await advanceTime(10000);
    expect(recognition.calls).toEqual(["start", "stop"]);
    expect(voice.state.mode).toBe("question");
    await advanceTime(2000);
    await animationFrame();
    expect(recognition.calls).toEqual(["start", "stop", "abort"]);
    expect(voice.state.mode).toBe(null);
    expect(voice.state.error).toBe("I did not hear anything.");
    expect(asked.messages.map((message) => message.message)).toEqual(["how do"]);
});

test("who writes Arabic is listened to in Arabic", async () => {
    setupVoice();
    const { nudge, voice } = await openForm();
    nudge.open();
    await animationFrame();
    expect(voice.state.language).toBe("en");

    nudge.state.draft = "كيف أؤكد هذا الطلب؟";
    await click(".o_nudge_mic");
    await animationFrame();
    expect(FakeRecognition.last.lang).toBe("ar-PS");
    expect(".o_nudge_language").toHaveText("ع");
    voice.cancel();

    voice.followText("And in English?");
    expect(voice.state.language).toBe("en");

    // A language chosen by hand stays
    voice.switchLanguage();
    const chosen = voice.state.language;
    voice.followText("كيف أؤكد هذا الطلب؟");
    expect(voice.state.language).toBe(chosen);
});

test("words shown while speaking are kept when the browser ends without settling them", async () => {
    const asked = setupVoice();
    const { nudge, voice } = await openForm();
    nudge.open();
    await animationFrame();

    await click(".o_nudge_mic");
    await animationFrame();
    FakeRecognition.last.hear("كيف أؤكد هذا الطلب", false);
    await animationFrame();
    expect(".o_nudge_input").toHaveValue("كيف أؤكد هذا الطلب");
    FakeRecognition.last.end();
    await animationFrame();
    await tick();
    expect(asked.messages.map((message) => message.message)).toEqual(["كيف أؤكد هذا الطلب"]);
    expect(voice.state.error).toBe("");
});

test("a question that ends with no word says so", async () => {
    const asked = setupVoice();
    const { nudge, voice } = await openForm();
    nudge.open();
    await animationFrame();

    await click(".o_nudge_mic");
    await animationFrame();
    // The browser ends with no word and no reason
    FakeRecognition.last.end();
    await animationFrame();
    expect(voice.state.error).toBe("I did not hear anything.");
    expect(".o_nudge_voice_text").toHaveText("I did not hear anything.");
    expect(asked.messages).toHaveLength(0);
});
