/** @odoo-module **/

import { reactive } from "@odoo/owl";

import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { session } from "@web/session";

import { commit, insertText, isEditable } from "./dictation";
import { BrowserEngine, ServerEngine } from "./engines";
import {
    detectLanguage,
    LANGUAGE_AUTO,
    languageOf,
    nextLanguage,
    resolveLanguage,
    speechTag,
} from "./languages";
import { speechApi } from "./speech_api";

const MAX_SPOKEN_CHARS = 1200;

/**
 * @param {string} markdown
 * @returns {string} the text as it is said: no sign of formatting, no address
 */
export function toSpokenText(markdown) {
    return String(markdown || "")
        .replace(/```[\s\S]*?```/g, " ")
        .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
        .replace(/https?:\/\/\S+/g, " ")
        .replace(/^\s{0,3}(#{1,6}|>|[-*+]|\d+[.)])\s+/gm, "")
        .replace(/[*_`~|]/g, "")
        .replace(/\s+/g, " ")
        .trim()
        .slice(0, MAX_SPOKEN_CHARS);
}

/**
 * @param {Object} params
 * @param {boolean} params.isAllowed whether the administrator allows voice
 * @param {string} params.wanted who the administrator wants to listen
 * @param {boolean} params.byBrowser whether this browser turns speech into text
 * @param {boolean} params.byProvider whether the AI provider does, and the
 *   browser can record for it
 * @returns {"browser"|"provider"|null} who turns speech into text here
 */
export function chooseEngine({ isAllowed, wanted, byBrowser, byProvider }) {
    if (!isAllowed) {
        return null;
    }
    if (wanted === "browser") {
        return byBrowser ? "browser" : null;
    }
    if (wanted === "provider") {
        return byProvider ? "provider" : null;
    }
    return byBrowser ? "browser" : byProvider ? "provider" : null;
}

/**
 * Listens to the person, and speaks to them.
 *
 * The microphone is open from the moment they ask for it to the moment they
 * are done, and never otherwise.
 */
export const nudgeVoiceService = {
    dependencies: ["notification", "user"],

    start(env, { notification, user }) {
        const config = session.nudge || {};
        const settings = user.settings || {};
        const state = reactive({
            isAllowed: Boolean(config.can_use_voice),
            // Who turns speech into text here: "browser", "provider", or
            // null when nobody can
            engine: null,
            // What is listened for: "question", "dictation", or null
            mode: null,
            // "idle", "listening", "transcribing"
            phase: "idle",
            interim: "",
            error: "",
            languageSetting: settings.nudge_voice_language || LANGUAGE_AUTO,
            language: "en",
            speakAnswers: Boolean(settings.nudge_speak_answers),
            isSpeaking: false,
            // The field a dictation is typed in
            target: null,
            // Private mode of the company, and whether it lets the browser speak and listen
            isPrivate: Boolean(config.voice_private),
            canUseBrowserSpeech: config.browser_speech_ok !== false,
            // A provider turns a recording into text: a meeting is sent as sound
            canTranscribe: Boolean(config.can_transcribe),
            // Why there is no microphone, when the person should be told; hidden: the browser cannot at all
            blockedReason: "",
            isBlockedHidden: false,
        });
        let current = null;
        // A language switched to by hand is kept, whatever is written next
        let isLanguageSwitched = false;

        function pickEngine() {
            return chooseEngine({
                isAllowed: state.isAllowed,
                wanted: config.voice_engine || "auto",
                byBrowser: Boolean(speechApi.getRecognition()),
                byProvider: Boolean(config.can_transcribe) && Boolean(speechApi.canRecord()),
            });
        }

        /** Why nobody can listen here, when there is something to tell the person */
        function updateBlocked() {
            let reason = "";
            let isHidden = false;
            if (state.isAllowed && !state.engine) {
                if (!speechApi.getRecognition() && (config.voice_engine === "browser" || state.isPrivate)) {
                    reason = _t(
                        "This browser cannot listen by itself. Chrome, Edge and Safari can; Firefox cannot."
                    );
                    isHidden = !config.voice_blocked_reason;
                }
                reason = config.voice_blocked_reason || reason;
            }
            Object.assign(state, { blockedReason: reason, isBlockedHidden: isHidden });
        }

        /** Look again at what this browser can do */
        function refresh() {
            state.engine = pickEngine();
            updateBlocked();
        }

        /** Tell the person why the microphone does not work */
        function explain() {
            if (state.blockedReason) {
                notification.add(state.blockedReason, { type: "info" });
            }
        }

        function saveSetting(name, value) {
            // The choice holds for this page even if it cannot be saved
            return user.setUserSettings(name, value).catch(() => {});
        }

        function errorMessage(error, message) {
            const messages = {
                silence: _t("I did not hear anything."),
                blocked: _t("The microphone is blocked. Allow it in the address bar of your browser."),
                no_microphone: _t("No microphone was found."),
                network: _t("Your browser could not reach its speech service."),
                language: _t("Your browser cannot listen to this language."),
            };
            return message || messages[error] || _t("What you said could not be turned into text.");
        }

        function release(listening) {
            if (current === listening) {
                current = null;
                Object.assign(state, { mode: null, phase: "idle", interim: "", target: null });
            }
        }

        /**
         * @param {Object} params
         * @param {"question"|"dictation"} params.mode
         * @param {boolean} [params.continuous] go on through silences, until told to stop
         * @param {string[]} [params.hints] words written on the screen
         * @param {HTMLElement} [params.target] the field a dictation is typed in
         * @param {(text: string) => void} [params.onInterim]
         * @param {(text: string) => void} params.onFinal
         * @param {(error: string, message: string, isCancelled: boolean) => void} [params.onEnd]
         * @returns {boolean} whether Nudge is listening
         */
        function listen(params) {
            state.engine = pickEngine();
            if (!state.engine || current) {
                return false;
            }
            stopSpeaking();
            const listening = { params, isDropped: false };
            listening.engine = state.engine === "browser" ? new BrowserEngine() : new ServerEngine();
            current = listening;
            Object.assign(state, {
                mode: params.mode,
                phase: "listening",
                interim: "",
                error: "",
                target: params.target || null,
            });
            listening.engine.start({
                language: {
                    code: state.language,
                    tag: speechTag(state.language, speechApi.getLanguages(), state.languageSetting),
                },
                continuous: params.continuous,
                hints: params.hints || [],
                onPhase: (phase) => {
                    if (current === listening) {
                        state.phase = phase;
                    }
                },
                onInterim: (text) => {
                    if (current === listening) {
                        state.interim = text;
                        params.onInterim?.(text);
                    }
                },
                onFinal: (text) => {
                    if (current === listening && !listening.isDropped) {
                        state.interim = "";
                        params.onFinal(text);
                    }
                },
                onEnd: (error, message) => {
                    if (current !== listening) {
                        return;
                    }
                    release(listening);
                    if (listening.isDropped) {
                        return;
                    }
                    state.error = error ? errorMessage(error, message) : "";
                    params.onEnd?.(error, state.error, false);
                },
            });
            return true;
        }

        /** The person is done: what they said is kept */
        function stop() {
            current?.engine.stop();
        }

        /** What was being said is dropped */
        function cancel() {
            const listening = current;
            if (listening) {
                listening.isDropped = true;
                listening.engine.abort();
                release(listening);
                listening.params.onEnd?.("", "", true);
            }
        }

        function applyLanguage() {
            state.language = resolveLanguage(state.languageSetting, user.lang);
        }

        /**
         * Who writes Arabic is listened to in Arabic, even with an interface in
         * another language; a language they chose stays theirs.
         *
         * @param {string} text what the person last wrote or said
         */
        function followText(text) {
            if (
                state.languageSetting !== LANGUAGE_AUTO ||
                isLanguageSwitched ||
                !/\p{L}/u.test(text || "")
            ) {
                return;
            }
            state.language =
                detectLanguage(text) === "ar" ? "ar" : resolveLanguage(LANGUAGE_AUTO, user.lang);
        }

        function setLanguage(setting) {
            state.languageSetting = setting || LANGUAGE_AUTO;
            applyLanguage();
            return saveSetting("nudge_voice_language", state.languageSetting);
        }

        /**
         * The next language, for the one who speaks more than one. What is
         * being listened to starts again in it.
         */
        function switchLanguage() {
            const language = nextLanguage(state.language, user.lang);
            const listening = current;
            isLanguageSwitched = true;
            const isAuto = language === languageOf(user.lang);
            setLanguage(isAuto ? LANGUAGE_AUTO : language);
            if (listening && !listening.isDropped) {
                listening.isDropped = true;
                listening.engine.abort();
                release(listening);
                listen(listening.params);
            }
        }

        function setSpeakAnswers(value) {
            state.speakAnswers = Boolean(value);
            if (!state.speakAnswers) {
                stopSpeaking();
            }
            return saveSetting("nudge_speak_answers", state.speakAnswers);
        }

        function findVoice(language) {
            const voices = speechApi.getSynthesis()?.getVoices() || [];
            return voices.find((voice) => languageOf(voice.lang) === language) || null;
        }

        /**
         * @param {string} language
         * @returns {boolean} whether this browser has a voice for it
         */
        function canSpeak(language) {
            return Boolean(findVoice(language));
        }

        /**
         * @param {string} text what to say, possibly written in Markdown
         * @param {Object} [options]
         * @param {boolean} [options.replace] instead of what is being said
         * @returns {boolean} whether it is being said
         */
        function speak(text, { replace = false } = {}) {
            const synthesis = speechApi.getSynthesis();
            if (
                !state.isAllowed ||
                !state.canUseBrowserSpeech ||
                !state.speakAnswers ||
                !synthesis ||
                current
            ) {
                return false;
            }
            const spoken = toSpokenText(text);
            const voice = spoken && findVoice(detectLanguage(spoken));
            if (!voice) {
                // Read by the voice of another language, a text is not understood
                return false;
            }
            if (replace) {
                synthesis.cancel();
            }
            const utterance = speechApi.createUtterance(spoken);
            utterance.voice = voice;
            utterance.lang = voice.lang;
            const onOver = () => {
                state.isSpeaking = Boolean(synthesis.pending || synthesis.speaking);
            };
            utterance.onend = onOver;
            utterance.onerror = onOver;
            state.isSpeaking = true;
            synthesis.speak(utterance);
            return true;
        }

        function stopSpeaking() {
            speechApi.getSynthesis()?.cancel();
            state.isSpeaking = false;
        }

        /**
         * Type what is said in the field the person is in.
         *
         * @param {Element|null} element the field
         * @param {Object} [options]
         * @param {string[]} [options.hints]
         * @returns {boolean} whether Nudge is listening
         */
        function dictate(element, { hints } = {}) {
            if (current) {
                return false;
            }
            if (!pickEngine()) {
                explain();
                return false;
            }
            if (!isEditable(element)) {
                notification.add(_t("Click in a text field first, then start the dictation."), {
                    type: "info",
                });
                return false;
            }
            return listen({
                mode: "dictation",
                continuous: true,
                hints,
                target: element,
                onFinal: (text) => insertText(element, text),
                onEnd: (error, message) => {
                    commit(element);
                    if (error && error !== "silence") {
                        notification.add(message, { type: "warning" });
                    }
                },
            });
        }

        applyLanguage();
        state.engine = pickEngine();
        updateBlocked();
        // Browsers load their voices when first asked for them
        speechApi.getSynthesis()?.getVoices();

        return {
            state,
            refresh,
            explain,
            listen,
            stop,
            cancel,
            dictate,
            setLanguage,
            switchLanguage,
            followText,
            setSpeakAnswers,
            speak,
            stopSpeaking,
            canSpeak,
        };
    },
};

registry.category("services").add("nudge_voice", nudgeVoiceService);
