import { Component, onMounted, onWillUnmount, useEffect, useRef, useState } from "@odoo/owl";

import { deserializeDateTime } from "@web/core/l10n/dates";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { user } from "@web/core/user";
import { useBus, useService } from "@web/core/utils/hooks";

import { DICTATION_HOTKEYS, hotkeyLabel, HOTKEYS, NO_HOTKEY } from "../core/hotkeys";
import { THEMES } from "../core/looks";
import { speechApi } from "../voice/speech_api";
import { createMeetingRecorder, TRANSCRIPT_NAME } from "../voice/meeting_recorder";
import { ARABIC_VARIANTS, ENGLISH_VARIANTS, LANGUAGE_AUTO, shortName } from "../voice/languages";
import { NudgeCoachCard } from "../coach/coach_card";
import { NudgeFace } from "./nudge_face";
import { NudgeImportCard } from "../import/import_card";
import { NudgeMessage } from "./nudge_message";
import { NudgePromptFill, NudgePromptSave } from "../prompts/prompt_forms";

// A finger drawn down this far closes the sheet on a phone
const SWIPE_CLOSE_PX = 80;

export class NudgePanel extends Component {
    static template = "nudge.Panel";
    static components = {
        NudgeCoachCard,
        NudgeFace,
        NudgeImportCard,
        NudgeMessage,
        NudgePromptFill,
        NudgePromptSave,
    };
    static props = {};

    setup() {
        this.nudge = useService("nudge");
        this.voice = useService("nudge_voice");
        this.action = useService("action");
        this.notification = useService("notification");
        this.orm = useService("orm");
        // The morning digest: the hour it arrives at, in the time zone of the person
        this.digest = useState({ hour: 8, busy: false, isSet: false });
        this.state = useState(this.nudge.state);
        this.voiceState = useState(this.voice.state);
        this.bodyRef = useRef("body");
        this.inputRef = useRef("input");
        this.fileRef = useRef("file");
        this.importFileRef = useRef("importFile");
        this.photoRef = useRef("photo");
        // Where a finger went down on the header of the sheet
        this.swipe = null;
        // A meeting recorded here, to be summarised
        this.meeting = useState({ on: false, seconds: 0 });
        this.recorder = null;
        onWillUnmount(() => this.recorder?.abort());
        this.importer = useService("nudge_import");
        // The prompt library: the chips of the screen, the form of the one chosen, "keep as a prompt"
        this.prompts = useState({ chips: [], fill: null, save: null });
        this.chipsKey = null;
        onMounted(() => this.loadChips());
        useBus(this.env.bus, "ACTION_MANAGER:UI-UPDATED", () => this.loadChips());
        // The search without AI, before a provider is set up
        this.quick = useState({
            words: "", results: null, answer: null, memory: null, action: null, isSearching: false,
        });
        // The AI over the records selected in a list
        this.orm = useService("orm");
        this.bulk = useState({
            seen: "", open: false, options: null, operation: "classify", field: "", instruction: "",
            language: "", everything: false,
        });
        const watching = browser.setInterval(() => {
            // The selection of a list is not told: it is looked at
            const target = this.nudge.state.isOpen ? this.nudge.bulkTarget() : null;
            const seen = target ? `${target.model}:${target.ids.length}:${target.total}` : "";
            if (seen !== this.bulk.seen) {
                this.bulk.seen = seen;
                if (!seen) {
                    this.bulk.open = false;
                    this.bulk.everything = false;
                }
            }
        }, 1000);
        onWillUnmount(() => browser.clearInterval(watching));
        // The commands offered while one is typed: the one chosen with the arrows
        this.commandMenu = useState({ index: 0, closedFor: null });
        // NP-23: the overlay that lists the commands
        this.help = useState({ open: false });

        useEffect(
            () => this.inputRef.el?.focus(),
            () => [this.state.focusRequest, this.state.view]
        );
        // Keep the latest words in sight while the answer grows
        useEffect(
            () => {
                const body = this.bodyRef.el;
                if (body) {
                    // The chat follows its last words; the other views open at their top
                    body.scrollTop = this.state.view === "chat" ? body.scrollHeight : 0;
                }
            },
            () => [this.state.view, this.state.messages.length, this.state.messages.at(-1)?.content]
        );
    }

