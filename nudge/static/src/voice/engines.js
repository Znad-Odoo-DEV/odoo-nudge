import { browser } from "@web/core/browser/browser";

import { speechApi } from "./speech_api";

/**
 * Two ways to turn speech into text, used the same way.
 *
 * `start` takes what to listen for and what to call:
 *  - onInterim(text): what is being heard, not settled yet
 *  - onFinal(text): a piece of speech, settled
 *  - onPhase(phase): "listening", then "transcribing" when the words come later
 *  - onEnd(error, message): it is over; `error` is empty when all went well
 * `stop` ends the listening and keeps what was heard; `abort` drops it.
 */

// Seconds after which a recording is sent even if the person goes on
export const MAX_SECONDS = 60;
// A dictation goes on through silences: the browser is asked again
const MAX_RESTARTS = 30;
// A question ends by itself this long after the person stopped speaking
export const END_OF_SPEECH_MS = 1500;
// and is dropped, nothing sent, when nobody spoke for this long
export const NO_SPEECH_MS = 8000;
// Some browsers open the microphone and never say a word back: they are told
// to stop when nothing came this long, and left when they do not stop either
export const NOTHING_HEARD_MS = 10000;
const STOP_GRACE_MS = 2000;
// Speech is this much louder than the room, and never quieter than this
const SPEECH_OVER_ROOM = 2.5;
const MIN_SPEECH_LEVEL = 0.015;

/**
 * Tells, from how loud the microphone is, when a question is over.
 *
 * @returns {(level: number, now: number) => ""|"done"|"nobody"} "done" once
 *   the person spoke and went quiet, "nobody" when no one spoke at all
 */
export function endOfSpeech() {
    let startedAt = null;
    let room = null;
    let lastSpokenAt = null;
    return (level, now) => {
        startedAt ??= now;
        // The room is the quietest it was, rising slowly with a noisier one
        room = room === null ? level : Math.min(level, room + 0.0005);
        if (level > Math.max(MIN_SPEECH_LEVEL, room * SPEECH_OVER_ROOM)) {
            lastSpokenAt = now;
        }
        if (lastSpokenAt !== null) {
            return now - lastSpokenAt >= END_OF_SPEECH_MS ? "done" : "";
        }
        return now - startedAt >= NO_SPEECH_MS ? "nobody" : "";
    };
}

const BROWSER_ERRORS = {
    "no-speech": "silence",
    "not-allowed": "blocked",
    "service-not-allowed": "blocked",
    "audio-capture": "no_microphone",
    network: "network",
    "language-not-supported": "language",
    aborted: "",
};

function microphoneError(error) {
    if (error?.name === "NotAllowedError" || error?.name === "SecurityError") {
        return "blocked";
    }
    if (error?.name === "NotFoundError" || error?.name === "OverconstrainedError") {
        return "no_microphone";
    }
    return "failed";
}

/**
 * The recognition of the browser: words come as they are said.
 */
export class BrowserEngine {
    constructor() {
        this.recognition = null;
        this.isOver = true;
    }

    start(options) {
        this.options = options;
        this.isOver = false;
        this.isStopping = false;
        this.restarts = 0;
        this.heard = false;
        options.onPhase("listening");
        this.listen();
    }

    listen() {
        const { language, continuous, onInterim, onFinal } = this.options;
        const Recognition = speechApi.getRecognition();
        const recognition = new Recognition();
        let error = "";
        // Words shown as heard, that the browser has not settled yet
        let pending = "";
        recognition.lang = language.tag;
        recognition.interimResults = true;
        recognition.continuous = Boolean(continuous);
        recognition.maxAlternatives = 1;
        browser.clearTimeout(this.watchdog);
        if (!continuous) {
            this.watchdog = browser.setTimeout(() => this.giveUp(recognition), NOTHING_HEARD_MS);
        }
        recognition.onresult = (event) => {
            browser.clearTimeout(this.watchdog);
            let interim = "";
            for (let index = event.resultIndex; index < event.results.length; index++) {
                const result = event.results[index];
                const text = (result[0]?.transcript || "").trim();
                if (!result.isFinal) {
                    interim = [interim, text].filter(Boolean).join(" ");
                } else if (text) {
                    this.heard = true;
                    onFinal(text);
                }
            }
            pending = interim;
            onInterim(interim);
        };
        recognition.onerror = (event) => {
            error = BROWSER_ERRORS[event.error] ?? "failed";
        };
        recognition.onend = () => {
            if (recognition !== this.recognition) {
                return;
            }
            browser.clearTimeout(this.watchdog);
            this.recognition = null;
            // Browsers sometimes end without settling the last words they
            // showed: what the person saw is what they said
            if (pending) {
                this.heard = true;
                onFinal(pending);
                onInterim("");
                pending = "";
            }
            // Silence ends what the browser listens to, not a dictation
            const goesOn =
                continuous &&
                !this.isStopping &&
                (!error || error === "silence") &&
                this.restarts < MAX_RESTARTS;
            if (goesOn) {
                this.restarts++;
                this.listen();
            } else if (continuous) {
                this.finish(this.heard && error === "silence" ? "" : error);
            } else {
                // A question that ends with no word is told, never dropped quietly
                this.finish(this.heard ? "" : error || "silence");
            }
        };
        this.recognition = recognition;
        try {
            recognition.start();
        } catch {
            this.recognition = null;
            this.finish("failed");
        }
    }

