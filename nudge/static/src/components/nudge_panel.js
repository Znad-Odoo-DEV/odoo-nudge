import { Component, useEffect, useRef, useState } from "@odoo/owl";

import { deserializeDateTime } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";

import { DICTATION_HOTKEYS, hotkeyLabel, HOTKEYS, NO_HOTKEY } from "../core/hotkeys";
import { THEMES } from "../core/looks";
import { LANGUAGE_AUTO, shortName } from "../voice/languages";
import { NudgeFace } from "./nudge_face";
import { NudgeMessage } from "./nudge_message";

export class NudgePanel extends Component {
    static template = "nudge.Panel";
    static components = { NudgeFace, NudgeMessage };
    static props = {};

    setup() {
        this.nudge = useService("nudge");
        this.voice = useService("nudge_voice");
        this.action = useService("action");
        this.notification = useService("notification");
        this.state = useState(this.nudge.state);
        this.voiceState = useState(this.voice.state);
        this.bodyRef = useRef("body");
        this.inputRef = useRef("input");
        this.fileRef = useRef("file");
        // The search without AI, before a provider is set up
        this.quick = useState({ words: "", results: null, answer: null, isSearching: false });
        // The commands offered while one is typed: the one chosen with the arrows
        this.commandMenu = useState({ index: 0, closedFor: null });

        useEffect(
            () => this.inputRef.el?.focus(),
            () => [this.state.focusRequest, this.state.view]
        );
        // Keep the latest words in sight while the answer grows
        useEffect(
            () => {
                const body = this.bodyRef.el;
                if (body) {
                    body.scrollTop = body.scrollHeight;
                }
            },
            () => [this.state.view, this.state.messages.length, this.state.messages.at(-1)?.content]
        );
    }

    get greeting() {
        const firstName = (user.name || "").trim().split(/\s+/)[0];
        return firstName ? _t("Hi %s, I'm Nudge", firstName) : _t("Hi, I'm Nudge");
    }

    get isSeeing() {
        return this.state.canShareScreen && this.state.shareScreen;
    }

    /** Nudge acts: Agent mode, or Developer mode which holds everything of it */
    get isAgent() {
        return this.state.canUseAgent && this.state.mode !== "talk";
    }

    get isDev() {
        return this.state.canDevelop && this.state.mode === "dev";
    }

    get placeholder() {
        if (this.isDev) {
            return _t("Tell Nudge what to build…");
        }
        return this.isAgent ? _t("Tell Nudge what to do…") : _t("Ask Nudge…");
    }

    get suggestions() {
        if (this.isDev) {
            return [
                _t("Send an email when a record is confirmed"),
                _t("What runs by itself on this screen?"),
                _t("Add a server action to the Actions menu"),
            ];
        }
        if (this.isAgent) {
            return [
                _t("Create a contact"),
                _t("What is waiting for me today?"),
                _t("Find the last orders of this customer"),
            ];
        }
        if (this.isSeeing && this.state.screenHasView) {
            return [
                _t("What am I looking at?"),
                _t("What should I do next here?"),
                _t("Is anything missing on this screen?"),
            ];
        }
        return [
            _t("What can you help me with?"),
            _t("How do I create a quotation?"),
            _t("What is the difference between a quotation and a sales order?"),
        ];
    }

    hotkeyOptions(hotkeys) {
        return hotkeys.map((value) => ({
            value,
            label: value === NO_HOTKEY ? _t("No shortcut") : hotkeyLabel(value),
        }));
    }

    get hotkeys() {
        return this.hotkeyOptions(HOTKEYS);
    }

    get dictationHotkeys() {
        return this.hotkeyOptions(DICTATION_HOTKEYS);
    }

    get hotkeyHint() {
        return hotkeyLabel(this.state.hotkey);
    }

    get explainOptions() {
        return [
            { value: "ask", label: _t("Ask me each time") },
            { value: "screen", label: _t("On the screen, with the pointer") },
            { value: "text", label: _t("In writing, in the conversation") },
        ];
    }

