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
import ast
import json
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


def example(schema, depth=0):
    """:return: a value written the way ``schema`` wants it, its required
    parts only: what a model that got it wrong is shown"""
    kind = schema.get("type")
    kind = kind if isinstance(kind, str) else (kind or ["string"])[0]
    if "enum" in schema:
        return schema["enum"][0]
    if kind == "object":
        if depth > 2:
            return {}
        properties = schema.get("properties", {})
        names = [name for name in schema.get("required", ()) if name in properties]
        return {name: example(properties[name], depth + 1) for name in (names or list(properties)[:3])}
    if kind == "array":
        return [example(schema["items"], depth + 1)] if "items" in schema else []
    return {"integer": 1, "number": 1.5, "boolean": True, "null": None}.get(kind, "...")


def shape(schema):
    return json.dumps(example(schema), ensure_ascii=False)


# What a field path written as a placeholder carries around it: {{ object.x }}, #object.x
PLACEHOLDER_BITS = re.compile(r"\{\{|\}\}|^\s*#|\bobject\.")


def is_pairs(schema):
    """:return: whether ``schema`` is a list of ``{"field", "value"}``"""
    items = schema.get("items") or {}
    return (schema.get("type") == "array" and items.get("type") == "object"
            and set(items.get("required", ())) == {"field", "value"})


def is_empty(value):
    return value is None or (isinstance(value, (str, list, dict)) and not value)


def unescaped_code(code):
    """:return: ``code`` with its lines, when it was written on one line with
    "\\n" between them and does not compile as it is"""
    if "\\n" not in code:
        return code
    try:
        ast.parse(code)
    except SyntaxError:
        # 7 of 8 tries of a reminder held "\\n": each refused as a SyntaxError of line 1
        return code.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "    ")
    return code


def _kept(name, item, properties, required):
    """:return: whether an argument written empty is kept: "view_id": "" and
    an argument no tool has were refused four times in a row for nothing"""
    if not is_empty(item) or name in required:
        return True
    if name not in properties:
        return False
    try:
        validate(item, properties[name], name)
    except SchemaError:
        return False
    return True


def forgive(value, schema):
    """:return: ``value`` as a small model means it, where it cannot mean
    anything else: 9 written for "9", ``{"user_id": "8"}`` for a list of
    ``{"field": "user_id", "value": "8"}``, ``{"field": "user_id"}`` for
    "user_id", "{{ object.user_id }}" or "#object.id" for the field path the
    pattern asks for, a list written as the text of its JSON, an argument
    written empty for one not given, and code written on one line with "\\n"
    for its lines"""
    expected = schema.get("type")
    allowed = [expected] if isinstance(expected, str) else list(expected or ())
    if allowed in (["array"], ["object"]) and isinstance(value, str) and value.strip().startswith(("[", "{")):
        # actions: "[{...}]" was refused: expected array, got str
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = None
        if isinstance(parsed, TYPES[allowed[0]]):
            value = parsed
    if allowed == ["string"] and "enum" not in schema and isinstance(value, (int, float)):
        return str(value).lower() if isinstance(value, bool) else str(value)
    if allowed == ["string"] and isinstance(value, dict) and len(value) == 1:
        (only,) = value.values()
        value = only if isinstance(only, str) else value
    if allowed == ["string"] and isinstance(value, str) and schema.get("pattern") and not re.fullmatch(
            schema["pattern"], value):
        cleaned = PLACEHOLDER_BITS.sub("", value).strip()
        if cleaned != value and re.fullmatch(schema["pattern"], cleaned):
            return cleaned
    if isinstance(value, dict) and is_pairs(schema) and "field" not in value:
        value = [{"field": name, "value": item} for name, item in value.items()]
    if isinstance(value, list) and "items" in schema:
        return [forgive(item, schema["items"]) for item in value]
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", ())
        forgiven = {name: forgive(item, properties[name]) if name in properties else item
                    for name, item in value.items()
                    if not properties or _kept(name, item, properties, required)}
        if isinstance(forgiven.get("code"), str) and properties.get("code", {}).get("type") == "string":
            forgiven["code"] = unescaped_code(forgiven["code"])
        return forgiven
    return value


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
            told = f"expected {' or '.join(allowed)}, got {type(value).__name__}"
            if "object" in allowed or "array" in allowed:
                # A model that wrote text where the parts go is shown them
                told += f", such as {shape(schema)}"
            _fail(path, told)

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
                    accepted = ", ".join(properties)
                    _fail(path, f"'{name}' is not an accepted argument; accepted: {accepted}")
        for name, item in value.items():
            if name in properties:
                validate(item, properties[name], f"{path}.{name}" if path else name)
    return value
