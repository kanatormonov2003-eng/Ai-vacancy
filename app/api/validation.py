"""Tiny declarative validator: typed, explicit, no mass assignment."""
from __future__ import annotations
from typing import Any, Callable
from ..errors import ValidationError

class Field:
    def __init__(self, kind: str, *, required: bool = False, default: Any = None,
                 min: float | None = None, max: float | None = None,
                 choices: tuple | None = None, max_len: int | None = None,
                 item: str | None = None, max_items: int = 200,
                 coerce: Callable[[Any], Any] | None = None):
        self.kind, self.required, self.default = kind, required, default
        self.min, self.max, self.choices = min, max, choices
        self.max_len, self.item, self.max_items, self.coerce = max_len, item, max_items, coerce

def _fail(field: str, msg: str):
    raise ValidationError(msg, details={"field": field})

def _check_scalar(name: str, kind: str, value: Any) -> Any:
    if kind == "str":
        if not isinstance(value, str):
            _fail(name, f"{name} must be a string")
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            _fail(name, f"{name} must be an integer")
        try:
            return int(value)
        except (TypeError, ValueError):
            _fail(name, f"{name} must be an integer")
    if kind == "float":
        try:
            return float(value)
        except (TypeError, ValueError):
            _fail(name, f"{name} must be a number")
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in ("true", "false", "1", "0", "yes", "no"):
            return value.lower() in ("true", "1", "yes")
        _fail(name, f"{name} must be a boolean")
    if kind == "dict":
        if not isinstance(value, dict):
            _fail(name, f"{name} must be an object")
        return value
    return value

def validate(payload: Any, schema: dict[str, Field]) -> dict:
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValidationError("Request body must be a JSON object")
    unknown = set(payload) - set(schema)
    if unknown:
        raise ValidationError(f"Unknown fields: {', '.join(sorted(unknown))}", details={"fields": sorted(unknown)})
    out: dict[str, Any] = {}
    for name, field in schema.items():
        present = name in payload and payload[name] is not None
        if not present:
            if field.required:
                _fail(name, f"{name} is required")
            out[name] = field.default() if callable(field.default) else field.default
            continue
        value = payload[name]
        if field.kind == "list":
            if not isinstance(value, list):
                _fail(name, f"{name} must be a list")
            if len(value) > field.max_items:
                _fail(name, f"{name} has too many items (max {field.max_items})")
            items = [_check_scalar(name, field.item or "str", v) for v in value]
            if field.choices:
                bad = [v for v in items if v not in field.choices]
                if bad:
                    _fail(name, f"{name} contains unsupported values: {bad[:3]}")
            if field.max_len:
                items = [v[: field.max_len] if isinstance(v, str) else v for v in items]
            out[name] = items
            continue
        value = _check_scalar(name, field.kind, value)
        if field.kind == "str":
            value = value.strip()
            if field.max_len is not None and len(value) > field.max_len:
                _fail(name, f"{name} must be at most {field.max_len} characters")
            if field.required and not value:
                _fail(name, f"{name} is required")
        if field.kind in ("int", "float"):
            if field.min is not None and value < field.min:
                _fail(name, f"{name} must be >= {field.min}")
            if field.max is not None and value > field.max:
                _fail(name, f"{name} must be <= {field.max}")
        if field.choices and field.kind != "list" and value not in field.choices:
            _fail(name, f"{name} must be one of: {', '.join(map(str, field.choices))}")
        if field.coerce:
            value = field.coerce(value)
        out[name] = value
    return out