    get themes() {
        const names = {
            violet: _t("Violet"),
            teal: _t("Teal"),
            amber: _t("Amber"),
            rose: _t("Rose"),
            slate: _t("Slate"),
        };
        return THEMES.map((value) => ({ value, label: names[value] || value }));
    }

    // ------------------------------------------------------------------
    // Voice
    // ------------------------------------------------------------------

    get canTalk() {
        return Boolean(this.voiceState.engine);
    }

    get isListening() {
        return this.voiceState.mode === "question";
    }

    get isTranscribing() {
        return this.isListening && this.voiceState.phase === "transcribing";
    }

    get languageName() {
        return shortName(this.voiceState.language);
    }

    get languages() {
        return [
            { value: LANGUAGE_AUTO, label: _t("Language of the interface") },
            { value: "ar", label: _t("Arabic") },
            { value: "en", label: _t("English") },
        ];
    }

    /** What the person is told about where their voice goes */
    get voiceNote() {
        if (!this.voiceState.isAllowed) {
            return _t("Your administrator turned voice off.");
        }
        if (this.voiceState.engine === "browser") {
            return _t(
                "Your browser turns what you say into text. Chrome, Edge and Safari send the sound to the servers of their maker to do so."
            );
        }
        if (this.voiceState.engine === "provider") {
            return _t(
                "What you say is sent to the AI provider, which turns it into text. No recording is kept."
            );
        }
        return _t("This browser cannot listen here. Use Chrome or Edge, on a secure address (https).");
    }

    /** Languages an answer can be written in and not read, for lack of a voice */
    get silentLanguages() {
        const names = { ar: _t("Arabic"), en: _t("English") };
        return Object.keys(names)
            .filter((language) => !this.voice.canSpeak(language))
            .map((language) => names[language])
            .join(", ");
    }

    // ------------------------------------------------------------------
    // Messages
    // ------------------------------------------------------------------

    get canSend() {
        return (
            Boolean(this.state.draft.trim() || this.state.attachment) &&
            !this.state.isStreaming &&
            !this.isListening
        );
    }

    send(text, options = {}) {
        if (this.state.isStreaming) {
            return;
        }
        this.nudge.send(text, options);
        this.state.draft = "";
        this.inputRef.el?.focus();
    }

    pickFile() {
        this.fileRef.el?.click();
    }

    onFileChosen(event) {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (!file) {
            return;
        }
        const problem = this.nudge.setAttachment(file);
        if (problem) {
            this.notification.add(problem, { type: "warning" });
        }
    }

    onSubmit() {
        if (this.canSend) {
            this.send(this.state.draft);
        }
    }

    /** The work is left to the background: the person goes on with something else */
    onSubmitLater() {
        if (this.canSend && this.isAgent) {
            this.send(this.state.draft, { background: true });
        }
    }

    // ------------------------------------------------------------------
    // In the background
    // ------------------------------------------------------------------

    get workingCount() {
        return this.state.news.filter((news) => ["queued", "running", "waiting"].includes(news.state))
            .length;
    }

    get workingText() {
        const count = this.workingCount;
        return count === 1
            ? _t("One request is worked on in the background.")
            : _t("%s requests are worked on in the background.", count);
    }

    /**
     * @param {number} conversationId
     * @returns {{ text: string, unseen: boolean } | null} what goes on in a conversation
     */
    newsBadge(conversationId) {
        const news = this.state.news.filter((news) => news.conversation_id === conversationId);
        if (!news.length) {
            return null;
        }
        const texts = {
            queued: _t("Working"),
            running: _t("Working"),
            waiting: _t("Needs you"),
            done: _t("Done"),
            failed: _t("Failed"),
            stopped: _t("Stopped"),
        };
        const latest = news[0];
        return { text: texts[latest.state] || latest.state, unseen: latest.is_unseen };
    }

