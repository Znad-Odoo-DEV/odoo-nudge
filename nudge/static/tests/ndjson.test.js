import { describe, expect, test } from "@odoo/hoot";

import { NdjsonParser } from "@nudge/core/ndjson";

describe.current.tags("headless");

function parse(chunks) {
    const events = [];
    const parser = new NdjsonParser((event) => events.push(event));
    for (const chunk of chunks) {
        parser.push(chunk);
    }
    parser.flush();
    return events;
}

test("one event per line", () => {
    expect(parse(['{"type":"start"}\n{"type":"delta","text":"a"}\n'])).toEqual([
        { type: "start" },
        { type: "delta", text: "a" },
    ]);
});

test("a line cut by the network is completed by the next chunk", () => {
    expect(parse(['{"type":"del', 'ta","text":"مر', 'حبا"}\n{"type"', ':"done"}'])).toEqual([
        { type: "delta", text: "مرحبا" },
        { type: "done" },
    ]);
});

test("a line break inside a text does not end the line", () => {
    expect(parse(['{"type":"delta","text":"a\\nb"}\n'])).toEqual([
        { type: "delta", text: "a\nb" },
    ]);
});

test("what is not an event is ignored", () => {
    expect(parse(["\n\n", "not json\n", "42\n", "null\n", '"text"\n', '{"type":"done"}\n'])).toEqual([
        { type: "done" },
    ]);
});