    get bulkTarget() {
        // Read again whenever the selection changes: see ``bulk.seen``
        return this.bulk.seen ? this.nudge.bulkTarget() : null;
    }

    get bulkCount() {
        const target = this.bulkTarget;
        return target ? (this.bulk.everything ? target.total : target.ids.length) : 0;
    }

    get bulkFields() {
        const options = this.bulk.options;
        if (!options) {
            return [];
        }
        return { classify: options.classify, fill: options.fill }[this.bulk.operation] || options.text;
    }

    async openBulk() {
        const target = this.bulkTarget;
        if (!target) {
            return;
        }
        this.bulk.open = !this.bulk.open;
        if (this.bulk.open && !this.bulk.options) {
            this.bulk.options = await this.orm.call("nudge.bulk", "options", [target.model]);
            this.bulk.operation = this.bulk.options.classify.length ? "classify" : "summarise";
            this.bulk.field = this.bulkFields[0]?.name || "";
        }
    }

    chooseBulkOperation(operation) {
        this.bulk.operation = operation;
        this.bulk.field = this.bulkFields[0]?.name || "";
    }

    get canStartBulk() {
        const { operation, field, instruction, language } = this.bulk;
        return Boolean(
            field &&
                (operation !== "fill" || instruction.trim()) &&
                (operation !== "translate" || language.trim())
        );
    }

