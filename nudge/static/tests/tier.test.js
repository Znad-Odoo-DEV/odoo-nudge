import { describe, expect, test } from "@odoo/hoot";
import { patchTranslations } from "@web/../tests/web_test_helpers";

import { fallbackLabel } from "@nudge/core/nudge_service";

describe.current.tags("headless");

test("an answer the main model gave after the economy model says so", () => {
    patchTranslations();
    expect(String(fallbackLabel({ type: "fallback", model: "claude-sonnet-5-5", reason: "escalated" }))).toBe(
        "Answered by the main model (claude-sonnet-5-5)"
    );
});

test("the model standing in is still named as before", () => {
    patchTranslations();
    expect(String(fallbackLabel({ type: "fallback", model: "qwen3:4b", local: true, reason: "error" }))).toBe(
        "Answered by the local model (qwen3:4b)"
    );
    expect(String(fallbackLabel({ type: "fallback", provider: "Groq", reason: "not_ready" }))).toBe(
        "Answered by the fallback model (Groq)"
    );
});
