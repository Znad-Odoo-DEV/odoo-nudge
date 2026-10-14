/** @odoo-module **/

import { reactive } from "@odoo/owl";

import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { session } from "@web/session";

import { NudgeRoot } from "../components/nudge_root";
import { writeInField } from "../context/form_writer";
import { holdable } from "./hold";
import { DEFAULT_DICTATION_HOTKEY, DEFAULT_HOTKEY, NO_HOTKEY } from "./hotkeys";
import { DEFAULT_THEME, readPosition, THEMES } from "./looks";
import { streamChat, streamDecision } from "./stream_client";

const HISTORY_LIMIT = 30;
// A change of the settings is read in a new page: menus and rights change with it
const SETTINGS_MODEL = "res.config.settings";
const MAX_HINTS = 40;
// Buttons first: their names are what people say in the middle of a sentence
const HINT_SOURCES = ["buttons", "tabs", "fields", "columns"];

export const MODE_TALK = "talk";
export const MODE_AGENT = "agent";
export const MODE_DEV = "dev";

// Said or written to Nudge, it means: do the work
const DO_IT = new RegExp(
    "^\\s*(nudge\\s*[,،:]?\\s*)?(" +
        ["do it", "do that", "go ahead", "نف[ّ]?ذ(ها|ه)?", "اعمل(ها|ه)", "سو[ّ]?ي(ها|ه)"].join("|") +
        ")(?=$|[\\s.,!،])",
    "i"
);

// Or said in a sentence: "make the automation yourself", "اعمل ال automation انت"
const DO_IT_YOURSELF = new RegExp(
    "(\\b(you do it|do it yourself|do it for me|make it yourself|build it yourself)\\b" +
        "|^\\s*(اعمل|نف[ّ]?ذ|سو[ّ]?ي|طب[ّ]?ق|ابني|ضيف|أضف|اضف)(ها|ه|هم)?\\s(.*\\s)?(انت|إنت|أنت|عني|عنّي|بنفسك|الك)\\s*[.!؟?]*\\s*$)",
    "i"
);

/**
 * @param {string} text
 * @returns {boolean} whether the message asks Nudge to do the work
 */
export function asksToDoIt(text) {
    const said = String(text || "");
    return DO_IT.test(said) || DO_IT_YOURSELF.test(said);
}

// A question of how, or of where: the steps, written or shown, may be what
// the person wants, even in Agent mode
const HOW_TO = new RegExp(
    "(\\b(how (do|can|could|would|should|to|is|are|does)|where (do|can|is|are|should|does)|show me|teach me|" +
        "walk me through|what are the steps)\\b" +
        "|كيف|كيفية|شلون|ازاي|إزاي|وين|فين|فرجيني|ورجيني|وريني|علمني|خطوات|شو الطريقة|ما الطريقة)",
    "i"
);

/**
 * @param {string} text
 * @returns {boolean} whether the message asks how something is done
 */
export function asksHowTo(text) {
    return HOW_TO.test(String(text || ""));
}

// A comparison or a report: the person is asked whether they want it in the
// chat or as a spreadsheet
const REPORT = new RegExp(
    "(\\bcompar|\\breport\\b|\\bvs\\.?\\b|\\bversus\\b|\\bstatistic|\\bsummary\\b|\\bbreakdown\\b|\\bper (month|week|department|warehouse)\\b" +
        "|مقارن|قارن|تقرير|مقابل|احصائ|إحصائ|ملخص|تلخيص|حسب (الشهر|القسم|المستودع|الاسبوع|الأسبوع))",
    "i"
);

export function asksForReport(text) {
    return REPORT.test(String(text || ""));
}

function quote(text) {
    return String(text)
        .split("\n")
        .map((line) => `> ${line}`)
        .join("\n");
}

function join(...parts) {
    return parts.filter(Boolean).join("\n\n");
}

// The conversation kept across a reload of the page, for so long
const KEPT_KEY = "nudge.kept_conversation";
const KEPT_MS = 60000;