    async startBulk() {
        const target = this.bulkTarget;
        if (!target || !this.canStartBulk || this.state.isStreaming) {
            return;
        }
        const { operation, field, instruction, language, everything } = this.bulk;
        const work = {
            model: target.model,
            operation,
            field,
            instruction: instruction.trim(),
            language: language.trim(),
            ...(everything ? { domain: target.domain } : { ids: target.ids }),
        };
        this.bulk.open = false;
        await this.nudge.startBulk(work, _t("Do this for the %s selected", this.bulkCount));
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

    // ------------------------------------------------------------------
    // Prompt library
    // ------------------------------------------------------------------

    /** The model of the view in front: the chips follow it */
    get screenModel() {
        const current = this.action.currentController;
        return current?.props?.resModel || current?.action?.res_model || null;
    }

    async loadChips() {
        // Without a provider only the prompts the rules of Nudge understand are offered
        const withoutAi = !this.state.isConfigured;
        const key = `${this.screenModel}|${withoutAi}`;
        if (key === this.chipsKey) {
            return;
        }
        this.chipsKey = key;
        try {
            this.prompts.chips = await this.orm.call("nudge.prompt", "chips", [this.screenModel, withoutAi]);
        } catch {
            // The built-in suggestions stay
            this.prompts.chips = [];
            this.chipsKey = null;
        }
    }

    get promptChips() {
        return this.prompts.chips;
    }

    async onChip(chip) {
        if (this.state.isStreaming) {
            return;
        }
        // Words to fill in, or the text of a colleague: read first, sent when the person says so
        if (chip.variables.length || chip.preview) {
            this.prompts.fill = chip;
            return;
        }
        try {
            this.sendFilled(await this.orm.call("nudge.prompt", "render", [[chip.id], {}]));
        } catch (error) {
            this.notification.add(error?.data?.message || _t("This prompt could not be used."), { type: "danger" });
        }
    }

    sendFilled({ text, mode }) {
        this.prompts.fill = null;
        if (!this.state.isConfigured) {
            return this.nudge.sendWithoutAi(text);
        }
        // A prompt for Agent or Developer work puts the panel in that mode; one for Talk keeps the mode
        if (mode === "agent" && this.state.canUseAgent && this.state.mode === "talk") {
            this.nudge.setMode("agent");
        } else if (mode === "dev" && this.state.canDevelop && this.state.mode !== "dev") {
            this.nudge.setMode("dev");
        }
        this.send(text);
    }

    /** The words to keep: what is typed, else the last message that was sent */
    get promptSource() {
        const typed = this.state.draft.trim();
        if (typed) {
            return typed;
        }
        const last = [...this.state.messages].reverse().find((message) => message.role === "user");
        return (last?.content || "").trim();
    }

    get canSavePrompt() {
        return Boolean(this.promptSource) && !this.state.isStreaming;
    }

    openSave() {
        this.prompts.save = this.promptSource ? { text: this.promptSource, mode: this.state.mode } : null;
    }

    savedPrompt() {
        this.prompts.save = null;
        this.chipsKey = null;
        this.notification.add(_t("Kept as a prompt: it is offered as a chip on screens like this one."), { type: "success" });
        return this.loadChips();
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
                ...(this.state.setupOffer ? [_t("Set up this database with me")] : []),
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

    /** NP-23: the toggles of the look, shown in the preferences */
    get lookChoices() {
        return [
            { name: "highContrast", label: _t("High contrast") },
            { name: "largeText", label: _t("Large text") },
            { name: "compact", label: _t("Compact tiles on the home page") },
            { name: "dock", label: _t("Dock the panel at the side of the screen") },
            { name: "companyAccent", label: _t("Use the colour of my company") },
        ];
    }

    toggleHelp() {
        this.help.open = !this.help.open;
    }

    get themes() {
        const names = {
            odoo: _t("Odoo colour"),
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
            ...ARABIC_VARIANTS.map((tag) => ({ value: tag, label: this.variantName(tag) })),
            { value: "en", label: _t("English") },
            ...ENGLISH_VARIANTS.map((tag) => ({ value: tag, label: this.variantName(tag) })),
        ];
    }

    variantName(tag) {
        const names = {
            "ar-SA": _t("Arabic (Saudi Arabia)"),
            "ar-EG": _t("Arabic (Egypt)"),
            "ar-AE": _t("Arabic (UAE)"),
            "ar-SY": _t("Arabic (Syria)"),
            "ar-IQ": _t("Arabic (Iraq)"),
            "ar-JO": _t("Arabic (Jordan)"),
            "ar-LB": _t("Arabic (Lebanon)"),
            "ar-PS": _t("Arabic (Palestine)"),
            "ar-KW": _t("Arabic (Kuwait)"),
            "ar-QA": _t("Arabic (Qatar)"),
            "ar-BH": _t("Arabic (Bahrain)"),
            "ar-OM": _t("Arabic (Oman)"),
            "ar-YE": _t("Arabic (Yemen)"),
            "ar-SD": _t("Arabic (Sudan)"),
            "ar-MA": _t("Arabic (Morocco)"),
            "ar-DZ": _t("Arabic (Algeria)"),
            "ar-TN": _t("Arabic (Tunisia)"),
            "en-US": _t("English (United States)"),
            "en-GB": _t("English (United Kingdom)"),
        };
        return names[tag] || tag;
    }

    /** What the person is told about where their voice goes */
    get voiceNote() {
        if (!this.voiceState.isAllowed) {
            return _t("Your administrator turned voice off.");
        }
        if (this.voiceState.engine === "browser" && this.voiceState.isPrivate) {
            return _t(
                "Private mode is on, and your administrator allowed the speech of the browser. Chrome and Edge may send the sound to the servers of their maker, which is not your own server."
            );
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
        return (
            this.voiceState.blockedReason ||
            _t("This browser cannot listen here. Use Chrome or Edge, on a secure address (https).")
        );
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

    /** Whether a meeting can be recorded here: the browser records, and something can turn it into text */
    get canRecordMeeting() {
        if (!speechApi.canRecord() || this.state.isStreaming) {
            return false;
        }
        const voice = this.voiceState;
        return voice.isAllowed !== false && (voice.canTranscribe || (voice.canUseBrowserSpeech && Boolean(speechApi.getRecognition())));
    }

    // ----------------------------------------------------------------
    // On a phone
    // ----------------------------------------------------------------

    /** The big tiles of the sheet: those of the company, as far as they can be used now */
    get mobileTiles() {
        const tiles = {
            clock: { icon: "fa-clock-o", label: _t("Clock in/out") },
            photo: { icon: "fa-camera", label: _t("Photo of a bill") },
            tasks: { icon: "fa-tasks", label: _t("My tasks today") },
            record: { icon: "fa-file-text-o", label: _t("Ask about this record") },
            note: { icon: "fa-microphone", label: _t("Record a note") },
        };
        return this.state.mobileTiles
            .filter((id) => tiles[id])
            // A question about the record needs one on the screen; a note needs a microphone
            .filter((id) => id !== "record" || this.state.screenHasView)
            .filter((id) => id !== "note" || this.canRecordMeeting)
            .map((id) => ({ id, ...tiles[id] }));
    }

    onTile(id) {
        const nudge = this.nudge;
        switch (id) {
            case "clock":
                // The page of Odoo where the person checks in or out
                nudge.close();
                this.action.doAction("hr_attendance.hr_attendance_action_my_attendances");
                break;
            case "photo":
                this.photoRef.el?.click();
                break;
            case "tasks":
                this.askAsAgent(_t("What are my tasks for today?"));
                break;
            case "record":
                nudge.send(_t("Tell me about this record"));
                break;
            case "note":
                this.toggleMeeting();
                break;
        }
    }

    askAsAgent(text) {
        if (this.state.canUseAgent) {
            this.nudge.setMode("agent");
        }
        this.nudge.send(text);
    }

    onPhotoChosen(event) {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (!file) {
            return;
        }
        const problem = this.nudge.setAttachment(file);
        if (problem) {
            this.notification.add(problem, { type: "warning" });
            return;
        }
        this.askAsAgent(_t("Make a draft vendor bill from this photo"));
    }

    onHoldStart(event) {
        event.currentTarget?.setPointerCapture?.(event.pointerId);
        if (this.voiceState.mode !== "question") {
            this.nudge.talk({ isHeld: true });
        }
    }

    onHoldEnd() {
        if (this.voiceState.mode === "question") {
            this.voice.stop();
        }
    }

    /** A long press is not a menu */
    onHoldMenu() {}

    /** The button is held with the space bar or Enter as well */
    onHoldKey(event) {
        if (event.key !== " " && event.key !== "Enter") {
            return;
        }
        event.preventDefault();
        if (event.type === "keydown" && !event.repeat) {
            this.onHoldStart({});
        } else if (event.type === "keyup") {
            this.onHoldEnd();
        }
    }

    onSwipeStart(event) {
        const touch = event.changedTouches?.[0];
        this.swipe = touch ? { x: touch.clientX, y: touch.clientY } : null;
    }

    /** A finger drawn down the header closes the sheet */
    onSwipeEnd(event) {
        const start = this.swipe;
        const touch = event.changedTouches?.[0];
        this.swipe = null;
        if (!start || !touch || !this.state.isMobile) {
            return;
        }
        if (touch.clientY - start.y >= SWIPE_CLOSE_PX && Math.abs(touch.clientX - start.x) < SWIPE_CLOSE_PX) {
            this.nudge.close();
        }
    }

    get meetingTime() {
        const seconds = this.meeting.seconds;
        return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    }

    async toggleMeeting() {
        if (this.meeting.on) {
            const recorder = this.recorder;
            this.recorder = null;
            const heard = await recorder.stop();
            this.meeting.on = false;
            this.finishMeeting(heard);
            return;
        }
        // The sound is sent when a provider can listen; otherwise the words the browser heard
        const voice = this.voiceState;
        this.recorder = createMeetingRecorder({
            listen: !voice.canTranscribe,
            language: voice.language === "ar" ? "ar" : "",
            onTick: (seconds) => (this.meeting.seconds = seconds),
            onStopped: () => this.toggleMeeting(),
        });
        const problem = await this.recorder.start();
        if (problem) {
            this.recorder = null;
            this.notification.add(problem, { type: "warning" });
            return;
        }
        Object.assign(this.meeting, { on: true, seconds: 0 });
    }

    finishMeeting({ audio, transcript }) {
        const file = this.voiceState.canTranscribe && audio
            ? audio
            : transcript
              ? new File([transcript], TRANSCRIPT_NAME, { type: "text/plain" })
              : null;
        if (!file) {
            this.notification.add(_t("Nothing was recorded."), { type: "warning" });
            return;
        }
        const problem = this.nudge.setAttachment(file);
        if (problem) {
            this.notification.add(problem, { type: "warning" });
            return;
        }
        if (!this.state.draft.trim()) {
            this.state.draft = _t("Summarise this meeting");
        }
        this.inputRef.el?.focus();
    }

    pickImportFile() {
        this.importFileRef.el?.click();
    }

    /** A file chosen where there is no AI to talk to: its columns are matched */
    onImportFileChosen(event) {
        const file = event.target.files?.[0];
        event.target.value = "";
        if (file) {
            this.importer.start(file);
        }
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
            if (this.help.open) {
                this.help.open = false;
            } else if (this.isListening) {
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
        this.quick.memory = null;
        this.quick.action = null;
        try {
            const typed = this.nudge.parseCommand(words);
            if (typed?.command.kind === "focused") {
                this.quick.results = null;
                this.quick.answer = {
                    content: _t("%s asks the AI: it works once a provider is set up.", typed.word),
                    state: "complete", error: "", products: null,
                    // What the screen can say of itself is not for the AI to answer
                    found: this.screenCommands.length ? { commands: this.screenCommands } : null,
                };
            } else if (typed) {
                // A command answers here as in the chat, without any AI
                this.quick.results = null;
                this.quick.answer = await this.nudge.answerCommand(words);
            } else {
                this.quick.answer = null;
                this.quick.action = (await this.nudge.quickAction(words)) || null;
                this.quick.memory = (await this.nudge.quickRemembered(words)) || null;
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
                !results.products?.length && !results.tours?.length
        );
    }

    /** A saved record is on the screen and the person may let Nudge work: who changed it can be asked */
    get canSeeChanges() {
        return Boolean(this.nudge.state.canUseAgent && this.nudge.changesTarget());
    }

    /** What the screen can say of itself without any AI, offered while no provider is set up */
    get screenCommands() {
        return this.nudge.state.commands.filter((command) => command.at_once);
    }

    useQuickFound(kind, item) {
        if (kind === "explain") {
            this.quick.words = `/field ${item.field}`;
            return this.quickSearch();
        }
        if (kind === "command" && item.at_once) {
            this.quick.words = item.words[0];
            return this.quickSearch();
        }
        if (kind === "command") {
            this.quick.words = `${item.words[0]} `;
            return;
        }
        return this.nudge.useFound(kind, item);
    }

    /** The request understood without any AI, done: the answer is in the chat */
    async doQuickAction() {
        const words = this.quick.words.trim();
        this.quick.action = null;
        this.quick.words = "";
        this.quick.results = null;
        await this.nudge.sendWithoutAi(words);
    }

    /** The composer is there once a provider is set up, or a request was understood without one */
    get canCompose() {
        const state = this.state;
        return state.isConfigured || (state.canWorkWithoutAi && state.messages.length > 0);
    }

    quickMenu(menu) {
        this.nudge.quickGuide("menu", menu.id);
    }

    quickSetting(setting) {
        this.nudge.quickGuide("setting", { app: setting.app, field: setting.field });
    }

    get digestHours() {
        return Array.from({ length: 24 }, (_, hour) => ({
            value: hour,
            label: `${String(hour).padStart(2, "0")}:00`,
        }));
    }

    /** One click: the digest of the apps the person works in, every morning */
    async setupDigest() {
        this.digest.busy = true;
        try {
            const result = await this.orm.call("nudge.task", "action_setup_digest", [], {
                hour: Number(this.digest.hour),
            });
            this.digest.isSet = true;
            this.notification.add(
                _t("Your digest will arrive every day at %(hour)s:00 (%(tz)s).", {
                    hour: String(result.hour).padStart(2, "0"),
                    tz: result.tz,
                }),
                { type: "success" }
            );
        } finally {
            this.digest.busy = false;
        }
    }

    /** Read today's digest now, in a conversation */
    async previewDigest() {
        this.digest.busy = true;
        try {
            const conversationId = await this.orm.call("nudge.task", "action_preview_digest", []);
            await this.nudge.openConversation(conversationId);
        } finally {
            this.digest.busy = false;
        }
    }

    /** Warn about every check at entry time again, those the person muted included */
    async showMutedWarnings() {
        await this.orm.call("nudge.validator", "unmute_all", []);
        this.notification.add(_t("You will be warned again while you fill in forms."), { type: "success" });
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
