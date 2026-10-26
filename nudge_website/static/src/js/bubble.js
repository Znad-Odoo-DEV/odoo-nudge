/** @odoo-module **/
// The bubble of the assistant of the website. Plain DOM, no framework: it runs on the
// public pages and in the portal, where only the frontend assets are loaded. Every text
// of a visitor or of the server goes in with textContent; an address is used only when it
// stays on this website.

(function () {
    "use strict";

    const MAX_TRANSCRIPT = 20;

    function onReady(callback) {
        if (document.readyState === "loading") {
            document.addEventListener("DOMContentLoaded", callback);
        } else {
            callback();
        }
    }

    async function call(route, params) {
        const response = await fetch(route, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ jsonrpc: "2.0", method: "call", params }),
        });
        const payload = await response.json();
        if (payload.error) {
            throw new Error(payload.error.data?.message || payload.error.message || "error");
        }
        return payload.result;
    }

    function isLocal(url) {
        return typeof url === "string" && url.startsWith("/") && !url.startsWith("//") && !url.startsWith("/\\");
    }

    function line(log, className, text) {
        const node = document.createElement("p");
        node.className = className;
        node.dir = "auto";
        node.textContent = text;
        log.appendChild(node);
        log.scrollTop = log.scrollHeight;
        return node;
    }

    function link(log, label, url) {
        if (!isLocal(url)) {
            return;
        }
        const anchor = document.createElement("a");
        anchor.className = "o_nudge_web_link";
        anchor.href = url;
        anchor.textContent = label || url;
        const row = document.createElement("p");
        row.className = "o_nudge_web_bot";
        row.appendChild(anchor);
        log.appendChild(row);
    }

    function card(log, product) {
        const box = document.createElement("div");
        box.className = "o_nudge_web_product";
        const title = document.createElement("strong");
        title.textContent = product.name;
        box.appendChild(title);
        const detail = [product.price, product.availability].filter(Boolean).join(" - ");
        if (detail) {
            const small = document.createElement("span");
            small.textContent = detail;
            box.appendChild(small);
        }
        if (isLocal(product.url) && product.url) {
            const anchor = document.createElement("a");
            anchor.href = product.url;
            anchor.className = "o_nudge_web_link";
            anchor.textContent = product.name;
            title.textContent = "";
            title.appendChild(anchor);
        }
        log.appendChild(box);
    }

    function setup(root) {
        const panel = root.querySelector(".o_nudge_web_panel");
        const log = root.querySelector(".o_nudge_web_log");
        const form = root.querySelector(".o_nudge_web_form");
        const handover = root.querySelector(".o_nudge_web_handover");
        const transcript = [];
        let contact = "/contactus";
        let busy = false;

        const first = log.querySelector(".o_nudge_web_bot");
        if (first) {
            transcript.push({ role: "assistant", text: first.textContent });
        }

        root.querySelector(".o_nudge_web_open").addEventListener("click", () => {
            panel.hidden = !panel.hidden;
            if (!panel.hidden) {
                form.elements.message.focus();
            }
        });
        root.querySelector(".o_nudge_web_close").addEventListener("click", () => {
            panel.hidden = true;
        });

        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            const message = form.elements.message.value.trim();
            if (!message || busy) {
                return;
            }
            busy = true;
            form.elements.message.value = "";
            line(log, "o_nudge_web_me", message);
            transcript.push({ role: "user", text: message });
            const waiting = line(log, "o_nudge_web_bot o_nudge_web_wait", "...");
            try {
                const result = await call("/nudge_website/ask", { message });
                waiting.remove();
                contact = result.contact || contact;
                line(log, "o_nudge_web_bot", result.text);
                transcript.push({ role: "assistant", text: result.text });
                (result.products || []).forEach((product) => card(log, product));
                (result.links || []).forEach((item) => link(log, item.label, item.url));
                if (result.error === "limited" && isLocal(result.contact)) {
                    link(log, result.contact, result.contact);
                }
            } catch {
                waiting.remove();
                line(log, "o_nudge_web_bot", (root.querySelector(".o_nudge_web_failed") || {}).textContent || "...");
            } finally {
                busy = false;
                log.scrollTop = log.scrollHeight;
            }
        });

        root.querySelector(".o_nudge_web_person").addEventListener("click", async () => {
            try {
                const result = await call("/nudge_website/handover", {});
                if (result.type === "livechat" && result.url) {
                    window.open(result.url, "_blank", "noopener");
                    return;
                }
                handover.hidden = false;
                handover.dataset.contact = result.url || contact;
            } catch {
                handover.hidden = false;
            }
        });

        let sending = false;
        handover.addEventListener("submit", async (event) => {
            event.preventDefault();
            if (sending) {
                return;
            }
            sending = true;
            const data = new FormData(handover);
            try {
                const result = await call("/nudge_website/handover", {
                    name: data.get("name"),
                    email: data.get("email"),
                    consent: data.get("consent") === "on",
                    homepage: data.get("homepage") || "",
                    transcript: data.get("consent") === "on" ? transcript.slice(-MAX_TRANSCRIPT) : [],
                    message: transcript.filter((turn) => turn.role === "user").map((turn) => turn.text).join("\n"),
                });
                handover.hidden = true;
                if (result.text) {
                    line(log, "o_nudge_web_bot", result.text);
                } else if (isLocal(result.url)) {
                    window.location.assign(result.url);
                }
            } catch {
                handover.hidden = true;
            } finally {
                sending = false;
            }
        });
    }

    onReady(() => {
        const root = document.getElementById("nudge_web_root");
        if (root) {
            setup(root);
        }
    });
})();