    // ------------------------------------------------------------------
    // The commands of the message box
    // ------------------------------------------------------------------

    /** @returns {Object[]} the commands that start like the word being typed */
    get commandChoices() {
        const draft = this.state.draft || "";
        if (!draft.startsWith("/") || /\s/.test(draft) || this.commandMenu.closedFor === draft) {
            return [];
        }
        const typed = draft.toLowerCase();
        return this.state.commands.filter((command) => command.words.some((word) => word.startsWith(typed)));
    }

    get commandIndex() {
        return Math.min(this.commandMenu.index, Math.max(this.commandChoices.length - 1, 0));
    }

    /** @returns {string} the word of the command in the alphabet being typed */
    wordOf(command) {
        const typed = (this.state.draft || "").toLowerCase();
        return command.words.find((word) => typed.length > 1 && word.startsWith(typed)) || command.words[0];
    }

    pickCommand(command) {
        this.state.draft = `${this.wordOf(command)} `;
        this.commandMenu.index = 0;
        this.inputRef.el?.focus();
    }

    onInputKeydown(ev) {
        const choices = this.commandChoices;
        if (choices.length && !ev.isComposing) {
            if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
                ev.preventDefault();
                const step = ev.key === "ArrowDown" ? 1 : choices.length - 1;
                this.commandMenu.index = (this.commandIndex + step) % choices.length;
                return;
            }
            if (ev.key === "Tab" || (ev.key === "Enter" && !ev.shiftKey)) {
                ev.preventDefault();
                this.pickCommand(choices[this.commandIndex]);
                return;
            }
            if (ev.key === "Escape") {
                ev.preventDefault();
                ev.stopPropagation();
                this.commandMenu.closedFor = this.state.draft;
                return;
            }
        }
        // Enter sends; Shift+Enter, or Enter while composing a character, does not
        if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) {
            ev.preventDefault();
            if (this.isListening) {
                this.voice.stop();
            } else {
                this.onSubmit();
            }
        }
    }

    onKeydown(ev) {
        if (ev.key === "Escape") {
            ev.stopPropagation();
            if (this.isListening) {
                this.voice.cancel();
            } else {
                this.nudge.close();
            }
        }
    }

    formatDate(value) {
        return value ? deserializeDateTime(value).toRelative() : "";
    }

    async quickSearch() {
        const words = this.quick.words.trim();
        if (words.length < 2 || this.quick.isSearching) {
            return;
        }
        this.quick.isSearching = true;
        try {
            const typed = this.nudge.parseCommand(words);
            if (typed?.command.kind === "focused") {
                this.quick.results = null;
                this.quick.answer = {
                    content: _t("%s asks the AI: it works once a provider is set up.", typed.word),
                    state: "complete", error: "", products: null, found: null,
                };
            } else if (typed) {
                // A command answers here as in the chat, without any AI
                this.quick.results = null;
                this.quick.answer = await this.nudge.answerCommand(words);
            } else {
                this.quick.answer = null;
                this.quick.results = await this.nudge.quickFind(words);
            }
        } finally {
            this.quick.isSearching = false;
        }
    }

    get quickIsEmpty() {
        const results = this.quick.results;
        return Boolean(
            results && !results.menus.length && !results.settings.length && !results.pages.length &&
                !results.products?.length
        );
    }

    useQuickFound(kind, item) {
        if (kind === "command") {
            this.quick.words = `${item.words[0]} `;
            return;
        }
        return this.nudge.useFound(kind, item);
    }

    quickMenu(menu) {
        this.nudge.quickGuide("menu", menu.id);
    }

    quickSetting(setting) {
        this.nudge.quickGuide("setting", { app: setting.app, field: setting.field });
    }

    /** What the person asked Nudge to do by itself */
    openTasks() {
        this.nudge.close();
        this.action.doAction("nudge.nudge_task_action");
    }

    openProviders() {
        this.nudge.close();
        this.action.doAction("nudge.nudge_provider_action");
    }
}