    /** Nothing came: the browser is told to stop, and left if it does not */
    giveUp(recognition) {
        if (recognition !== this.recognition) {
            return;
        }
        this.isStopping = true;
        recognition.stop();
        this.watchdog = browser.setTimeout(() => {
            if (recognition === this.recognition) {
                this.recognition = null;
                recognition.abort();
                this.finish(this.heard ? "" : "silence");
            }
        }, STOP_GRACE_MS);
    }

    finish(error) {
        browser.clearTimeout(this.watchdog);
        if (!this.isOver) {
            this.isOver = true;
            this.options.onEnd(error);
        }
    }

    stop() {
        this.isStopping = true;
        if (this.recognition) {
            this.recognition.stop();
        } else {
            this.finish("");
        }
    }

    abort() {
        this.isStopping = true;
        const recognition = this.recognition;
        this.recognition = null;
        recognition?.abort();
        this.finish("");
    }
}

/**
 * @param {Blob} recording WAV file
 * @param {string} language code of the language most likely spoken
 * @param {string[]} hints words written on the screen
 * @returns {Promise<string>} what was said
 */
export async function transcribe(recording, language, hints) {
    // The token of an "http" route is read from the form
    const body = new FormData();
    body.append("csrf_token", odoo.csrf_token);
    body.append("audio", recording, "speech.wav");
    body.append("language", language);
    if (hints?.length) {
        body.append("hints", JSON.stringify(hints));
    }
    const response = await browser.fetch("/nudge/voice/transcribe", {
        method: "POST",
        body,
        credentials: "same-origin",
    });
    if (!response.ok) {
        throw Object.assign(new Error(`HTTP ${response.status}`), { code: "failed" });
    }
    const result = await response.json();
    if (result.error) {
        throw Object.assign(new Error(result.error), { code: "refused" });
    }
    return String(result.text || "").trim();
}

/**
 * The microphone is recorded, and the AI provider turns the recording into
 * text: words come once the person is done.
 */
export class ServerEngine {
    constructor() {
        this.recorder = null;
        this.isOver = true;
    }

    async start(options) {
        this.options = options;
        this.isOver = false;
        this.isAborted = false;
        this.isStopping = false;
        this.isNobody = false;
        let stream;
        try {
            stream = await speechApi.openMicrophone();
        } catch (error) {
            this.finish(microphoneError(error));
            return;
        }
        const release = () => stream.getTracks().forEach((track) => track.stop());
        if (this.isStopping) {
            // Released before the microphone was there
            release();
            this.finish("");
            return;
        }
        const chunks = [];
        let recorder;
        try {
            recorder = speechApi.createRecorder(stream);
        } catch {
            release();
            this.finish("failed");
            return;
        }
        recorder.ondataavailable = (event) => {
            if (event.data?.size) {
                chunks.push(event.data);
            }
        };
        recorder.onstop = () => {
            browser.clearTimeout(this.timer);
            this.unwatch?.();
            this.unwatch = null;
            release();
            this.recorder = null;
            this.send(new Blob(chunks, { type: recorder.mimeType || chunks[0]?.type || "" }));
        };
        this.recorder = recorder;
        recorder.start();
        this.timer = browser.setTimeout(() => this.stop(), MAX_SECONDS * 1000);
        if (!options.continuous) {
            // A question is over when the person goes quiet, as with the
            // recognition of a browser
            const decide = endOfSpeech();
            this.unwatch = speechApi.watchLevel(stream, (level) => {
                const verdict = decide(level, Date.now());
                if (verdict) {
                    this.isNobody = verdict === "nobody";
                    this.stop();
                }
            });
        }
        options.onPhase("listening");
    }

    async send(recording) {
        const { language, hints, onFinal, onPhase } = this.options;
        if (this.isAborted) {
            this.finish("");
            return;
        }
        if (!recording.size || this.isNobody) {
            this.finish("silence");
            return;
        }
        onPhase("transcribing");
        try {
            const wav = await speechApi.toWav(recording);
            const text = await transcribe(wav, language.code, hints);
            if (this.isAborted) {
                this.finish("");
                return;
            }
            if (text) {
                onFinal(text);
            }
            this.finish(text ? "" : "silence");
        } catch (error) {
            this.finish(error.code || "failed", error.code === "refused" ? error.message : "");
        }
    }

    finish(error, message = "") {
        if (!this.isOver) {
            this.isOver = true;
            this.options.onEnd(error, message);
        }
    }

    stop() {
        this.isStopping = true;
        if (this.recorder?.state === "recording") {
            this.recorder.stop();
        }
    }

    abort() {
        this.isAborted = true;
        this.stop();
        this.finish("");
    }
}
