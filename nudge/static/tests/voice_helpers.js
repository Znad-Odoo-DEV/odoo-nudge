import { browser } from "@web/core/browser/browser";
import { user } from "@web/core/user";
import { session } from "@web/session";
import { onRpc, patchWithCleanup } from "@web/../tests/web_test_helpers";

import { speechApi } from "@nudge/voice/speech_api";

/**
 * Stands for the recognition of the browser: says what the test makes it say.
 */
export class FakeRecognition {
    static made = [];

    constructor() {
        FakeRecognition.made.push(this);
        this.calls = [];
    }

    static get last() {
        return FakeRecognition.made.at(-1);
    }

    start() {
        this.calls.push("start");
    }

    stop() {
        this.calls.push("stop");
        this.onend?.();
    }

    abort() {
        this.calls.push("abort");
        this.onerror?.({ error: "aborted" });
        this.onend?.();
    }

    hear(text, isFinal = true) {
        const result = Object.assign([{ transcript: text }], { isFinal });
        this.onresult?.({ resultIndex: 0, results: [result] });
    }

    fail(error) {
        this.onerror?.({ error });
        this.onend?.();
    }

    end() {
        this.onend?.();
    }
}

export class FakeRecorder {
    static made = [];

    constructor(stream) {
        FakeRecorder.made.push(this);
        this.stream = stream;
        this.state = "inactive";
        this.mimeType = "audio/webm";
    }

    start() {
        this.state = "recording";
    }

    stop() {
        this.state = "inactive";
        this.ondataavailable?.({ data: new Blob(["recorded"], { type: this.mimeType }) });
        this.onstop?.();
    }
}

export class FakeSynthesis {
    constructor(languages) {
        this.voices = languages.map((lang) => ({ lang, name: `Voice ${lang}` }));
        this.said = [];
        this.cancelled = 0;
        this.speaking = false;
        this.pending = false;
    }

    getVoices() {
        return this.voices;
    }

    speak(utterance) {
        this.said.push(utterance);
        this.speaking = true;
    }

    cancel() {
        this.cancelled++;
        this.speaking = false;
    }

    /** The voice is done with what it was saying */
    finish() {
        this.speaking = false;
        this.said.at(-1)?.onend?.();
    }
}

export function lines(...events) {
    return events.map((event) => JSON.stringify(event) + "\n").join("");
}

let answers = 1000;

/** An answer that ends well. Each one is a message of its own. */
export function answered(...events) {
    return () =>
        new Response(
            lines(...events, {
                type: "done",
                message_id: ++answers,
                state: "complete",
                message: "",
            }),
            { status: 200 }
        );
}

/** An answer that ends the way its events say */
export function streamed(...events) {
    return () => new Response(lines(...events), { status: 200 });
}

// Used by the tests of everything Nudge does, voice or not
export const setupNudge = (params) => setupVoice(params);

/**
 * @param {Object} [params]
 * @param {Object} [params.config] what the server tells about Nudge
 * @param {boolean} [params.recognition] whether the browser can listen by itself
 * @param {boolean} [params.microphone] whether the browser can record
 * @param {string[]} [params.voices] languages the browser can speak
 * @param {Function} [params.answer] what the server answers to a message
 * @param {Function} [params.transcribe] what the server answers to a recording
 * @param {Function} [params.decide] what the server answers to a yes or a no
 * @returns {Object} what was asked of the browser and of the server
 */
export function setupVoice({
    config = {},
    // The tests send their questions straight: the person is not asked how
    settings = { nudge_explain: "text" },
    recognition = true,
    microphone = true,
    voices = ["en-US"],
    answer = answered({ type: "delta", text: "Here is how." }),
    transcribe = () => new Response(JSON.stringify({ text: "What the provider heard" })),
    decide = answered({ type: "delta", text: "It is done." }),
} = {}) {
    FakeRecognition.made = [];
    FakeRecorder.made = [];
    const synthesis = new FakeSynthesis(voices);
    const asked = {
        messages: [],
        recordings: [],
        decisions: [],
        synthesis,
        stopped: 0,
        saved: [],
        // How loud the microphone is, as the test says it
        hearLevel: null,
        unwatched: 0,
    };
    patchWithCleanup(session, {
        nudge: {
            is_configured: true,
            can_share_screen: true,
            can_use_voice: true,
            voice_engine: "auto",
            can_transcribe: false,
            ...config,
        },
    });
    patchWithCleanup(user, { settings: { ...user.settings, ...settings } });
    patchWithCleanup(speechApi, {
        getRecognition: () => (recognition ? FakeRecognition : null),
        getSynthesis: () => synthesis,
        createUtterance: (text) => ({ text }),
        canRecord: () => microphone,
        openMicrophone: async () => {
            if (microphone === "blocked") {
                throw Object.assign(new Error("denied"), { name: "NotAllowedError" });
            }
            return { getTracks: () => [{ stop: () => asked.stopped++ }] };
        },
        createRecorder: (stream) => new FakeRecorder(stream),
        watchLevel: (stream, onLevel) => {
            asked.hearLevel = onLevel;
            return () => asked.unwatched++;
        },
        toWav: async (recording) => new Blob([`wav of ${recording.size}`], { type: "audio/wav" }),
        getLanguages: () => ["en-US", "fr-FR", "ar-PS"],
    });
    patchWithCleanup(browser, {
        fetch(url, options) {
            if (url === "/nudge/chat/stream") {
                asked.messages.push(Object.fromEntries(options.body.entries()));
                return answer(url, options);
            }
            if (url === "/nudge/voice/transcribe") {
                asked.recordings.push(Object.fromEntries(options.body.entries()));
                return transcribe(url, options);
            }
            if (url === "/nudge/agent/decide") {
                asked.decisions.push(Object.fromEntries(options.body.entries()));
                return decide(url, options);
            }
            // The web client fetches things of its own, translations for instance
            return super.fetch(...arguments);
        },
    });
    onRpc("res.users.settings", "set_res_users_settings", ({ kwargs }) => {
        asked.saved.push(kwargs.new_settings);
        return {};
    });
    return asked;
}
