import { registerTarget } from "@nudge/guidance/target_resolver";

/**
 * Where things are in Studio's editor, by name. The names are what Studio
 * calls them in its classes; the texts of its top menu are matched as
 * written, in the language of the interface or in English.
 */
const TABS = { new: "o_web_studio_new", view: "o_web_studio_view", properties: "o_web_studio_properties" };
const MENU_TEXTS = {
    views: ["Views"],
    reports: ["Reports"],
    automations: ["Automations"],
    actions: ["Actions"],
    webhooks: ["Webhooks"],
    access_control: ["Access Control"],
    model_pages: ["Model Pages"],
    filter_rules: ["Filter Rules"],
};
const CONTROLS = {
    add_button: ".o-web-studio-editor--add-button-action",
    leave: ".o_web_studio_leave",
    undo: ".o_web_studio_undo",
    redo: ".o_web_studio_redo",
    new_model: ".o_web_studio_menu .o_web_studio_new_model, .o_studio_navbar .o_web_studio_new_model",
    edit_menu: ".o_web_studio_menu .o_web_studio_edit_menu, .o_studio_navbar .o_web_studio_edit_menu",
};

function isShown(element) {
    return Boolean(element && element.isConnected && element.getClientRects().length);
}

function firstShown(selector) {
    for (const element of document.querySelectorAll(selector)) {
        if (isShown(element)) {
            return element;
        }
    }
    return null;
}

function byText(root, texts) {
    const wanted = texts.map((text) => text.toLowerCase());
    for (const element of root.querySelectorAll("a, button, span, li")) {
        const text = element.textContent.trim().toLowerCase();
        if (wanted.includes(text) && isShown(element)) {
            return element.closest("a, button") || element;
        }
    }
    return null;
}

registerTarget("studio_tab", (name) => {
    const cls = TABS[name];
    const element = cls && firstShown(`.o_web_studio_sidebar .${cls}`);
    return element ? { element } : null;
});

registerTarget("studio_component", (name) => {
    if (!/^[a-z0-9_]+$/.test(name)) {
        return null;
    }
    const element = firstShown(`.o_web_studio_sidebar .o_web_studio_field_${name}`);
    if (element) {
        return { element };
    }
    // The components are on the Add tab: it is opened first
    const tab = firstShown(`.o_web_studio_sidebar .${TABS.new}`);
    return tab ? { element: tab, isDetour: true, text: "Open the Add tab first." } : null;
});

registerTarget("studio_menu", (name) => {
    const texts = MENU_TEXTS[name];
    const menu = firstShown(".o_web_studio_menu, .o_studio_navbar");
    const element = texts && menu && byText(menu, texts);
    return element ? { element } : null;
});

registerTarget("studio_control", (name) => {
    const selector = CONTROLS[name];
    const element = selector && firstShown(selector);
    return element ? { element } : null;
});
