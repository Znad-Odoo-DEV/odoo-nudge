import { _t } from "@web/core/l10n/translation";

/**
 * Finds on the page the element a guidance step is about.
 *
 * A step names its target by kind and by name: "the field partner_id", "the
 * button action_confirm". The selector is built here, from a fixed pattern
 * per kind, with the name escaped. Nothing a model writes is ever used as a
 * selector.
 */

const CONTROLS = {
    new: ".o_form_button_create, .o_list_button_add, .o-kanban-button-new",
    save: ".o_form_button_save",
    discard: ".o_form_button_cancel",
    search: ".o_searchview_input",
    filters: ".o_searchview_dropdown_toggler",
    pager_next: ".o_pager_next",
    pager_previous: ".o_pager_previous",
    actions: ".o_cp_action_menus .dropdown-toggle",
};

export function isShown(element) {
    return Boolean(element && element.isConnected && element.getClientRects().length);
}

function firstShown(root, selector) {
    for (const element of root.querySelectorAll(selector)) {
        if (isShown(element) && !element.closest(".o_nudge")) {
            return element;
        }
    }
    return null;
}

function archNode(view, tag, name) {
    const xmlDoc = view?.controller.archInfo?.xmlDoc;
    if (!xmlDoc) {
        return null;
    }
    for (const node of xmlDoc.querySelectorAll(`${tag}[name="${CSS.escape(name)}"]`)) {
        if (!node.parentElement?.closest("field")) {
            return node;
        }
    }
    return null;
}

function tabLink(root, page) {
    const name = page.getAttribute("name");
    const label = page.getAttribute("string") || name || "";
    let link = name && firstShown(root, `.o_notebook_headers .nav-link[name="${CSS.escape(name)}"]`);
    if (!link) {
        link = [...root.querySelectorAll(".o_notebook_headers .nav-link")].find(
            (candidate) => isShown(candidate) && candidate.textContent.trim() === label
        );
    }
    return link ? { element: link, label } : null;
}

/**
 * The element is in a tab that is not open: the way to it is the tab.
 */
function throughTab(view, root, tag, name) {
    const page = archNode(view, tag, name)?.closest("page");
    const tab = page && tabLink(root, page);
    if (!tab || tab.element.classList.contains("active")) {
        return null;
    }
    return {
        element: tab.element,
        isDetour: true,
        text: _t("Open the tab “%s” first.", tab.label),
    };
}

/**
 * Buttons that do not fit are folded in a menu: the way to them is the menu.
 */
function throughMenu(root, selectors) {
    const toggler = firstShown(root, selectors);
    return toggler
        ? { element: toggler, isDetour: true, text: _t("Open this menu first.") }
        : null;
}

// Where the apps are opened from: the apps menu of the navbar, the button of
// the home menu, the sidebar on a small screen
const APPS_TOGGLES = ".o_navbar_apps_menu button, .o_navbar_apps_menu .dropdown-toggle, .o_menu_toggle";
const MENU_ID = /^\d{1,9}$/;
const XMLID = /^[\w.]{1,128}$/;

/**
 * A menu of the navbar or of the home menu, by its id or its external id.
 */
export function findMenu(id, xmlid) {
    const candidates = [];
    if (MENU_ID.test(id || "")) {
        candidates.push(...document.querySelectorAll(`[data-section="${id}"]`));
    }
    if (XMLID.test(xmlid || "")) {
        candidates.push(...document.querySelectorAll(`[data-menu-xmlid="${CSS.escape(xmlid)}"]`));
    }
    for (const candidate of candidates) {
        // The label of a section is a span inside its button
        const element = candidate.closest("button, a, li, .o-dropdown-item, .o_app") || candidate;
        if (isShown(element) && !element.closest(".o_nudge")) {
            return element;
        }
    }
    return null;
}

/**
 * A menu not on the screen is reached through the menu above it, or, for
 * an app, through the apps menu.
 */
function resolveMenu(target) {
    const element = findMenu(target.name, target.xmlid);
    if (element) {
        return { element };
    }
    if (!target.app && MENU_ID.test(target.parent || "")) {
        const parent = findMenu(target.parent, "");
        if (parent) {
            return {
                element: parent,
                isDetour: true,
                text: _t("Open “%s” first.", String(target.parent_label || "")),
            };
        }
    }
    const toggle = firstShown(document, APPS_TOGGLES);
    return toggle
        ? { element: toggle, isDetour: true, text: _t("Open the apps first.") }
        : null;
}

const RESOLVERS = {
    field(name, view, root) {
        const escaped = CSS.escape(name);
        const element =
            firstShown(root, `.o_field_widget[name="${escaped}"]`) ||
            firstShown(root, `thead th[data-name="${escaped}"]`);
        return element ? { element } : throughTab(view, root, "field", name);
    },
    button(name, view, root) {
        const element = firstShown(root, `button[name="${CSS.escape(name)}"]`);
        if (element) {
            return { element };
        }
        const node = archNode(view, "button", name);
        if (node?.closest('div[name="button_box"]')) {
            return throughMenu(root, ".o-form-buttonbox .o_button_more");
        }
        if (node?.closest("header")) {
            return throughMenu(root, ".o_statusbar_buttons .dropdown-toggle");
        }
        return throughTab(view, root, "button", name);
    },
    tab(name, view, root) {
        const page = archNode(view, "page", name);
        const tab = page && tabLink(root, page);
        return tab ? { element: tab.element } : null;
    },
    menu(name, view, root, target) {
        return resolveMenu(target);
    },
    control(name) {
        const selector = CONTROLS[name];
        const element = selector && firstShown(document, selector);
        return element ? { element } : null;
    },
};

/**
 * @param {{ type: string, name: string }} target
 * @param {Object|null} view the view in front, as registered by its controller
 * @returns {{ element: HTMLElement, isDetour?: boolean, text?: string } | null}
 */
export function resolveTarget(target, view) {
    const resolver = RESOLVERS[target?.type];
    if (!resolver || typeof target.name !== "string") {
        return null;
    }
    const root = view?.controller.rootRef?.el || document;
    try {
        return resolver(target.name, view, root, target);
    } catch {
        return null;
    }
}
