"""Validated, finite input vocabularies for authored decision tables."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import json
import re

from .parser import KEYWORDS
from .values import FeelError, equal, typed, wire


def fields(obj, required, optional=()):
    if not isinstance(obj, dict) or not set(required) <= obj.keys() or obj.keys() - set(required) - set(optional):
        raise FeelError("expected fields " + ", ".join(required) + "; optional " + ", ".join(optional))


def identifier(name):
    reserved = KEYWORDS | {"not", "for", "return", "some", "every", "satisfies", "function", "external", "instance", "of"}
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*(?: [A-Za-z_][A-Za-z_0-9]*)*", name) or name in reserved or len(name) > 80:
        raise FeelError("unsupported or reserved decision name")
    return name


@dataclass(frozen=True)
class Scalar:
    kind: str
    values: tuple | None
    low: object = None
    high: object = None
    unit: str | None = None

    @classmethod
    def parse(cls, spec, *, input=False):
        fields(spec, ("type",), ("values", "min", "max", "unit"))
        kind = spec["type"]
        if kind not in {"number", "boolean", "string", "date"}:
            raise FeelError("unsupported scalar type")
        if "unit" in spec and (kind != "number" or not isinstance(spec["unit"], str) or not spec["unit"]):
            raise FeelError("only numbers may declare a nonempty unit")
        if "values" in spec and not isinstance(spec["values"], list):
            raise FeelError("domain values must be an array")
        values = tuple(typed(v, kind) for v in spec["values"]) if "values" in spec else None
        if values is not None and (not 1 <= len(values) <= 256 or len(set(values)) != len(values)):
            raise FeelError("domain values must be unique and nonempty, at most 256")
        if values is not None and ("min" in spec or "max" in spec):
            raise FeelError("use an enumeration or bounds, not both")
        if ("min" in spec) != ("max" in spec):
            raise FeelError("both inclusive min and max are required")
        low, high = None, None
        if "min" in spec:
            if kind not in {"number", "date"}:
                raise FeelError("bounds require number or date")
            low, high = typed(spec["min"], kind), typed(spec["max"], kind)
            if low > high:
                raise FeelError("domain min exceeds max")
        if kind == "boolean" and values is None:
            values = (False, True)
        if input and values is None and low is None:
            raise FeelError("inputs require an explicit enum or min/max domain")
        return cls(kind, values, low, high, spec.get("unit"))

    def accept(self, value):
        value = typed(value, self.kind)
        if self.values is not None and not any(equal(value, v) is True for v in self.values):
            raise FeelError("input/output value is outside its declared vocabulary")
        if self.low is not None and not self.low <= value <= self.high:
            raise FeelError("input/output value is outside its declared bounds")
        return value

    def accept_result(self, value):
        expected = {"number": Decimal, "date": date, "string": str, "boolean": bool}[self.kind]
        if type(value) is not expected:
            raise FeelError("FEEL result does not match its declared " + self.kind + " type")
        return self.accept(value)


def canonical(spec):
    """Copy and canonicalize JSON; reject host objects, floats and duplicate keys."""
    def reject_float(value):
        raise FeelError("model numeric constants must be decimal strings or integers")
    text = json.dumps(spec, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(text.encode()) > 262144:
        raise FeelError("decision model exceeds 256 KiB")
    return json.loads(text, parse_float=reject_float), text


def load_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise FeelError("duplicate JSON field: " + key)
            result[key] = value
        return result
    if len(text.encode()) > 262144:
        raise FeelError("decision model exceeds 256 KiB")
    return json.loads(text, object_pairs_hook=pairs)
