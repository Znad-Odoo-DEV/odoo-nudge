import { markup } from "@odoo/owl";

/**
 * Small Markdown renderer for answers of the model.
 *
 * The text of a model is untrusted. It is never inserted as HTML: every piece
 * of text is escaped, and the only tags in the output are the ones written
 * here. DOMPurify then filters the result a second time.
 */

const ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

const ALLOWED_TAGS = [
    "a", "blockquote", "br", "code", "del", "em", "h4", "h5", "h6", "hr", "li", "ol", "p",
    "pre", "strong", "table", "tbody", "td", "th", "thead", "tr", "ul",
];
const ALLOWED_ATTR = ["href", "target", "rel", "start"];

const INLINE = new RegExp(
    [
        "(`[^`\\n]+`)", // 1 code
        "(\\[[^\\]\\n]+\\]\\([^)\\s]+\\))", // 2 link
        "(https?:\\/\\/[^\\s<>()\\[\\]]+)", // 3 bare url
        "(\\*\\*[^\\n]+?\\*\\*)", // 4 bold
        "(\\*[^*\\s][^*\\n]*\\*)", // 5 italic
        "(~~[^~\\n]+~~)", // 6 strike
    ].join("|"),
    "g"
);

const FENCE = /^\s*```/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBER = /^\s*\d{1,9}[.)]\s+(.*)$/;
const TABLE_ROW = /^\s*\|.*\|\s*$/;
const TABLE_RULE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

export function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (character) => ESCAPES[character]);
}

/**
 * @param {string} url
 * @returns {boolean} whether the url may be the target of a link
 */
export function isSafeUrl(url) {
    if (/^https?:\/\//i.test(url) || /^mailto:/i.test(url)) {
        return true;
    }
    // Address inside Odoo. "//host" would leave the site
    return url.startsWith("/") && !url.startsWith("//");
}

function renderLink(url, label) {
    if (!isSafeUrl(url)) {
        return label;
    }
    const external = !url.startsWith("/");
    const attributes = external ? ' target="_blank" rel="noopener noreferrer"' : "";
    return `<a href="${escapeHtml(url)}"${attributes}>${label}</a>`;
}

function renderInline(text) {
    let html = "";
    let last = 0;
    for (const match of text.matchAll(INLINE)) {
        html += escapeHtml(text.slice(last, match.index));
        const token = match[0];
        if (match[1]) {
            html += `<code>${escapeHtml(token.slice(1, -1))}</code>`;
        } else if (match[2]) {
            const split = token.lastIndexOf("](");
            html += renderLink(token.slice(split + 2, -1), renderInline(token.slice(1, split)));
        } else if (match[3]) {
            // Punctuation ending a sentence is not part of the address
            const url = token.replace(/[.,;:!?]+$/, "");
            html += renderLink(url, escapeHtml(url)) + escapeHtml(token.slice(url.length));
        } else if (match[4]) {
            html += `<strong>${renderInline(token.slice(2, -2))}</strong>`;
        } else if (match[5]) {
            html += `<em>${renderInline(token.slice(1, -1))}</em>`;
        } else {
            html += `<del>${renderInline(token.slice(2, -2))}</del>`;
        }
        last = match.index + token.length;
    }
    return html + escapeHtml(text.slice(last));
}

function splitRow(line) {
    return line
        .trim()
        .replace(/^\|/, "")
        .replace(/\|$/, "")
        .split("|")
        .map((cell) => cell.trim());
}

function renderTable(header, rows) {
    const head = header.map((cell) => `<th>${renderInline(cell)}</th>`).join("");
    const body = rows
        .map((row) => `<tr>${row.map((cell) => `<td>${renderInline(cell)}</td>`).join("")}</tr>`)
        .join("");
    return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function isBlockStart(lines, index) {
    const line = lines[index];
    return (
        FENCE.test(line) ||
        HEADING.test(line) ||
        RULE.test(line) ||
        QUOTE.test(line) ||
        BULLET.test(line) ||
        NUMBER.test(line) ||
        (TABLE_ROW.test(line) && TABLE_RULE.test(lines[index + 1] || ""))
    );
}

/**
 * @param {string} text
 * @returns {string} html made of the allowed tags only
 */
export function markdownToHtml(text) {
    const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
    const blocks = [];
    let index = 0;

    const collect = (pattern) => {
        const items = [];
        while (index < lines.length && pattern.test(lines[index])) {
            items.push(lines[index].match(pattern)[1]);
            index++;
        }
        return items;
    };

    // An indented line goes on with the item above it, across a blank line too:
    // "1. Enable\n   Go to: …\n\n2. Add" made three lists, each numbered from 1
    const goesOn = (at) => /^\s+\S/.test(lines[at]) && !isBlockStart(lines, at);
    const collectItems = (pattern) => {
        const items = [];
        while (index < lines.length) {
            const line = lines[index];
            if (pattern.test(line)) {
                items.push([renderInline(line.match(pattern)[1].trim())]);
            } else if (line.trim() && goesOn(index)) {
                items[items.length - 1].push(renderInline(line.trim()));
            } else if (!line.trim()) {
                let next = index + 1;
                while (next < lines.length && !lines[next].trim()) {
                    next++;
                }
                if (next === lines.length || !(pattern.test(lines[next]) || goesOn(next))) {
                    break;
                }
            } else {
                break;
            }
            index++;
        }
        return items.map((parts) => `<li>${parts.join("<br>")}</li>`);
    };

    while (index < lines.length) {
        const line = lines[index];
        if (!line.trim()) {
            index++;
        } else if (FENCE.test(line)) {
            index++;
            const code = [];
            // A fence still open is an answer still being written
            while (index < lines.length && !FENCE.test(lines[index])) {
                code.push(lines[index]);
                index++;
            }
            index++;
            blocks.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
        } else if (HEADING.test(line)) {
            const [, marks, title] = line.match(HEADING);
            const level = Math.min(marks.length + 3, 6);
            blocks.push(`<h${level}>${renderInline(title)}</h${level}>`);
            index++;
        } else if (RULE.test(line)) {
            blocks.push("<hr>");
            index++;
        } else if (QUOTE.test(line)) {
            const quote = collect(QUOTE).map(renderInline).join("<br>");
            blocks.push(`<blockquote>${quote}</blockquote>`);
        } else if (BULLET.test(line)) {
            blocks.push(`<ul>${collectItems(BULLET).join("")}</ul>`);
        } else if (NUMBER.test(line)) {
            // A list that goes on after a paragraph keeps the number written
            // (a template inside a template lost its space in the bundle: <olstart="2">)
            const start = Number(line.match(/\d+/)[0]);
            const opening = start > 1 ? '<ol start="' + start + '">' : "<ol>";
            blocks.push(opening + collectItems(NUMBER).join("") + "</ol>");
        } else if (TABLE_ROW.test(line) && TABLE_RULE.test(lines[index + 1] || "")) {
            const header = splitRow(line);
            index += 2;
            const rows = [];
            while (index < lines.length && TABLE_ROW.test(lines[index])) {
                rows.push(splitRow(lines[index]));
                index++;
            }
            blocks.push(renderTable(header, rows));
        } else {
            const paragraph = [];
            while (index < lines.length && lines[index].trim() && !isBlockStart(lines, index)) {
                paragraph.push(renderInline(lines[index].trim()));
                index++;
            }
            blocks.push(`<p>${paragraph.join("<br>")}</p>`);
        }
    }
    return blocks.join("");
}

/**
 * @param {string} text
 * @returns {ReturnType<markup>} value to render with t-out
 */
export function renderMarkdown(text) {
    let html = markdownToHtml(text);
    if (window.DOMPurify) {
        html = window.DOMPurify.sanitize(html, { ALLOWED_TAGS, ALLOWED_ATTR });
    }
    return markup(html);
}
