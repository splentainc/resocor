"""Bounded FEEL values and decimal128 arithmetic context; standard library only."""
from datetime import date
from dataclasses import dataclass
from decimal import Context, Decimal, DecimalException, ROUND_HALF_EVEN, localcontext

DECIMAL = Context(prec=34, rounding=ROUND_HALF_EVEN, Emin=-100, Emax=100)


class FeelError(ValueError):
    pass


def number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise FeelError("number requires an integer, Decimal or decimal string")
    try:
        original = Decimal(value)
        if not original.is_finite():
            raise FeelError("number must be finite")
        with localcontext(DECIMAL) as ctx:
            result = ctx.create_decimal(original)
    except DecimalException as exc:
        raise FeelError("invalid or out-of-bounds decimal") from exc
    if result != original:
        raise FeelError("number exceeds 34 significant digits")
    if not result.is_finite() or result.copy_abs() >= Decimal("1e100") or result.as_tuple().exponent < -100:
        raise FeelError("number exceeds supported bounds")
    return result


def normalize(value, depth=0, budget=None):
    budget = [0] if budget is None else budget
    budget[0] += 1
    if budget[0] > 4096:
        raise FeelError("value exceeds total node limit")
    if depth > 32:
        raise FeelError("value exceeds nesting limit")
    if value is None or type(value) in (bool, date):
        return value
    if isinstance(value, (int, Decimal)) and not isinstance(value, bool):
        return number(value)
    if isinstance(value, str) and len(value) <= 4096:
        return value
    if isinstance(value, list) and len(value) <= 256:
        return [normalize(v, depth + 1, budget) for v in value]
    if isinstance(value, dict) and len(value) <= 256 and all(isinstance(k, str) for k in value):
        return {k: normalize(v, depth + 1, budget) for k, v in value.items()}
    raise FeelError("unsupported FEEL value or size; Python floats are not accepted")


def wire(value):
    if isinstance(value, Decimal):
        return format(value, "f")
    if type(value) is date:
        return value.isoformat()
    if isinstance(value, Range):
        return {"range": {"low": wire(value.low), "high": wire(value.high),
                          "low_included": value.low_included, "high_included": value.high_included}}
    if isinstance(value, list):
        return [wire(v) for v in value]
    if isinstance(value, dict):
        return {k: wire(v) for k, v in value.items()}
    return value


@dataclass(frozen=True)
class Range:
    low: object
    high: object
    low_included: bool
    high_included: bool

    def contains(self, value):
        if value is None or type(value) is not type(self.low) or type(value) is not type(self.high):
            return None
        return (value >= self.low if self.low_included else value > self.low) and (
            value <= self.high if self.high_included else value < self.high)


def typed(value, kind):
    if kind == "number":
        return number(value)
    if kind == "date":
        if type(value) is date:
            return value
        if not isinstance(value, str) or len(value) != 10:
            raise FeelError("date requires YYYY-MM-DD")
        result = date.fromisoformat(value)
        if result.isoformat() != value:
            raise FeelError("date requires YYYY-MM-DD")
        return result
    if kind == "boolean" and type(value) is bool:
        return value
    if kind == "string" and isinstance(value, str) and len(value) <= 4096:
        return value
    raise FeelError("value does not match declared " + str(kind) + " type")


def equal(a, b):
    if a is None or b is None:
        return a is b
    if type(a) is not type(b):
        return None
    if isinstance(a, list):
        pairs = [equal(x, y) for x, y in zip(a, b)]
        return False if len(a) != len(b) or False in pairs else None if None in pairs else True
    if isinstance(a, dict):
        if a.keys() != b.keys():
            return False
        pairs = [equal(a[k], b[k]) for k in a]
        return False if False in pairs else None if None in pairs else True
    return a == b


def logic(op, a, b):
    a = a if type(a) is bool else None
    b = b if type(b) is bool else None
    if op == "and":
        return False if a is False or b is False else True if a is True and b is True else None
    return True if a is True or b is True else False if a is False and b is False else None