export const nudgeService = {
    dependencies: [
        "orm",
        "user",
        "hotkey",
        "menu",
        "action",
        "notification",
        "bus_service",
        "nudge_context",
        "nudge_guidance",
        "nudge_voice",
    ],

    start(
        env,
        {
            orm,
            user,
            hotkey: hotkeyService,
            menu,
            action,
            notification,
            bus_service: bus,
            nudge_context: context,
            nudge_guidance: guidance,
            nudge_voice: voice,
        }
    ) {
        // Present only for users of the Nudge groups
        const config = session.nudge;
        const settings = user.settings || {};
        const canUseAgent = Boolean(config?.can_use_agent);
        const canDevelop = canUseAgent && Boolean(config?.can_develop);
        // The mode asked for, among those open to this person
        const allowedMode = (mode) => {
            if (mode === MODE_DEV && canDevelop) {
                return MODE_DEV;
            }
            return canUseAgent && [MODE_AGENT, MODE_DEV].includes(mode) ? MODE_AGENT : MODE_TALK;
        };
        const state = reactive({
            // Nudge is for this person (its groups) ...
            isAvailable: Boolean(config),
            // ... and they have it switched on, from their own menu
            isEnabled: Boolean(config) && settings.nudge_enabled !== false,
            // The server was updated after this page was loaded
            isOutdated: false,
            // A file chosen for the next question: { file, name, size }
            attachment: null,
            // A report can be made as a spreadsheet, with the module for it
            canMakeSpreadsheet: Boolean(config?.can_make_spreadsheet) && canUseAgent,
            isManager: Boolean(config?.is_manager),
            isConfigured: Boolean(config?.is_configured),
            // The commands of the message box this person may type: /screen, /find...
            commands: Array.isArray(config?.commands) ? config.commands : [],
            isOpen: false,
            view: "chat",
            conversationId: null,
            title: "",
            messages: [],
            isStreaming: false,
            conversations: [],
            // What is being written, or said, in the message box
            draft: "",
            // Whether the administrator allows it, and whether this person wants it
            canShareScreen: Boolean(config?.can_share_screen),
            shareScreen: settings.nudge_share_screen !== false,
            screenSummary: "",
            // The screen shows records: a form, a list, a kanban, not only an app
            screenHasView: false,
            // Talk: Nudge explains and shows. Agent: Nudge does the work.
            canUseAgent,
            // Developer: Nudge builds, for an administrator the switch is on for
            canDevelop,
            mode: allowedMode(settings.nudge_mode),
            // How the person wants the answer to a question asked with their
            // screen: asked each time, on the screen, or in writing
            explain: ["ask", "screen", "text"].includes(settings.nudge_explain)
                ? settings.nudge_explain
                : "ask",
            // The question waiting for the person to say how, with its message
            asking: null,
            // Where and in what colour the person wants Nudge
            theme: THEMES.includes(settings.nudge_theme) ? settings.nudge_theme : DEFAULT_THEME,
            position: readPosition(settings.nudge_position),
            hotkey: settings.nudge_hotkey || DEFAULT_HOTKEY,
            dictationHotkey: settings.nudge_dictation_hotkey || DEFAULT_DICTATION_HOTKEY,
            // What is worked on in the background, and what ended since the
            // person last looked
            news: [],
            // Changes each time the message box must take the focus
            focusRequest: 0,
        });
        let abortController = null;
        let localId = 0;
        let removeHotkeys = [];

        async function refreshStatus() {
            const status = await orm.call("nudge.conversation", "get_status", []);
            state.isManager = Boolean(status.is_manager);
            state.isConfigured = Boolean(status.is_configured);
        }

        function isSharing() {
            return state.canShareScreen && state.shareScreen;
        }

        /**
         * Describe the screen now. Nothing leaves the browser here: the
         * description is sent only with a message.
         */
        // How the person wants the answer to a question asked with their screen
        const EXPLAIN_ASK = "ask";
        const EXPLAIN_SCREEN = "screen";
        const EXPLAIN_TEXT = "text";
        // Not an explanation: the work itself, asked of the agent
        const EXPLAIN_DO = "do";
// As the server reads them (llm/attachments.py)
const MAX_ATTACHMENT_BYTES = 10 * 1048576;
const READABLE_TYPES = /^(application\/(x-)?pdf|text\/|application\/(json|csv|xml)|image\/(png|jpeg|webp|gif))/;
const READABLE_EXTENSIONS = /\.(pdf|txt|csv|md|json|xml|tsv|log|png|jpe?g|webp|gif)$/i;

function isReadable(file) {
    return READABLE_TYPES.test(file.type || "") || READABLE_EXTENSIONS.test(file.name || "");
}
        const EXPLAIN_CHOICES = [EXPLAIN_SCREEN, EXPLAIN_TEXT];

        function lookAtScreen() {
            const capture = isSharing() ? context.capture() : null;
            state.screenSummary = capture?.summary || "";
            state.screenHasView = Boolean(capture?.snapshot?.view_type || capture?.snapshot?.record);
            return capture?.snapshot || null;
        }

        /**
         * @returns {string[]} what is written on the buttons, tabs and fields
         *   of the screen: the words most likely to be said about it. Labels
         *   only, never what the records hold.
         */
        function screenHints() {
            const snapshot = isSharing() ? context.capture()?.snapshot : null;
            const labels = new Set();
            for (const source of HINT_SOURCES) {
                for (const entry of snapshot?.[source] || []) {
                    if (typeof entry.label === "string" && entry.label) {
                        labels.add(entry.label);
                    }
                }
            }
            return [...labels].slice(0, MAX_HINTS);
        }

        function open() {
            state.isOpen = true;
            state.focusRequest++;
            lookAtScreen();
            voice.refresh();
            if (!state.isConfigured) {
                // A provider may have been set up since the page was loaded
                refreshStatus();
            }
        }

        function close() {
            if (voice.state.mode === "question") {
                voice.cancel();
            }
            voice.stopSpeaking();
            state.isOpen = false;
        }

        function toggle() {
            if (state.isOpen) {
                close();
            } else {
                open();
            }
        }

        /**
         * Nudge: On / Off, from the user menu. Off, the avatar, the button of
         * the top bar and the shortcuts go, at once and on every page after.
         *
         * @param {boolean} value
         */
        function setEnabled(value) {
            if (!state.isAvailable) {
                return Promise.resolve();
            }
            state.isEnabled = Boolean(value);
            if (!state.isEnabled) {
                close();
            }
            registerHotkeys();
            return saveSetting("nudge_enabled", state.isEnabled);
        }

        function saveSetting(name, value) {
            // The choice holds for this page even if it cannot be saved
            return user.setUserSettings(name, value).catch(() => {});
        }

        function setShareScreen(value) {
            state.shareScreen = Boolean(value);
            lookAtScreen();
            return saveSetting("nudge_share_screen", state.shareScreen);
        }

        function setMode(mode) {
            state.mode = allowedMode(mode);
            state.focusRequest++;
            return saveSetting("nudge_mode", state.mode);
        }

        function setExplain(value) {
            state.explain = [EXPLAIN_ASK, ...EXPLAIN_CHOICES].includes(value) ? value : EXPLAIN_ASK;
            return saveSetting("nudge_explain", state.explain);
        }

        function setTheme(theme) {
            state.theme = THEMES.includes(theme) ? theme : DEFAULT_THEME;
            return saveSetting("nudge_theme", state.theme);
        }

        /**
         * @param {{ x: number, y: number } | null} position of the avatar, as
         *   fractions of the screen; null puts it back in the corner
         */
        function setPosition(position) {
            state.position = readPosition(position ? JSON.stringify(position) : "");
            return saveSetting("nudge_position", state.position ? JSON.stringify(state.position) : false);
        }

        // --------------------------------------------------------------
        // Voice
        // --------------------------------------------------------------

        /**
         * Listen to a question. What is heard is shown in the message box as
         * it comes, and sent when the person is done.
         *
         * @param {Object} [options]
         * @param {boolean} [options.isHeld] listen until told to stop, not until a silence
         * @returns {boolean} whether Nudge is listening
         */
        function talk({ isHeld = false } = {}) {
            if (!state.isEnabled || !state.isConfigured || state.isStreaming) {
                return false;
            }
            const typed = state.draft.trim();
            voice.followText(
                typed || state.messages.findLast((message) => message.role === "user")?.content
            );
            let heard = "";
            const show = (interim) => {
                state.draft = [typed, heard, interim].filter(Boolean).join(" ");
            };
            const isListening = voice.listen({
                mode: "question",
                continuous: isHeld,
                hints: screenHints(),
                onInterim: show,
                onFinal: (text) => {
                    heard = [heard, text].filter(Boolean).join(" ");
                    show("");
                },
                onEnd: (error, message, isCancelled) => {
                    show("");
                    state.focusRequest++;
                    // What was typed before is theirs to read again, and to send
                    if (heard && !typed && !isCancelled) {
                        state.draft = "";
                        send(heard, { spoken: true });
                    }
                },
            });
            if (isListening) {
                state.isOpen = true;
                state.view = "chat";
            }
            return isListening;
        }

        function toggleTalk() {
            if (voice.state.mode === "question") {
                voice.stop();
                return false;
            }
            return talk();
        }

        function dictate() {
            const element = document.activeElement;
            if (element?.closest?.(".o_nudge")) {
                // In the message box, what is dictated is a message
                return talk({ isHeld: true });
            }
            return voice.dictate(element, { hints: screenHints() });
        }

        function addHotkey(hotkey, callback) {
            if (!hotkey || hotkey === NO_HOTKEY) {
                return;
            }
            try {
                removeHotkeys.push(
                    hotkeyService.add(hotkey, callback, {
                        // From anywhere, a field being edited included
                        global: true,
                        bypassEditableProtection: true,
                    })
                );
            } catch {
                // A combination the hotkey service refuses leaves Nudge without shortcut
            }
        }

        function registerHotkeys() {
            for (const remove of removeHotkeys) {
                remove();
            }
            removeHotkeys = [];
            if (!state.isEnabled) {
                return;
            }
            // Tapped, the shortcut calls Nudge. Held, it listens as long as
            // it is held.
            addHotkey(
                state.hotkey,
                holdable({
                    onPress: open,
                    onHold: () => talk({ isHeld: true }),
                    onRelease: () => {
                        if (voice.state.mode === "question") {
                            voice.stop();
                        }
                    },
                })
            );
            if (!voice.state.isAllowed || state.dictationHotkey === state.hotkey) {
                return;
            }
            // Tapped, the shortcut starts a dictation and ends it. Held, the
            // dictation lasts as long as it is held.
            let isStarting = false;
            let isHeld = false;
            addHotkey(
                state.dictationHotkey,
                holdable({
                    onPress: () => {
                        isStarting = !voice.state.mode;
                        if (isStarting) {
                            dictate();
                        } else {
                            voice.stop();
                        }
                    },
                    onHold: () => {
                        isHeld = isStarting && Boolean(voice.state.mode);
                    },
                    onRelease: () => {
                        if (isHeld) {
                            isHeld = false;
                            voice.stop();
                        }
                    },
                })
            );
        }

        function setHotkey(value) {
            state.hotkey = value || NO_HOTKEY;
            registerHotkeys();
            return saveSetting("nudge_hotkey", state.hotkey);
        }

        function setDictationHotkey(value) {
            state.dictationHotkey = value || NO_HOTKEY;
            registerHotkeys();
            return saveSetting("nudge_dictation_hotkey", state.dictationHotkey);
        }

        // --------------------------------------------------------------
        // Conversations
        // --------------------------------------------------------------

        function stop() {
            abortController?.abort();
            voice.stopSpeaking();
        }

        function newConversation() {
            stop();
            state.view = "chat";
            state.conversationId = null;
            state.title = "";
            state.messages = [];
            state.asking = null;
            state.draft = "";
            state.focusRequest++;
        }

        async function showHistory() {
            state.view = "history";
            state.conversations = await orm.searchRead(
                "nudge.conversation",
                [["user_id", "=", user.userId]],
                ["name", "write_date", "message_count"],
                { limit: HISTORY_LIMIT, order: "write_date desc, id desc" }
            );
        }

        function showChat() {
            state.view = "chat";
            state.focusRequest++;
        }

        function showSettings() {
            state.view = "settings";
        }

        function writtenText(event) {
            return join(_t("Written in “%s”:", event.label || event.field), quote(event.text));
        }

        // --------------------------------------------------------------
        // In the background
        // --------------------------------------------------------------

        function newsOf(conversationId) {
            return state.news.filter((news) => news.conversation_id === conversationId);
        }

        /**
         * @returns {number} what the person has not looked at yet
         */
        function unseenCount() {
            return state.news.filter((news) => news.is_unseen).length;
        }

        async function loadNews() {
            if (!state.canUseAgent) {
                return;
            }
            try {
                state.news = await orm.call("nudge.run", "get_news", []);
            } catch {
                state.news = [];
            }
        }

        /**
         * The person looked at the conversation: what it holds is no news
         */
        async function markSeen(conversationId) {
            const unseen = newsOf(conversationId).filter((news) => news.is_unseen);
            if (!unseen.length) {
                return;
            }
            for (const news of unseen) {
                news.is_unseen = false;
            }
            state.news = state.news.filter(
                (news) => news.is_unseen || !["done", "failed", "stopped"].includes(news.state)
            );
            try {
                await orm.call("nudge.run", "action_seen", [unseen.map((news) => news.run_id)]);
            } catch {
                // Told again next time, at worst
            }
        }

        function show(conversationId) {
            state.isOpen = true;
            state.view = "chat";
            return openConversation(conversationId);
        }

        function outcomeOf(payload) {
            const outcomes = {
                waiting: _t("Nudge needs your answer"),
                done: _t("Nudge is done"),
                failed: _t("Nudge could not finish"),
                stopped: _t("Nudge stopped"),
            };
            return outcomes[payload.kind] || "";
        }

        /**
         * @param {{ model: string, ids?: number[] }[]} [changed] records
         *  written on the server, which the screen in front may show
         */
        async function refreshAll(changed) {
            for (const records of changed || []) {
                try {
                    await refreshScreen(records);
                } catch {
                    // Written all the same: a screen that cannot be refreshed is refreshed by hand
                }
            }
        }

        /**
         * Something happened to a request worked on in the background.
         *
         * @param {Object} payload as the server sent it
         */
        function onNews(payload) {
            // What was done in the background is on the screen in front
            refreshAll(payload.changed);
            const isOver = ["done", "failed", "stopped"].includes(payload.kind);
            const known = state.news.find((news) => news.run_id === payload.run_id);
            const entry = {
                run_id: payload.run_id,
                conversation_id: payload.conversation_id,
                state: payload.state,
                request: payload.request,
                progress: payload.progress || "",
                is_unseen: payload.kind !== "progress",
            };
            if (known) {
                Object.assign(known, entry);
            } else {
                state.news.unshift(entry);
            }
            const isShown =
                state.isOpen &&
                state.view === "chat" &&
                state.conversationId === payload.conversation_id;
            if (isShown) {
                const answer = state.messages.findLast((message) => message.runId === payload.run_id);
                if (payload.kind === "progress") {
                    if (answer) {
                        answer.progress = payload.progress || "";
                    }
                    return;
                }
                // What Nudge wrote and asked is read again from the server
                openConversation(payload.conversation_id);
                return;
            }
            if (payload.kind === "progress") {
                return;
            }
            const sticky = payload.kind === "waiting";
            notification.add(payload.text || payload.request, {
                title: outcomeOf(payload),
                type: payload.kind === "failed" ? "warning" : "info",
                sticky,
                buttons: [
                    {
                        name: _t("Show me"),
                        primary: true,
                        onClick: () => show(payload.conversation_id),
                    },
                ],
            });
        }

        /**
         * Stop what is worked on in the background. What was done stays done.
         */
        async function stopRun(runId) {
            try {
                await orm.call("nudge.run", "action_stop", [[runId]]);
            } catch {
                // Over already, or not theirs: read again says which
            }
            const news = state.news.find((entry) => entry.run_id === runId);
            // What is known of the run first, then the conversation, which shows it
            await loadNews();
            if (news && state.conversationId === news.conversation_id) {
                await openConversation(news.conversation_id);
            }
        }

        function readMessage(message, cards) {
            const events = message.screen_events || [];
            const shown = events.find((event) => event.type === "guidance");
            const products = events.find((event) => event.type === "products");
            const screens = events.find((event) => event.type === "found");
            const told = events
                .filter((event) =>
                    ["navigate", "write", "open", "filter", "open_action"].includes(event.type)
                )
                .map((event) => (event.type === "write" ? writtenText(event) : event.text));
            return {
                id: message.id,
                role: message.role,
                content: join(message.content, ...told),
                state: message.state,
                error: message.error_message || "",
                guidance: shown?.steps || [],
                products: products?.items || null,
                productQuery: products?.query || "",
                found: screens ? { menus: screens.menus || [] } : null,
                choice: "",
                spoken: Boolean(message.is_spoken),
                attachment: message.attachment_name || "",
                feedback: message.feedback || "",
                card: cards[message.id] || null,
                runId: null,
                progress: "",
            };
        }

        /**
         * What is still worked on in the background is shown at the end of
         * the conversation, with what Nudge is doing.
         */
        function workingMessage(conversationId) {
            const working = newsOf(conversationId).find((news) =>
                ["queued", "running"].includes(news.state)
            );
            if (!working) {
                return null;
            }
            return {
                id: `run_${working.run_id}`,
                role: "assistant",
                content: "",
                state: "queued",
                error: "",
                guidance: [],
                choice: "",
                explain: "",
                card: null,
                runId: working.run_id,
                progress: working.progress,
            };
        }

        async function openConversation(conversationId) {
            stop();
            const [conversations, messages, cards] = await Promise.all([
                orm.read("nudge.conversation", [conversationId], ["name"]),
                orm.searchRead(
                    "nudge.message",
                    [
                        ["conversation_id", "=", conversationId],
                        ["role", "in", ["user", "assistant"]],
                    ],
                    ["role", "content", "state", "error_message", "screen_events", "is_spoken",
                     "attachment_name", "feedback"],
                    { order: "id" }
                ),
                // What Nudge did, or is waiting to do, in this conversation
                state.canUseAgent
                    ? orm.call("nudge.step", "get_cards", [conversationId]).catch(() => ({}))
                    : {},
            ]);
            state.conversationId = conversationId;
            state.asking = null;
            state.title = conversations[0]?.name || "";
            state.messages = messages
                .map((message) => readMessage(message, cards || {}))
                // An answer made of calls to tools only has nothing to read
                .filter(
                    (message) =>
                        message.content || message.error || message.guidance.length || message.card ||
                        message.products?.length
                );
            const working = workingMessage(conversationId);
            if (working) {
                state.messages.push(working);
            }
            state.view = "chat";
            markSeen(conversationId);
        }

        /**
         * Point at the screen. The panel gets out of the way: it would hide
         * what is pointed at.
         */
        /**
         * Before a provider is set up: what the menus, the settings and the
         * documentation hold for the words typed. No AI is asked.
         *
         * @param {string} words
         * @returns {Promise<{ menus: Object[], settings: Object[], pages: Object[] }>}
         */
        /**
         * @param {string} text what was typed
         * @returns {string|null} the words after /product (or /منتج), null for any other message
         */
        function productCommand(text) {
            const match = /^\/(product|منتج)(?:\s+|$)/iu.exec(text.trim());
            return match ? text.trim().slice(match[0].length).trim() : null;
        }

        /**
         * @param {string} text what was typed
         * @returns {{ command: Object, word: string, rest: string }|null} the
         *   command the message starts with, among those of the person
         */
        function parseCommand(text) {
            const trimmed = (text || "").trim();
            const first = trimmed.split(/\s/, 1)[0].toLowerCase();
            if (!first.startsWith("/")) {
                return null;
            }
            const command = state.commands.find((entry) => entry.words.includes(first));
            return command ? { command, word: first, rest: trimmed.slice(first.length).trim() } : null;
        }

        /** The model of the screen in front: /rules without words is about it */
        function screenModel() {
            const current = action.currentController;
            return current?.props?.resModel || current?.action?.res_model || null;
        }

        /**
         * A command answered by the server without any AI: what it found is
         * drawn in the answer, with the pointer, the screen or the record a
         * click away.
         *
         * @param {string} text the whole message
         * @returns {Promise<Object>} the answer, as a message of the chat
         */
        async function answerCommand(text) {
            const answer = {
                id: `local_${++localId}`, role: "assistant", content: "", state: "complete",
                error: "", found: null, products: null,
            };
            try {
                const result = await orm.call("nudge.quick", "run_command", [text, screenModel()]);
                answer.products = result.products || null;
                answer.found = result;
                answer.content = result.content || "";
            } catch {
                answer.state = "error";
                answer.error = _t("The command could not be run.");
            }
            return answer;
        }

        async function runCommand(text, spoken = false) {
            state.view = "chat";
            state.messages.push({
                id: `local_${++localId}`, role: "user", content: text, state: "complete",
                error: "", screen: "", spoken, attachment: "",
            });
            state.messages.push({
                id: `local_${++localId}`, role: "assistant", content: "", state: "streaming", error: "",
            });
            const waiting = state.messages.at(-1);
            Object.assign(waiting, await answerCommand(text), { id: waiting.id });
        }

        /**
         * What was found by a command is used: the way shown, the screen or
         * the record opened, a command put in the message box.
         *
         * @param {"menu"|"open_menu"|"setting"|"record"|"command"} kind
         * @param {Object} item
         */
        function useFound(kind, item) {
            if (kind === "menu") {
                return quickGuide("menu", item.id);
            }
            if (kind === "open_menu") {
                close();
                return menu.selectMenu(item.id);
            }
            if (kind === "setting") {
                return quickGuide("setting", { app: item.app, field: item.field });
            }
            if (kind === "record") {
                return openRecord({ model: item.model, id: item.id });
            }
            if (kind === "command") {
                state.draft = `${item.words[0]} `;
                state.focusRequest++;
            }
        }

        function isPicture(attachment) {
            return Boolean(attachment?.file?.type?.startsWith("image/"));
        }

        /**
         * /product without a picture: the products most like the words,
         * found by the server without any AI, shown as cards.
         */
        async function findProducts(text, query, spoken = false) {
            state.view = "chat";
            state.messages.push({
                id: `local_${++localId}`, role: "user", content: text, state: "complete",
                error: "", screen: "", spoken, attachment: "",
            });
            state.messages.push({
                id: `local_${++localId}`, role: "assistant", content: "", state: "streaming",
                error: "", products: null, productQuery: query,
            });
            // The reactive copy: what is changed below is drawn
            const answer = state.messages.at(-1);
            if (!query) {
                answer.content = _t("Write what you look for after /product: a name, a description, a code or a size.");
                answer.state = "complete";
                return;
            }
            try {
                const found = await orm.call("nudge.quick", "find_products", [query]);
                answer.products = found.items || [];
                answer.content = answer.products.length
                    ? ""
                    : _t("No product looks like “%s”. Try another word, a code or a size.", query);
                answer.state = "complete";
            } catch {
                answer.state = "error";
                answer.error = _t("The products could not be searched.");
            }
        }

        function openProduct(productId) {
            return action.doAction({
                type: "ir.actions.act_window",
                res_model: "product.product",
                res_id: productId,
                views: [[false, "form"]],
                target: "current",
            });
        }

        async function quickFind(words) {
            try {
                return await orm.call("nudge.quick", "find", [words]);
            } catch {
                return { menus: [], settings: [], pages: [], products: [] };
            }
        }

        /**
         * The pointer shows the way to a menu or a setting found by quickFind.
         *
         * @param {"menu"|"setting"} kind
         * @param {number|{ app: string, field: string }} ref
         * @returns {Promise<boolean>} whether there is a way to show
         */
        async function quickGuide(kind, ref) {
            let steps = [];
            try {
                steps = await orm.call("nudge.quick", "guide", [kind, ref]);
            } catch {
                steps = [];
            }
            showGuidance(steps);
            return Boolean(steps?.length);
        }

        function showGuidance(steps) {
            if (steps?.length) {
                close();
                guidance.begin(steps);
            }
        }

        /**
         * How the person wants the steps of an answer: in writing here
         * ("text"), or shown on the screen ("screen").
         */
        function chooseGuidance(messageId, how) {
            const message = state.messages.find((entry) => entry.id === messageId);
            if (!message) {
                return;
            }
            message.choice = how;
            if (how === "screen") {
                showGuidance(message.guidance);
            }
        }

        /**
         * Type in the form what the answer wrote for it, and point at it:
         * the person reads it where it is, and takes it back if they wish.
         */
        async function write(answer, event) {
            // Said at once: the end of the answer may come before the writing
            // is done, and an answer that writes is never an empty bubble
            answer.wrote = true;
            const result = await writeInField(context.frontView(), event);
            if (result.error) {
                answer.content = join(
                    answer.content,
                    _t("I could not write in “%s”. Here is the text:", event.label || event.field),
                    quote(event.text)
                );
                return;
            }
            answer.content = join(answer.content, writtenText(event));
            showGuidance([
                {
                    target: { type: "field", name: event.field },
                    text: _t("I wrote here what you asked. Read it, then save."),
                    advance: "manual",
                    undo: result.undo,
                },
            ]);
        }

        // --------------------------------------------------------------
        // Agent
        // --------------------------------------------------------------

        /**
         * Records were created or changed on the server: what the screen
         * shows of them is read again. A button may have opened a window:
         * it is opened here, as after a click. What Nudge built for the
         * screens (a view, a button, a field, a report) redraws the screen in
         * front.
         */
        async function onChanged(event) {
            try {
                if (event.action) {
                    const isReload = event.action.type === "ir.actions.client" && event.action.tag === "reload";
                    if (isReload && event.model !== SETTINGS_MODEL && (await redrawScreen())) {
                        return;
                    }
                    if (isReload) {
                        forgetScreens();
                        keepAcrossReload();
                    }
                    await action.doAction(event.action);
                    return;
                }
                await refreshScreen(event);
            } catch {
                // The work is done: a screen that cannot be refreshed is refreshed by hand
            }
        }

        /**
         * The list or the kanban in front of the kind of the records is read
         * again; the form of one of them too, unless what the person typed in
         * it is not saved.
         * @param {{ model: string, ids?: number[] }} changed
         */
        async function refreshScreen(changed) {
            const view = context.frontView();
            const model = view?.controller.model;
            if (!model || view.controller.props.resModel !== changed.model) {
                return;
            }
            if (view.type !== "form") {
                await model.load();
            } else if ((changed.ids || []).includes(model.root.resId) && !model.root.dirty) {
                // What the person typed and did not save is theirs
                await model.load();
            }
        }

        /**
         * The browser keeps the screens it has shown (their views, their
         * menus of actions and of reports), on disk: what Nudge built in them
         * would not be seen, even after a reload of the page.
         */
        function forgetScreens() {
            env.bus.trigger("CLEAR-CACHES");
        }

        /**
         * The screen in front is drawn again, from screens read anew.
         * @returns {Promise<boolean>} whether there was one
         */
        async function redrawScreen() {
            if (!action.currentController) {
                return false;
            }
            forgetScreens();
            await action.doAction({ type: "ir.actions.client", tag: "soft_reload" });
            return true;
        }

        async function openRecord(event) {
            try {
                await action.doAction({
                    type: "ir.actions.act_window",
                    res_model: event.model,
                    res_id: event.id,
                    views: [[false, "form"]],
                    target: "current",
                });
            } catch {
                // Told in the answer all the same
            }
        }

        /**
         * The list in front is of the same kind: the conditions join its
         * filters, where the person sees and removes them. Otherwise the
         * filtered list is opened.
         */
        async function applyFilter(event) {
            const view = context.frontView();
            const searchModel = view?.controller.env.searchModel;
            if (
                searchModel?.resModel === event.model &&
                view.type !== "form" &&
                !view.controller.env.inDialog
            ) {
                try {
                    if (event.domain?.length) {
                        await searchModel.splitAndAddDomain(event.domain);
                    }
                    for (const groupBy of event.group_by || []) {
                        const [fieldName, interval] = groupBy.split(":");
                        searchModel.createNewGroupBy(fieldName, interval ? { interval } : {});
                    }
                    return;
                } catch {
                    // The list is opened instead
                }
            }
            try {
                await action.doAction({
                    type: "ir.actions.act_window",
                    name: event.title,
                    res_model: event.model,
                    domain: event.domain,
                    context: event.group_by?.length ? { group_by: event.group_by } : {},
                    views: [
                        [false, "list"],
                        [false, "form"],
                    ],
                    target: "current",
                });
            } catch {
                // Told in the answer all the same
            }
        }

        /**
         * The person says whether an answer helped, and why not.
         * @param {number} messageId a message the server gave an id
         * @param {"up"|"down"} value
         * @param {string} [note]
         */
        async function rate(messageId, value, note = "") {
            if (typeof messageId !== "number" || !["up", "down"].includes(value)) {
                return;
            }
            await orm.call("nudge.message", "rate", [[messageId], value, note || ""]);
            const message = state.messages.find((candidate) => candidate.id === messageId);
            if (message) {
                message.feedback = value;
            }
        }

        function reloadPage() {
            browser.location.reload();
        }

        function findCard(stepId) {
            return state.messages.find((message) => message.card?.step_id === stepId)?.card;
        }

        function applyEvent(answer, event) {
            switch (event.type) {
                case "start":
                    if (event.version && config?.version && event.version !== config.version) {
                        state.isOutdated = true;
                    }
                    state.conversationId = event.conversation_id;
                    state.title = event.title || state.title;
                    break;
                case "delta":
                    answer.content += event.text;
                    break;
                case "guidance":
                    answer.guidance = event.steps;
                    if (answer.explain === EXPLAIN_SCREEN) {
                        // They asked for the screen: the pointer sets off
                        answer.choice = "screen";
                        showGuidance(event.steps);
                    } else if (answer.explain === EXPLAIN_TEXT) {
                        answer.choice = "text";
                    } else {
                        // Theirs to choose: the steps in writing here, or shown
                        answer.choice = "pending";
                    }
                    break;
                case "navigate":
                    answer.content = join(answer.content, event.text);
                    menu.selectMenu(event.menu_id);
                    break;
                case "write":
                    return write(answer, event);
                case "open":
                    answer.content = join(answer.content, event.text);
                    return openRecord(event);
                case "open_action":
                    answer.content = join(answer.content, event.text);
                    return action.doAction(event.action).catch(() => {});
                case "products":
                    answer.products = event.items || [];
                    answer.productQuery = event.query || "";
                    break;
                case "found":
                    // Screens Nudge found itself: the pointer to each, or Open
                    answer.found = { menus: event.menus || [] };
                    break;
                case "filter":
                    answer.content = join(answer.content, event.text);
                    return applyFilter(event);
                case "confirm": {
                    const { type, ...card } = event;
                    answer.card = card;
                    break;
                }
                case "decided": {
                    const card = findCard(event.step_id);
                    if (card) {
                        // A plan says how far it went, and whether it can be taken back
                        const { type, ...decided } = event;
                        Object.assign(card, decided, { error: event.error || "" });
                    }
                    break;
                }
                case "changed":
                    return onChanged(event);
                case "waiting":
                    answer.id = event.message_id;
                    answer.state = "waiting";
                    break;
                case "queued": {
                    // Left to the background: what comes next comes through the bus
                    answer.state = "queued";
                    answer.runId = event.run_id;
                    const known = state.news.find((news) => news.run_id === event.run_id);
                    if (known) {
                        Object.assign(known, { state: "queued", progress: "", is_unseen: false });
                    } else {
                        state.news.unshift({
                            run_id: event.run_id,
                            conversation_id: state.conversationId,
                            state: "queued",
                            request: state.messages.findLast((message) => message.role === "user")?.content || "",
                            progress: "",
                            is_unseen: false,
                        });
                    }
                    break;
                }
                case "done":
                    answer.id = event.message_id;
                    answer.state = event.state;
                    answer.error = event.message || "";
                    if (!answer.content && !answer.error && !answer.guidance?.length && !answer.card &&
                        !answer.products?.length && !answer.wrote) {
                        // Never an empty bubble: say that nothing came
                        answer.error = state.isOutdated
                            ? _t("Nudge was updated. Reload the page, then ask again.")
                            : _t("No answer came. Ask again, in other words.");
                    }
                    break;
                case "error":
                    answer.id = event.message_id || answer.id;
                    answer.state = "error";
                    answer.error =
                        [event.message, event.detail].filter(Boolean).join("\n") ||
                        _t("Something went wrong while answering.");
                    break;
            }
        }

        function addAnswer() {
            state.messages.push({
                id: `local_${++localId}`,
                role: "assistant",
                content: "",
                state: "streaming",
                error: "",
                guidance: [],
                choice: "",
                explain: "",
                card: null,
                runId: null,
                progress: "",
            });
            // Read it back: changes must go through the reactive object
            return state.messages.at(-1);
        }

        /**
         * @param {Object} answer the message the stream is written in
         * @param {(params: Object) => Promise} request what is asked of the server
         */
        async function receive(answer, request) {
            state.isStreaming = true;
            abortController = new AbortController();
            const pending = [];
            try {
                await request({
                    signal: abortController.signal,
                    onEvent: (event) => pending.push(applyEvent(answer, event)),
                });
                await Promise.all(pending);
                if (answer.state === "streaming") {
                    answer.state = "error";
                    answer.error = _t("The connection was lost before the answer was complete.");
                } else if (answer.state === "complete" || answer.state === "waiting") {
                    voice.speak(answer.content);
                }
            } catch (error) {
                if (error.name === "AbortError") {
                    answer.state = "aborted";
                } else {
                    answer.state = "error";
                    answer.error = _t("Nudge could not be reached. Check your connection and try again.");
                }
            } finally {
                state.isStreaming = false;
                abortController = null;
            }
        }

        /**
         * @param {string} text
         * @param {Object} [options]
         * @param {boolean} [options.spoken] the message was said, not typed
         * @param {boolean} [options.background] the work is done while the
         *   person does something else; in Agent mode only
         */
        /**
         * A file to send with the next question, or null to drop it.
         * @returns {string} what is wrong with it, or ""
         */
        function setAttachment(file) {
            if (!file) {
                state.attachment = null;
                return "";
            }
            if (file.size > MAX_ATTACHMENT_BYTES) {
                return _t("The file is too large. The limit is %s MB.", MAX_ATTACHMENT_BYTES / 1048576);
            }
            if (!isReadable(file)) {
                return _t("Only PDF, text, CSV and image files can be read.");
            }
            state.attachment = { file, name: file.name, size: file.size };
            return "";
        }

        async function send(
            text,
            { spoken = false, background = false, explain = null, report = null, file = null } = {}
        ) {
            text = (text || "").trim();
            const attachment = file ? { file, name: file.name, size: file.size } : state.attachment;
            if (!text && attachment) {
                text = attachment.name;
            }
            if (!text || state.isStreaming) {
                return;
            }
            state.attachment = null;
            voice.stopSpeaking();
            // A question still waiting for its how is replaced by this one
            cancelExplain({ keep: false });
            const productQuery = productCommand(text);
            if (productQuery !== null && !isPicture(attachment)) {
                // Found at once, without any AI: the words are enough
                return findProducts(text, productQuery, spoken);
            }
            const typed = parseCommand(text);
            if (typed?.command.kind === "direct" && !attachment) {
                // Answered by the server at once, without any AI
                return runCommand(text, spoken);
            }
            if (typed?.command.kind === "focused") {
                // One precise thing: in writing, with the tools of the command only
                explain = EXPLAIN_TEXT;
                report = "chat";
            }
            if (!typed && state.canUseAgent && state.mode === MODE_TALK && asksToDoIt(text)) {
                setMode(MODE_AGENT);
            }
            // What waited for a yes or a no is left: the person moved on
            for (const message of state.messages) {
                if (message.card?.state === "pending") {
                    message.card.state = "cancelled";
                    message.state = "complete";
                }
            }
            // The screen as it is when the question is asked
            const snapshot = lookAtScreen();
            state.view = "chat";
            // In Developer mode a report is something to build ("a PDF report of
            // the quotation"), not figures to show: the question goes as it is
            if (report === null && state.canMakeSpreadsheet && state.mode !== MODE_DEV && asksForReport(text)) {
                // A comparison or a report: theirs to say, in the chat or as a spreadsheet
                const question = {
                    id: `local_${++localId}`,
                    role: "user",
                    content: text,
                    state: "choosing",
                    error: "",
                    screen: snapshot ? state.screenSummary : "",
                    spoken,
                    attachment: attachment?.name || "",
                };
                state.messages.push(question);
                state.asking = {
                    kind: "report", messageId: question.id, text, spoken, background, snapshot,
                    file: attachment?.file,
                };
                return;
            }
            const question = {
                id: `local_${++localId}`,
                role: "user",
                content: text,
                state: "complete",
                error: "",
                screen: snapshot ? state.screenSummary : "",
                spoken,
                attachment: attachment?.name || "",
            };
            state.messages.push(question);
            if (report || productQuery !== null) {
                // A report is not a how-to, nor a product search: nothing to show with the pointer
                explain = null;
            } else if (!isSharing()) {
                // Nothing to point at without the screen: in writing, or the work
                explain = state.mode !== MODE_TALK ? null : EXPLAIN_TEXT;
            } else if (
                explain === EXPLAIN_DO ||
                (state.mode !== MODE_TALK && (asksToDoIt(text) || !asksHowTo(text)))
            ) {
                // An order is the work, not a question of how
                explain = null;
            } else if (!EXPLAIN_CHOICES.includes(explain)) {
                // In Agent mode a preference does not turn an order into an
                // explanation: either they are asked, or it is the work
                explain =
                    state.mode !== MODE_TALK && state.explain !== EXPLAIN_ASK ? null : state.explain;
            }
            if (explain === EXPLAIN_ASK) {
                // Theirs to say first: on the screen, or in writing
                state.messages.at(-1).state = "choosing";
                state.asking = {
                    messageId: question.id, text, spoken, background, snapshot, file: attachment?.file,
                };
                return;
            }
            await dispatch(text, { spoken, background, snapshot, explain, file: attachment?.file, report });
        }

        /**
         * Send a question to Nudge and show its answer as it comes.
         */
        async function dispatch(
            text,
            { spoken, background, snapshot, explain, file = null, report = null }
        ) {
            const answer = addAnswer();
            answer.explain = explain || "";
            await receive(answer, (params) =>
                streamChat({
                    message: text,
                    conversationId: state.conversationId,
                    snapshot,
                    spoken,
                    readAloud: voice.state.isAllowed && voice.state.speakAnswers,
                    mode: state.mode,
                    background: background && state.mode !== MODE_TALK,
                    explain: explain || null,
                    file,
                    report,
                    ...params,
                })
            );
        }

        /**
         * The person says how they want the answer to the question that
         * waits: on the screen, in writing, or, in Agent mode, done for them.
         *
         * @param {"screen"|"text"|"do"} how
         */
        async function chooseExplain(how) {
            const asking = state.asking;
            if (!asking || ![...EXPLAIN_CHOICES, EXPLAIN_DO].includes(how) || state.isStreaming) {
                return;
            }
            state.asking = null;
            const question = state.messages.find((message) => message.id === asking.messageId);
            if (question) {
                question.state = "complete";
            }
            await dispatch(asking.text, {
                spoken: asking.spoken,
                background: asking.background,
                snapshot: asking.snapshot,
                file: asking.file,
                explain: how === EXPLAIN_DO ? null : how,
            });
        }

        /**
         * The question that waited is taken back, into the message box
         * unless told otherwise.
         */
        /**
         * The person says how they want the comparison or the report that
         * waits: in the chat, or as a spreadsheet, which the agent makes.
         *
         * @param {"chat"|"sheet"} how
         */
        async function chooseReport(how) {
            const asking = state.asking;
            if (!asking || asking.kind !== "report" || !["chat", "sheet"].includes(how) || state.isStreaming) {
                return;
            }
            state.asking = null;
            state.messages = state.messages.filter((message) => message.id !== asking.messageId);
            if (how === "sheet" && state.mode === MODE_TALK) {
                // The figures are read and the spreadsheet made by the agent
                setMode(MODE_AGENT);
            }
            await send(asking.text, {
                spoken: asking.spoken,
                background: asking.background,
                report: how,
                file: asking.file || null,
            });
        }

        function cancelExplain({ keep = true } = {}) {
            const asking = state.asking;
            if (!asking) {
                return;
            }
            state.asking = null;
            state.messages = state.messages.filter((message) => message.id !== asking.messageId);
            if (keep) {
                state.draft = state.draft || asking.text;
                state.focusRequest++;
            }
        }

        /**
         * Say yes or no to what Nudge is about to do.
         *
         * @param {number} stepId
         * @param {"confirm"|"decline"} decision
         */
        /**
         * What a change wrote is written back, as it was, unless someone
         * changed the record since.
         */
        async function undoStep(stepId) {
            const card = findCard(stepId);
            if (!card?.can_undo) {
                return;
            }
            card.can_undo = false;
            try {
                const result = await orm.call("nudge.step", "undo_changes", [stepId]);
                Object.assign(card, result.card);
                notification.add(result.message, { type: "success" });
                await refreshAll(result.changed);
            } catch (error) {
                card.can_undo = true;
                throw error;
            }
        }

        async function decide(stepId, decision) {
            const card = findCard(stepId);
            if (!card || card.state !== "pending" || state.isStreaming) {
                return;
            }
            card.state = "deciding";
            const holder = state.messages.find((message) => message.card === card);
            const answer = addAnswer();
            await receive(answer, (params) =>
                streamDecision({
                    conversationId: state.conversationId,
                    stepId,
                    decision,
                    ...params,
                })
            );
            if (card.state === "deciding") {
                // Nothing was decided: refused, or the connection was lost
                card.state = "pending";
            } else if (holder) {
                holder.state = "complete";
            }
        }

        /**
         * What Nudge made reloads the page (a new field, a changed screen): the
         * conversation is kept for the page that comes, a short while only.
         */
        function keepAcrossReload() {
            if (!state.conversationId) {
                return;
            }
            try {
                browser.sessionStorage.setItem(
                    KEPT_KEY,
                    JSON.stringify({ conversationId: state.conversationId, isOpen: state.isOpen, at: Date.now() })
                );
            } catch {
                // Without storage the page comes back without the conversation
            }
        }

        /** @returns {{conversationId: number, isOpen: boolean}|null} what was kept, taken once */
        function takeKept() {
            try {
                const kept = JSON.parse(browser.sessionStorage.getItem(KEPT_KEY) || "null");
                browser.sessionStorage.removeItem(KEPT_KEY);
                return kept && Date.now() - kept.at < KEPT_MS ? kept : null;
            } catch {
                return null;
            }
        }

        if (state.isAvailable) {
            // Mounted for whoever may use Nudge: switched off, it shows nothing
            registry.category("main_components").add("nudge.Root", { Component: NudgeRoot });
            const kept = takeKept();
            if (kept) {
                // Back after the reload what Nudge made asked for: where it was
                openConversation(kept.conversationId)
                    .then(() => kept.isOpen && open())
                    .catch(() => {});
            }
            registerHotkeys();
            if (state.canUseAgent) {
                bus.subscribe("nudge.run", onNews);
                bus.start();
                loadNews();
            }
            // The screen changed under an open panel: tell what is seen now
            env.bus.addEventListener("ACTION_MANAGER:UI-UPDATED", () => {
                if (state.isOpen) {
                    lookAtScreen();
                }
            });
        }

        return {
            state,
            open,
            close,
            toggle,
            setEnabled,
            findProducts,
            openProduct,
            parseCommand,
            answerCommand,
            useFound,
            send,
            decide,
            undoStep,
            stop,
            newConversation,
            openConversation,
            showHistory,
            showChat,
            showSettings,
            lookAtScreen,
            setShareScreen,
            setMode,
            setTheme,
            setPosition,
            setHotkey,
            setDictationHotkey,
            showGuidance,
            quickFind,
            quickGuide,
            chooseGuidance,
            setExplain,
            chooseExplain,
            chooseReport,
            cancelExplain,
            talk,
            toggleTalk,
            dictate,
            stopRun,
            reloadPage,
            setAttachment,
            rate,
            show,
            newsOf,
            unseenCount,
        };
    },
};

registry.category("services").add("nudge", nudgeService);
