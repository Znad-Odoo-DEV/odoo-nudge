"""Stored tours, picked by the AI instead of improvised steps."""
from . import base


@base.register
class ShowTour(base.Tool):
    name = "show_tour"
    terminal = True
    description = (
        "Show the person a tour the managers recorded for a task (create a quotation, confirm an "
        "order, register a payment...): Nudge's pointer goes through the real steps with the "
        "managers' notes. Prefer it to inventing steps when the person asks how to do such a task. "
        "Give words of the task to find it, or tour_id from an earlier call. Without arguments it "
        "lists the tours the person may replay."
    )
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "tour_id": {"type": "integer", "minimum": 1},
            "words": {"type": "string", "maxLength": 120,
                      "description": "Words of the task, in the language of the person."},
        },
    }

    def run(self, env, arguments, screen):
        Tour = env["nudge.tour"]
        tour_id = arguments.get("tour_id")
        if not tour_id:
            found = Tour.find_tours(arguments.get("words") or "")
            if not found:
                return base.ToolResult("No recorded tour answers this. Explain or point with the other tools.")
            if len(found) != 1 or not arguments.get("words"):
                # Several answer, or none was asked for: the model chooses
                return base.ToolResult(base.dump(found))
            tour_id = found[0]["id"]
        # A search, not a browse: the record rules apply to the person
        tour = Tour.search([("id", "=", tour_id)])
        data = Tour.play(tour_id) if tour else {}
        if not data or not data["steps"]:
            return base.ToolResult("There is no such tour for this person.", is_error=True)
        steps = tour._way_to_start() + data["steps"]
        note = f" {len(data['skipped'])} step(s) do not exist on this database." if data["skipped"] else ""
        return base.ToolResult(
            f"The tour {data['name']!r} is being shown on the person's screen, {len(steps)} step(s).{note}",
            event={"type": "guidance", "steps": steps},
        )
