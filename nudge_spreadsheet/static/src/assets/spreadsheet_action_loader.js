import { _t } from "@web/core/l10n/translation";
import { addSpreadsheetActionLazyLoader } from "@spreadsheet/assets_backend/spreadsheet_action_loader";

// The editor is heavy: its bundle is loaded the first time a spreadsheet of
// Nudge is opened, and the real action takes this one's place
addSpreadsheetActionLazyLoader("nudge_spreadsheet.open", "nudge-spreadsheet", _t("Nudge Spreadsheet"));
