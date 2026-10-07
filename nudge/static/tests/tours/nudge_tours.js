import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("nudge_chat", {
    url: "/odoo",
    steps: () => [
        {
            content: "Open Nudge",
            trigger: ".o_nudge_avatar",
            run: "click",
        },
        {
            content: "Nudge says hello",
            trigger: ".o_nudge_panel .o_nudge_empty_title:contains(I'm Nudge)",
        },
        {
            content: "Ask a suggested question",
            trigger: ".o_nudge_suggestion:contains(How do I create a quotation?)",
            run: "click",
        },
        {
            content: "Asked how first: in writing",
            trigger: ".o_nudge_ask .o_nudge_explain_text",
            run: "click",
        },
        {
            content: "The question is shown",
            trigger: ".o_nudge_message_user:contains(How do I create a quotation?)",
        },
        {
            content: "The answer is complete and formatted",
            trigger: ".o_nudge_message_assistant[data-state=complete] .o_nudge_markdown strong:contains(Sales)",
        },
        {
            trigger: ".o_nudge_message_assistant .o_nudge_markdown code:contains(New)",
        },
        {
            content: "The title of the conversation is its first question",
            trigger: ".o_nudge_title:contains(How do I create a quotation?)",
        },
        {
            content: "Write a follow-up",
            trigger: ".o_nudge_input",
            run: "edit Thanks!",
        },
        {
            trigger: ".o_nudge_send:enabled",
            run: "click",
        },
        {
            content: "Asked how first: in writing",
            trigger: ".o_nudge_ask .o_nudge_explain_text",
            run: "click",
        },
        {
            content: "Both answers are there",
            trigger: ".o_nudge_body:has(.o_nudge_message_assistant[data-state=complete]:eq(1))",
        },
        {
            content: "Open the history",
            trigger: ".o_nudge_history",
            run: "click",
        },
        {
            trigger: ".o_nudge_history_item:contains(How do I create a quotation?)",
            run: "click",
        },
        {
            content: "The conversation is loaded from the server",
            trigger: ".o_nudge_body:has(.o_nudge_message:eq(3)):not(:has(.o_nudge_message:eq(4)))",
        },
        {
            content: "Start over",
            trigger: ".o_nudge_new",
            run: "click",
        },
        {
            trigger: ".o_nudge_empty_title",
        },
        {
            content: "Close Nudge",
            trigger: ".o_nudge_close",
            run: "click",
        },
        {
            trigger: "body:not(:has(.o_nudge_panel)) .o_nudge_avatar",
        },
    ],
});

registry.category("web_tour.tours").add("nudge_hidden", {
    url: "/odoo",
    steps: () => [
        {
            content: "The web client is there, Nudge is not",
            trigger: "body:has(.o_main_navbar):not(:has(.o_nudge_avatar)):not(:has(.o_nudge_systray))",
        },
    ],
});

