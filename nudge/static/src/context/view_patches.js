import { onWillUnmount } from "@odoo/owl";

import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";
import { KanbanController } from "@web/views/kanban/kanban_controller";
import { ListController } from "@web/views/list/list_controller";

/**
 * The action service says which view is open but gives no access to the
 * record it shows. Controllers therefore announce themselves to Nudge, the
 * way the chatter of `mail` hooks into the form controller.
 *
 * The patches only register and unregister: they change nothing in the
 * behaviour of the views, and a fault in Nudge is never raised to them.
 */
function announce(controller, type) {
    try {
        const service = controller.env.services.nudge_context;
        if (service) {
            onWillUnmount(service.register({ type, controller }));
        }
    } catch {
        // The view works without Nudge
    }
}

patch(FormController.prototype, {
    setup() {
        super.setup(...arguments);
        announce(this, "form");
    },
});

patch(ListController.prototype, {
    setup() {
        super.setup(...arguments);
        announce(this, "list");
    },
});

patch(KanbanController.prototype, {
    setup() {
        super.setup(...arguments);
        announce(this, "kanban");
    },
});
