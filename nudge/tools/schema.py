"""Validation of tool arguments against the subset of JSON Schema that the
tools of Nudge use.

What a model writes is checked here before anything is done with it. The
subset is small on purpose: every schema is written in this module's
package, so there is nothing to support that the tools do not need.

Supported: ``type`` (object, array, string, integer, number, boolean),
``properties``, ``required``, ``additionalProperties: false``, ``items``,
``enum``, ``minItems``, ``maxItems``, ``minLength``, ``maxLength``,
``minimum``, ``maximum``, ``pattern``.
"""
import re

TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "null": type(None),
}


class SchemaError(ValueError):
    """The value does not follow the schema. The message is written for the
    model: it says where, and what was expected."""


def _fail(path, message):
    raise SchemaError(f"{path or 'arguments'}: {message}")


def validate(value, schema, path=""):
    """:raise SchemaError: when ``value`` does not follow ``schema``"""
    expected = schema.get("type")
    if expected:
        # One type, or any of several: ["string", "number", "null"]
        allowed = [expected] if isinstance(expected, str) else list(expected)
        # In Python a boolean is an integer; in JSON it is not
        is_boolean = isinstance(value, bool)
        if not any(
            isinstance(value, TYPES[name]) and (not is_boolean or name == "boolean")
            for name in allowed
        ):
            _fail(path, f"expected {' or '.join(allowed)}, got {type(value).__name__}")

    if "enum" in schema and value not in schema["enum"]:
        allowed = ", ".join(repr(item) for item in schema["enum"])
        _fail(path, f"must be one of {allowed}")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            _fail(path, "must not be empty" if schema["minLength"] == 1 else "is too short")
        if len(value) > schema.get("maxLength", float("inf")):
            _fail(path, f"is longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], value):
            _fail(path, "is not written in the expected form")

    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if value < schema.get("minimum", float("-inf")):
            _fail(path, f"must be at least {schema['minimum']}")
        if value > schema.get("maximum", float("inf")):
            _fail(path, f"must be at most {schema['maximum']}")

    elif isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            _fail(path, f"needs at least {schema['minItems']} item(s)")
        if len(value) > schema.get("maxItems", float("inf")):
            _fail(path, f"accepts at most {schema['maxItems']} item(s)")
        if "items" in schema:
            for index, item in enumerate(value):
                validate(item, schema["items"], f"{path}[{index}]")

    elif isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", ()):
            if name not in value:
                _fail(path, f"'{name}' is missing")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in properties:
                    _fail(path, f"'{name}' is not an accepted argument")
        for name, item in value.items():
            if name in properties:
                validate(item, properties[name], f"{path}.{name}" if path else name)
    return value
