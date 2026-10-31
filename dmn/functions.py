"""The supported FEEL built-ins; no reflection, host calls or dynamic code."""
from datetime import date
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from .values import FeelError, equal, logic, number

NAMES = {"not", "date", "number", "string", "abs", "floor", "ceiling", "decimal",
         "min", "max", "sum", "mean", "count", "all", "any", "contains", "starts with",
         "ends with", "lower case", "upper case", "string length", "list contains", "get value"}


def invoke(name, args):
    if name not in NAMES:
        raise FeelError("unsupported FEEL function: " + name)
    if name in {"min", "max", "sum", "mean"}:
        values = args[0] if len(args) == 1 and isinstance(args[0], list) else args
        if not values or not all(isinstance(v, Decimal) for v in values):
            raise FeelError(name + " requires nonempty numbers")
        return {"min": lambda: min(values), "max": lambda: max(values),
                "sum": lambda: sum(values, Decimal(0)),
                "mean": lambda: sum(values, Decimal(0)) / len(values)}[name]()
    if name in {"all", "any"} and len(args) == 1 and isinstance(args[0], list):
        result = name == "all"
        for value in args[0]:
            result = logic("and" if name == "all" else "or", result, value)
        return result
    if name == "not" and len(args) == 1:
        return not args[0] if type(args[0]) is bool else None
    if name == "date" and len(args) == 1 and isinstance(args[0], str):
        result = date.fromisoformat(args[0])
        if result.isoformat() != args[0]:
            raise FeelError("date requires YYYY-MM-DD")
        return result
    if name == "number" and len(args) == 1 and isinstance(args[0], str):
        return number(args[0])
    if name == "string" and len(args) == 1 and type(args[0]) in (str, Decimal, bool, date):
        value = args[0]
        return ("true" if value else "false") if type(value) is bool else format(value, "f") if isinstance(value, Decimal) else str(value)
    if name == "count" and len(args) == 1 and isinstance(args[0], list):
        return Decimal(len(args[0]))
    if name == "string length" and len(args) == 1 and isinstance(args[0], str):
        return Decimal(len(args[0]))
    if name in {"abs", "floor", "ceiling"} and len(args) == 1 and isinstance(args[0], Decimal):
        value = args[0]
        return abs(value) if name == "abs" else value.to_integral_value(rounding=ROUND_FLOOR if name == "floor" else ROUND_CEILING)
    if name == "decimal" and len(args) == 2 and all(isinstance(v, Decimal) for v in args):
        value, scale = args
        if scale != scale.to_integral_value() or not -32 <= scale <= 32:
            raise FeelError("decimal scale must be an integer from -32 to 32")
        return value.quantize(Decimal(1).scaleb(-int(scale)))
    if name in {"lower case", "upper case"} and len(args) == 1 and isinstance(args[0], str):
        return args[0].lower() if name == "lower case" else args[0].upper()
    if name in {"contains", "starts with", "ends with"} and len(args) == 2 and all(isinstance(v, str) for v in args):
        a, b = args
        return b in a if name == "contains" else a.startswith(b) if name == "starts with" else a.endswith(b)
    if name == "list contains" and len(args) == 2 and isinstance(args[0], list):
        result = False
        for value in args[0]:
            result = logic("or", result, equal(value, args[1]))
        return result
    if name == "get value" and len(args) == 2 and isinstance(args[0], dict) and isinstance(args[1], str):
        return args[0].get(args[1])
    raise FeelError("invalid arguments to FEEL function " + name)
