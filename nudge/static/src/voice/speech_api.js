import { browser } from "@web/core/browser/browser";

import { toSpeechWav } from "./wav";

// How often the loudness of the microphone is looked at
export const LEVEL_EVERY_MS = 100;

/**
 * What the browser offers to listen and to speak, in one place: tests put
 * their own in its stead.
 */
export const speechApi = {
    /** @returns {Function|null} the constructor of the browser's own recognition */
    getRecognition() {
        return window.SpeechRecognition || window.webkitSpeechRecognition || null;
    },

    getSynthesis() {
        return window.speechSynthesis || null;
    },

    createUtterance(text) {
        return new window.SpeechSynthesisUtterance(text);
    },

    /** Browsers give the microphone to pages served over https, or by this computer */
    canRecord() {
        return Boolean(browser.navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
    },

    openMicrophone() {
        return browser.navigator.mediaDevices.getUserMedia({ audio: true });
    },

    createRecorder(stream) {
        return new window.MediaRecorder(stream);
    },

    /**
     * Tell how loud the microphone is, every tenth of a second.
     *
     * @param {MediaStream} stream
     * @param {(level: number) => void} onLevel the loudness, from 0 to 1
     * @returns {() => void} what stops telling it
     */
    watchLevel(stream, onLevel) {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        if (!AudioContext) {
            return () => {};
        }
        const context = new AudioContext();
        const analyser = context.createAnalyser();
        analyser.fftSize = 2048;
        context.createMediaStreamSource(stream).connect(analyser);
        const samples = new Float32Array(analyser.fftSize);
        const timer = browser.setInterval(() => {
            analyser.getFloatTimeDomainData(samples);
            let sum = 0;
            for (const sample of samples) {
                sum += sample * sample;
            }
            onLevel(Math.sqrt(sum / samples.length));
        }, LEVEL_EVERY_MS);
        return () => {
            browser.clearInterval(timer);
            context.close().catch(() => {});
        };
    },

    /** @returns {Promise<Blob>} the recording as the WAV file providers read */
    toWav(recording) {
        return toSpeechWav(recording);
    },

    /** @returns {string[]} the languages the person asks websites for, dialect included */
    getLanguages() {
        return [...(browser.navigator.languages || [])];
    },
};
