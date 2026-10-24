/** @odoo-module **/

import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";

import { speechApi } from "./speech_api";

// The server takes a file up to this size (llm/attachments.py): the recording stops before it
export const MAX_MEETING_BYTES = 10 * 1048576 - 65536;
export const TRANSCRIPT_NAME = "meeting-transcript.txt";
const EXTENSIONS = { "audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3" };

function microphoneMessage(error) {
    if (error?.name === "NotAllowedError" || error?.name === "SecurityError") {
        return _t("The microphone is blocked. Allow it in the address bar of your browser.");
    }
    if (error?.name === "NotFoundError") {
        return _t("No microphone was found.");
    }
    return _t("The recording could not start.");
}

/**
 * Records a meeting in the panel. The sound is kept in the browser until the
 * person sends it. When the browser may listen while it records (the
 * administrator allows it, and no provider can listen) the words it heard
 * are returned too, to be sent instead of the sound.
 *
 * @param {Object} options
 * @param {boolean} options.listen the browser transcribes while recording
 * @param {string} [options.language] BCP 47 tag the browser listens in
 * @param {(seconds: number) => void} [options.onTick]
 * @param {(reason: string) => void} [options.onStopped] it ended by itself
 */
export function createMeetingRecorder({ listen = false, language = "", onTick, onStopped } = {}) {
    let stream = null;
    let recorder = null;
    let recognition = null;
    let timer = null;
    let chunks = [];
    let bytes = 0;
    let seconds = 0;
    let words = [];
    let isRecording = false;
    let starting = false;
    let finish = null;

    function startRecognition() {
        const Recognition = listen ? speechApi.getRecognition() : null;
        if (!Recognition) {
            return;
        }
        recognition = new Recognition();
        recognition.continuous = true;
        recognition.interimResults = false;
        if (language) {
            recognition.lang = language;
        }
        recognition.onresult = (event) => {
            for (let i = event.resultIndex; i < event.results.length; i++) {
                if (event.results[i].isFinal) {
                    words.push(event.results[i][0].transcript.trim());
                }
            }
        };
        // Browsers end a recognition by themselves after a silence: it is started again
        recognition.onend = () => {
            if (isRecording) {
                try {
                    recognition.start();
                } catch {
                    // already running
                }
            }
        };
        try {
            recognition.start();
        } catch {
            recognition = null;
        }
    }

    function release() {
        isRecording = false;
        browser.clearInterval(timer);
        if (recognition) {
            recognition.onend = null;
            try {
                recognition.stop();
            } catch {
                // already stopped
            }
        }
        stream?.getTracks().forEach((track) => track.stop());
    }

    function build() {
        const type = (recorder?.mimeType || "audio/webm").split(";")[0];
        const extension = EXTENSIONS[type] || "webm";
        const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");
        return {
            audio: bytes ? new File(chunks, `meeting-${stamp}.${extension}`, { type }) : null,
            transcript: words.join(" ").trim(),
            seconds,
        };
    }

    return {
        get isRecording() {
            return isRecording;
        },
        /** @returns {Promise<string>} what is wrong, or "" when it recorded */
        async start() {
            if (isRecording || starting) {
                // A second call would open a second microphone that nothing ever stops
                return "";
            }
            if (!speechApi.canRecord()) {
                return _t("This browser cannot record.");
            }
            starting = true;
            try {
                stream = await speechApi.openMicrophone();
            } catch (error) {
                return microphoneMessage(error);
            } finally {
                starting = false;
            }
            recorder = speechApi.createRecorder(stream);
            recorder.ondataavailable = (event) => {
                if (event.data?.size) {
                    chunks.push(event.data);
                    bytes += event.data.size;
                    if (bytes >= MAX_MEETING_BYTES) {
                        this.stop();
                        onStopped?.("size");
                    }
                }
            };
            recorder.onstop = () => finish?.(build());
            recorder.start(5000);
            isRecording = true;
            startRecognition();
            timer = browser.setInterval(() => onTick?.(++seconds), 1000);
            return "";
        },
        /** @returns {Promise<{audio: File|null, transcript: string, seconds: number}>} */
        stop() {
            if (!isRecording) {
                return Promise.resolve(build());
            }
            return new Promise((resolve) => {
                finish = resolve;
                release();
                recorder.stop();
            });
        },
        /** Drops everything: nothing is kept */
        abort() {
            release();
            finish = null;
            try {
                recorder?.stop();
            } catch {
                // already stopped
            }
            chunks = [];
            words = [];
        },
    };
}
