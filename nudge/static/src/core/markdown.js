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

// Every span is capped: a mark with no closing one scans at most this far, so the work on a line
// is linear in its length (an open "[" or "**" used to scan to the end of the line, each time)
const INLINE = new RegExp(
    [
        "(`[^`\\n]{1,500}`)", // 1 code
        "(\\[[^\\]\\n]{1,200}\\]\\([^)\\s]{1,500}\\))", // 2 link
        "(https?:\\/\\/[^\\s<>()\\[\\]]{1,2000})", // 3 bare url
        "(\\*\\*[^\\n]{1,500}?\\*\\*)", // 4 bold
        "(\\*[^*\\s][^*\\n]{0,500}\\*)", // 5 italic
        "(~~[^~\\n]{1,500}~~)", // 6 strike
    ].join("|"),
    "g"
);

const FENCE = /^\s*```/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*)$/;
const RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBER = /^\s*\d{1,9}[.)]\s+(.*)$/;
const RULE_CELL = /^:?-{2,}:?$/;

function isTableRow(line) {
    const row = line.trim();
    return row.length >= 2 && row.startsWith("|") && row.endsWith("|");
}

// "|---|:--:|", "---|---" and the like; split, not one regex with blanks inside a repeated group
function isTableRule(line) {
    let row = line.trim();
    if (row.startsWith("|")) {
        row = row.slice(1);
    }
    if (row.endsWith("|")) {
        row = row.slice(0, -1);
    }
    return row.split("|").every((cell) => RULE_CELL.test(cell.trim()));
}

/** @returns {string} the title of a heading with the closing # taken off */
function headingTitle(text) {
    let title = text.trimEnd();
    let end = title.length;
    while (end > 0 && title[end - 1] === "#") {
        end--;
    }
    return title.slice(0, end).trimEnd();
}

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
    // Address inside Odoo. "//host" and "/\host" (a browser reads the backslash as a slash) would leave the site
    return url.startsWith("/") && !url.startsWith("//") && !url.startsWith("/\\");
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
            let end = token.length;
            while (end > 0 && ".,;:!?".includes(token[end - 1])) {
                end--;
            }
            const url = token.slice(0, end);
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
        (isTableRow(line) && isTableRule(lines[index + 1] || ""))
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
            blocks.push(`<h${level}>${renderInline(headingTitle(title))}</h${level}>`);
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
        } else if (isTableRow(line) && isTableRule(lines[index + 1] || "")) {
            const header = splitRow(line);
            index += 2;
            const rows = [];
            while (index < lines.length && isTableRow(lines[index])) {
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
 * A table does not fit a narrow place (the notice of a job done in the background): each row is a bullet of
 * its own, cells joined by " · ", the header in bold; the rule row is dropped. What is not a table is left.
 *
 * @param {string} text
 * @returns {string} markdown without tables
 */
export function tablesAsLines(text) {
    const lines = String(text || "").split(/\r\n?|\n/);
    const out = [];
    for (let index = 0; index < lines.length; index++) {
        if (isTableRow(lines[index]) && isTableRule(lines[index + 1] || "")) {
            const header = splitRow(lines[index]).filter(Boolean);
            out.push(header.length ? `**${header.join(" · ")}**` : "");
            index++;
            while (index + 1 < lines.length && isTableRow(lines[index + 1])) {
                index++;
                out.push(`- ${splitRow(lines[index]).join(" · ")}`);
            }
        } else {
            out.push(lines[index]);
        }
    }
    return out.join("\n");
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

// An answer that streams grows by a few characters at a time: it is shown again at most this often
export const STREAM_RENDER_INTERVAL = 50;

/**
 * Renders the answer of one message. The same text is rendered once, and while the answer streams a
 * new text is rendered at most once every STREAM_RENDER_INTERVAL ms (the one in between shows the
 * last rendering: `stale` says a newer text waits, and the caller asks again later). A finished
 * answer is always rendered exactly.
 */
export class MarkdownRenderer {
    /** @param {() => number} [clock] milliseconds */
    constructor(clock = () => performance.now()) {
        this.clock = clock;
        this.renders = 0;
        this.stale = false;
        this.text = null;
        this.value = markup("");
        this.at = -Infinity;
    }

    /**
     * @param {string} text
     * @param {boolean} streaming whether the answer is still being written
     * @returns {ReturnType<markup>}
     */
    render(text, streaming) {
        if (text === this.text) {
            this.stale = false;
            return this.value;
        }
        const now = this.clock();
        if (streaming && this.text !== null && now - this.at < STREAM_RENDER_INTERVAL) {
            this.stale = true;
            return this.value;
        }
        this.text = text;
        this.value = renderMarkdown(text);
        this.at = now;
        this.renders++;
        this.stale = false;
        return this.value;
    }
}
