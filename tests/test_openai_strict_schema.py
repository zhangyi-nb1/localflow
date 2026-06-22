"""R4 fix#2 — OpenAI strict-mode schema sanitizer.

OpenAI strict function-calling requires every object schema to set
``additionalProperties: false`` and to list every property in
``required``; free-form dict fields (objects with no declared
properties) can't be expressed and must be dropped. The kernel's
``submit_loop_decision`` schema was authored for Anthropic's laxer
strict mode and tripped all three rules, so the react loop's first
consult 400'd and silently fell back to batch (see
docs/test_artifacts/v0.36.0/react_loop/finding.md).

These tests pin the sanitizer so that regression can't recur.
"""

from __future__ import annotations

import copy

from app.agent.openai_client import _force_strict_object_schema, _is_freeform_object


def _violations(node, path="root", out=None):
    """Collect OpenAI-strict violations: additionalProperties!=false,
    required!=property-keys, or a property-less object."""
    if out is None:
        out = []
    if isinstance(node, dict):
        if node.get("type") == "object":
            props = set((node.get("properties") or {}).keys())
            req = set(node.get("required") or [])
            if node.get("additionalProperties") is not False:
                out.append((path, "additionalProperties not false"))
            if req != props:
                out.append((path, f"required!=props (missing={props - req} extra={req - props})"))
            if not props:
                out.append((path, "property-less object"))
        for k, v in node.items():
            _violations(v, f"{path}.{k}", out)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _violations(v, f"{path}[{i}]", out)
    return out


def test_forces_additional_properties_false():
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}
    _force_strict_object_schema(schema)
    assert schema["additionalProperties"] is False


def test_required_lists_every_property():
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
        "required": ["a"],
    }
    _force_strict_object_schema(schema)
    assert set(schema["required"]) == {"a", "b"}


def test_overrides_additional_properties_true():
    # the metadata case: free-form dict with additionalProperties:true.
    schema = {
        "type": "object",
        "properties": {
            "meta": {"type": "object", "additionalProperties": True},
            "name": {"type": "string"},
        },
        "required": ["meta", "name"],
    }
    _force_strict_object_schema(schema)
    # the free-form object is dropped entirely (can't exist under strict).
    assert "meta" not in schema["properties"]
    assert set(schema["required"]) == {"name"}


def test_recurses_into_anyof_and_nested_objects():
    schema = {
        "type": "object",
        "properties": {
            "child": {
                "anyOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "properties": {"x": {"type": "string"}},
                        "additionalProperties": True,
                    },
                ]
            }
        },
    }
    _force_strict_object_schema(schema)
    nested = schema["properties"]["child"]["anyOf"][1]
    assert nested["additionalProperties"] is False
    assert nested["required"] == ["x"]


def test_is_freeform_object():
    assert _is_freeform_object({"type": "object"}) is True
    assert _is_freeform_object({"type": "object", "additionalProperties": True}) is True
    assert _is_freeform_object({"type": "object", "properties": {"a": {}}}) is False
    assert _is_freeform_object({"type": "string"}) is False
    # nullable free-form (anyOf containing a property-less object)
    assert _is_freeform_object({"anyOf": [{"type": "null"}, {"type": "object"}]}) is True


def test_real_loop_decision_schema_becomes_strict_clean():
    """The actual kernel schema, sanitised, must have zero strict
    violations — this is the exact path that 400'd before fix#2."""
    from localflow_kernel.react_prompts import build_loop_decision_tool_schema

    for allowed in (None, ["mkdir", "move"]):
        raw = build_loop_decision_tool_schema(allowed_action_types=allowed)
        sanitised = _force_strict_object_schema(copy.deepcopy(raw))
        assert _violations(sanitised) == []


def test_does_not_mutate_via_client_copy():
    """The client deep-copies before sanitising, so a caller's schema is
    never mutated. Verify the sanitizer itself mutates in place (the
    client owns the copy)."""
    original = {"type": "object", "properties": {"a": {"type": "string"}}, "required": []}
    returned = _force_strict_object_schema(original)
    assert returned is original  # in place
    assert returned["required"] == ["a"]