registry.category("web_tour.tours").add("nudge_screen", {
    steps: () => [
        {
            content: "A contact is open",
            trigger: ".o_form_view .o_field_widget[name=email] input",
        },
        {
            content: "Call Nudge with the shortcut, from inside a field",
            trigger: ".o_form_view .o_field_widget[name=email] input",
            run: "click",
        },
        {
            trigger: ".o_form_view .o_field_widget[name=email] input:focus",
            run: "press ctrl+shift+space",
        },
        {
            content: "Nudge tells what it is looking at",
            trigger: ".o_nudge_panel .o_nudge_screen_text:contains(Nudge Test Partner)",
        },
        {
            content: "The message box has the focus",
            trigger: ".o_nudge_input:focus",
        },
        {
            content: "Suggestions are about the screen",
            trigger: ".o_nudge_suggestion:contains(What am I looking at?)",
            run: "click",
        },
        {
            content: "Asked how first: in writing",
            trigger: ".o_nudge_ask .o_nudge_explain_text",
            run: "click",
        },
        {
            trigger: ".o_nudge_message_user .o_nudge_seen:contains(Nudge Test Partner)",
        },
        {
            trigger: ".o_nudge_message_assistant[data-state=complete] strong:contains(Nudge Test Partner)",
        },
        {
            content: "Type in the form without saving",
            trigger: ".o_form_view .o_field_widget[name=email] input",
            run: "edit changed@nudge.example",
        },
        {
            trigger: ".o_nudge_input",
            run: "edit And now?",
        },
        {
            trigger: ".o_nudge_send:enabled",
            run: "click",
        },
        {
            content: "Asked how first: in writing",
            trigger: ".o_nudge_ask .o_nudge_explain_text",
            run: "click",
        },
        {
            trigger: ".o_nudge_body:has(.o_nudge_message_assistant[data-state=complete]:eq(1))",
        },
        {
            content: "Close the eye",
            trigger: ".o_nudge_eye[aria-pressed=true]",
            run: "click",
        },
        {
            trigger: ".o_nudge_screen.o_nudge_screen_off .o_nudge_screen_text:contains(Screen not shared)",
        },
        {
            trigger: ".o_nudge_input",
            run: "edit Without my screen",
        },
        {
            trigger: ".o_nudge_send:enabled",
            run: "click",
        },
        {
            trigger: ".o_nudge_body:has(.o_nudge_message_assistant[data-state=complete]:eq(2))",
        },
        {
            content: "The last question carries no screen",
            trigger: ".o_nudge_message_user:eq(2):not(:has(.o_nudge_seen))",
        },
        {
            content: "Preferences offer the shortcuts",
            trigger: ".o_nudge_preferences",
            run: "click",
        },
        {
            trigger: ".o_nudge_hotkey:has(option[value='alt+control+n'])",
        },
        {
            trigger: ".o_nudge_close",
            run: "click",
        },
        {
            content: "Leave the form as it was",
            trigger: ".o_form_button_cancel",
            run: "click",
        },
        {
            trigger: ".o_form_view .o_field_widget[name=email] input:value(partner@nudge.example)",
        },
    ],
});

registry.category("web_tour.tours").add("nudge_guidance", {
    steps: () => [
        {
            trigger: ".o_form_view .o_field_widget[name=email] input",
        },
        {
            trigger: ".o_nudge_avatar",
            run: "click",
        },
        {
            trigger: ".o_nudge_input",
            run: "edit How do I change the email?",
        },
        {
            trigger: ".o_nudge_send:enabled",
            run: "click",
        },
        {
            content: "Asked how first: on the screen",
            trigger: ".o_nudge_ask .o_nudge_explain_screen",
            run: "click",
        },
        {
            content: "The panel gets out of the way and the field is pointed at",
            trigger: "body:not(:has(.o_nudge_panel)) .o_nudge_ring",
        },
        {
            trigger: ".o_nudge_guide_bubble .o_nudge_guide_text:contains(Type the new address here.)",
        },
        {
            trigger: ".o_nudge_guide_count:contains(1 / 2)",
        },
        {
            content: "Doing the step brings the next one",
            trigger: ".o_form_view .o_field_widget[name=email] input",
            run: "edit guided@nudge.example",
        },
        {
            trigger: ".o_nudge_guide_bubble .o_nudge_guide_text:contains(Then save.)",
        },
        {
            trigger: ".o_nudge_guide_next:contains(Done)",
        },
        {
            content: "The last step is a control of the view",
            trigger: ".o_form_button_save",
            run: "click",
        },
        {
            trigger: "body:not(:has(.o_nudge_ring)):not(:has(.o_nudge_guide_bubble))",
        },
        {
            content: "The steps stay in the conversation",
            trigger: ".o_nudge_avatar",
            run: "click",
        },
        {
            trigger: ".o_nudge_message_assistant .o_nudge_steps li:contains(Then save.)",
        },
        {
            trigger: ".o_nudge_show_again",
            run: "click",
        },
        {
            trigger: ".o_nudge_guide_bubble .o_nudge_guide_text:contains(Type the new address here.)",
        },
        {
            trigger: ".o_nudge_guide_stop",
            run: "click",
        },
        {
            trigger: "body:not(:has(.o_nudge_ring))",
        },
    ],
});

/**
 * The browser of the tests has no microphone, and nobody to talk in it: what
 * would be heard is said here.
 */
