/** @odoo-module **/

import { browser } from "@web/core/browser/browser";

import { toSpeechWav } from "./wav";

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

    /** @returns {Promise<Blob>} the recording as the WAV file providers read */
    toWav(recording) {
        return toSpeechWav(recording);
    },

    /** @returns {string[]} the languages the person asks websites for, dialect included */
    getLanguages() {
        return [...(browser.navigator.languages || [])];
    },
};
