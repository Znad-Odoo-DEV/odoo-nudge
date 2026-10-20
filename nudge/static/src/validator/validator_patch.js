import { patch } from "@web/core/utils/patch";
import { FormController } from "@web/views/form/form_controller";

import { NudgeFormStrip } from "./validator_strip";

/**
 * The strip joins the form view; it reads the record and changes nothing in it.
 */
patch(FormController, {
    components: { ...FormController.components, NudgeFormStrip },
});