function hear(...sentences) {
    window.SpeechRecognition = class {
        start() {
            this.timer = setTimeout(() => {
                for (const transcript of sentences) {
                    const result = Object.assign([{ transcript }], { isFinal: true });
                    this.onresult?.({ resultIndex: 0, results: [result] });
                }
                if (!this.continuous) {
                    this.onend?.();
                }
            }, 50);
        }
        stop() {
            clearTimeout(this.timer);
            this.onend?.();
        }
        abort() {
            clearTimeout(this.timer);
            this.onend?.();
        }
    };
}

registry.category("web_tour.tours").add("nudge_voice", {
    steps: () => [
        {
            trigger: ".o_form_view .o_field_widget[name=email] input",
            run() {
                hear("How do I change the email?");
            },
        },
        {
            trigger: ".o_nudge_avatar",
            run: "click",
        },
        {
            content: "The language spoken is the one of the interface",
            trigger: ".o_nudge_language:contains(EN)",
        },
        {
            trigger: ".o_nudge_mic",
            run: "click",
        },
        {
            content: "What was said is sent, and told as said",
            trigger: ".o_nudge_message_user:contains(How do I change the email?) .o_nudge_said",
        },
        {
            content: "Asked how first: in writing",
            trigger: ".o_nudge_ask .o_nudge_explain_text",
            run: "click",
        },
        {
            trigger: ".o_nudge_message_assistant[data-state=complete] strong:contains(Save)",
        },
        {
            content: "The microphone is closed",
            trigger: ".o_nudge_mic:not(.o_nudge_mic_on)",
        },
        {
            content: "Preferences of the voice",
            trigger: ".o_nudge_preferences",
            run: "click",
        },
        {
            trigger: ".o_nudge_dictation_hotkey:has(option[value='control+shift+d'])",
        },
        {
            trigger: ".o_nudge_voice_language:has(option[value='ar'])",
        },
        {
            trigger: ".o_nudge_speak:not(:checked)",
        },
        {
            trigger: ".o_nudge_close",
            run: "click",
        },
    ],
});

registry.category("web_tour.tours").add("nudge_dictation", {
    steps: () => [
        {
            trigger: ".o_form_view .o_field_widget[name=email] input",
            run() {
                hear("voice@nudge.example");
            },
        },
        {
            trigger: ".o_form_view .o_field_widget[name=email] input",
            run: "click",
        },
        {
            trigger: ".o_form_view .o_field_widget[name=email] input:focus",
            run: "press alt+ctrl+d",
        },
        {
            content: "Nudge listens next to the field",
            trigger: ".o_nudge_dictation",
        },
        {
            content: "What is said is typed in the field",
            trigger: ".o_form_view .o_field_widget[name=email] input:value(voice@nudge.example)",
        },
        {
            content: "The cursor did not leave the field",
            trigger: ".o_form_view .o_field_widget[name=email] input:focus",
            run: "press alt+ctrl+d",
        },
        {
            trigger: "body:not(:has(.o_nudge_dictation)) .o_form_view .o_form_dirty",
        },
        {
            content: "Nudge did not open: a dictation is not a question",
            trigger: "body:not(:has(.o_nudge_panel)) .o_form_button_save",
            run: "click",
        },
        {
            trigger: ".o_form_view .o_form_saved",
        },
    ],
});

registry.category("web_tour.tours").add("nudge_write", {
    steps: () => [
        {
            trigger: ".o_form_view .o_field_widget[name=street] input",
        },
        {
            trigger: ".o_nudge_avatar",
            run: "click",
        },
        {
            trigger: ".o_nudge_input",
            run: "edit Write the address: 12 Main Street",
        },
        {
            trigger: ".o_nudge_send:enabled",
            run: "click",
        },
        {
            content: "Asked how first: in writing",
            trigger: ".o_nudge_ask .o_nudge_explain_text",
            run: "click",
        },
        {
            content: "The text is typed in the form, and pointed at",
            trigger: ".o_form_view .o_field_widget[name=street] input:value(12 Main Street)",
        },
        {
            trigger: "body:not(:has(.o_nudge_panel)) .o_nudge_ring",
        },
        {
            trigger: ".o_nudge_guide_text:contains(I wrote here what you asked)",
        },
        {
            content: "It can be taken back",
            trigger: ".o_nudge_guide_undo",
            run: "click",
        },
        {
            trigger: ".o_form_view .o_field_widget[name=street] input:value(Old Road)",
        },
        {
            trigger: "body:not(:has(.o_nudge_ring))",
        },
        {
            content: "Asked again, kept this time",
            trigger: ".o_nudge_avatar",
            run: "click",
        },
        {
            trigger: ".o_nudge_message_assistant:contains(Written in)",
        },
        {
            trigger: ".o_nudge_input",
            run: "edit Again please",
        },
        {
            trigger: ".o_nudge_send:enabled",
            run: "click",
        },
        {
            content: "Asked how first: in writing",
            trigger: ".o_nudge_ask .o_nudge_explain_text",
            run: "click",
        },
        {
            trigger: ".o_form_view .o_field_widget[name=street] input:value(12 Main Street)",
        },
        {
            trigger: ".o_nudge_guide_next:contains(Done)",
            run: "click",
        },
        {
            content: "Nothing is saved until the person saves",
            trigger: "body:has(.o_form_dirty) .o_form_button_save",
            run: "click",
        },
        {
            trigger: ".o_form_view .o_form_saved",
        },
    ],
});

registry.category("web_tour.tours").add("nudge_agent", {
    steps: () => [
        {
            trigger: ".o_form_view .o_field_widget[name=function] input:value(Buyer)",
        },
        {
            trigger: ".o_nudge_avatar",
            run: "click",
        },
        {
            content: "Nudge talks until the person asks it to act",
            trigger: ".o_nudge_mode_talk.o_nudge_mode_on",
        },
        {
            trigger: ".o_nudge_mode_agent",
            run: "click",
        },
        {
            trigger: ".o_nudge_mode_agent.o_nudge_mode_on",
        },
        {
            trigger: ".o_nudge_input",
            run: "edit Make this contact a Director",
        },
        {
            trigger: ".o_nudge_send:enabled",
            run: "click",
        },
        {
            content: "Asked how first: the work itself",
            trigger: ".o_nudge_ask .o_nudge_explain_do",
            run: "click",
        },
        {
            content: "What is about to change is shown, as it is and as it will be",
            trigger: ".o_nudge_card[data-state=pending] .o_nudge_card_title:contains(Change: Contact)",
        },
        {
            trigger: ".o_nudge_card_records:contains(Nudge Agent Partner)",
        },
        {
            trigger: ".o_nudge_card_before:contains(Buyer)",
        },
        {
            trigger: ".o_nudge_card_value:contains(Director)",
        },
        {
            content: "Nothing changed yet",
            trigger: ".o_form_view .o_field_widget[name=function] input:value(Buyer)",
        },
        {
            trigger: ".o_nudge_confirm",
            run: "click",
        },
        {
            trigger: ".o_nudge_card[data-state=done] .o_nudge_card_outcome:contains(Done)",
        },
        {
            content: "The form shows what was done",
            trigger: ".o_form_view .o_field_widget[name=function] input:value(Director)",
        },
        {
            trigger: ".o_nudge_message_assistant[data-state=complete]:contains(is now a Director)",
        },
        {
            content: "Asked again, and refused this time",
            trigger: ".o_nudge_input",
            run: "edit And now a President",
        },
        {
            trigger: ".o_nudge_send:enabled",
            run: "click",
        },
        {
            content: "Asked how first: the work itself",
            trigger: ".o_nudge_ask .o_nudge_explain_do",
            run: "click",
        },
        {
            trigger: ".o_nudge_card[data-state=pending] .o_nudge_card_value:contains(President)",
        },
        {
            trigger: ".o_nudge_card[data-state=pending] .o_nudge_decline",
            run: "click",
        },
        {
            trigger: ".o_nudge_card[data-state=declined] .o_nudge_card_outcome:contains(You said no)",
        },
        {
            trigger: ".o_form_view .o_field_widget[name=function] input:value(Director)",
        },
        {
            trigger: ".o_nudge_close",
            run: "click",
        },
    ],
});
