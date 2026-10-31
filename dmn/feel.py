"""FEEL expressions with decimal numbers, null semantics and checked diagnostics."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, DecimalException, localcontext

from .functions import invoke
from .parser import compile_expression, references
from .values import DECIMAL, FeelError, Range, equal, logic, normalize, number


@dataclass(frozen=True)
class Evaluation:
    value: object
    errors: tuple = ()


class Interpreter:
    def __init__(self):
        self.errors = []
        self.steps = 0

    def error(self, message):
        self.errors.append(message)
        return None

    def run(self, node, env):
        self.steps += 1
        if self.steps > 4096:
            raise FeelError("FEEL evaluation exceeds work limit")
        try:
            result = self.visit(node, env)
            return number(result) if isinstance(result, Decimal) else result
        except (ValueError, TypeError, KeyError, IndexError, DecimalException, OverflowError) as exc:
            return self.error(str(exc))

    def visit(self, node, env):
        kind = node[0]
        if kind == "literal":
            return node[1]
        if kind == "name":
            return env[node[1]] if node[1] in env else self.error("unknown FEEL variable: " + node[1])
        if kind == "if":
            condition = self.run(node[1], env)
            # FEEL chooses the then branch only for true; otherwise the else.
            return self.run(node[2] if condition is True else node[3], env)
        if kind == "list":
            return [self.run(child, env) for child in node[1]]
        if kind == "context":
            result = {}
            for key, child in node[1]:
                result[key] = self.run(child, {**env, **result})
            return result
        if kind == "get":
            context = self.run(node[1], env)
            return context.get(node[2]) if isinstance(context, dict) else self.error("context access requires a context")
        if kind == "index":
            values, index = self.run(node[1], env), self.run(node[2], env)
            if not isinstance(values, list) or not isinstance(index, Decimal) or index != index.to_integral_value():
                return self.error("only integer FEEL list indexing is supported; filters are unsupported")
            i = int(index)
            if i == 0 or abs(i) > len(values):
                return None
            return values[i - 1 if i > 0 else i]
        if kind == "range":
            low, high = self.run(node[1], env), self.run(node[2], env)
            if type(low) is not type(high) or type(low) not in (Decimal, date, str) or low > high:
                return self.error("invalid or incompatible FEEL range bounds")
            return Range(low, high, node[3], node[4])
        if kind == "call":
            return invoke(node[1], [self.run(child, env) for child in node[2]])
        if kind == "unary":
            value = self.run(node[2], env)
            if value is None:
                return None
            if not isinstance(value, Decimal):
                return self.error("numeric unary operator requires a number")
            return -value if node[1] == "-" else value
        if kind == "binary":
            op = node[1]
            a, b = self.run(node[2], env), self.run(node[3], env)
            if op in {"and", "or"}:
                return logic(op, a, b)
            if op in {"=", "!="}:
                result = equal(a, b)
                return (not result) if op == "!=" and result is not None else result
            if op == "in":
                if isinstance(b, Range):
                    return b.contains(a)
                if isinstance(b, list):
                    result = False
                    for value in b:
                        result = logic("or", result, equal(a, value))
                    return result
                return equal(a, b)
            if a is None or b is None:
                return None
            if op in {"<", "<=", ">", ">="}:
                if type(a) is not type(b) or type(a) not in (Decimal, date, str):
                    return self.error("ordered comparison requires compatible scalars")
                return {"<": lambda: a < b, "<=": lambda: a <= b,
                        ">": lambda: a > b, ">=": lambda: a >= b}[op]()
            if op == "+" and isinstance(a, str) and isinstance(b, str):
                if len(a + b) > 4096:
                    return self.error("string exceeds size limit")
                return a + b
            if not isinstance(a, Decimal) or not isinstance(b, Decimal):
                return self.error("arithmetic requires numbers")
            if op == "**" and (b != b.to_integral_value() or abs(b) > 100):
                return self.error("power requires an integer exponent from -100 to 100")
            return {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b,
                    "/": lambda: a / b, "**": lambda: a ** int(b)}[op]()
        raise FeelError("unsupported FEEL node")


class Expression:
    def __init__(self, text, names=()):
        self.text = text
        self.tree = compile_expression(text, names)
        self.references = frozenset(references(self.tree))

    def evaluate(self, variables=None):
        interpreter = Interpreter()
        try:
            env = normalize({} if variables is None else variables)
            if not isinstance(env, dict):
                raise FeelError("FEEL variables must be a context")
            with localcontext(DECIMAL):
                value = interpreter.run(self.tree, env)
            return Evaluation(value, tuple(interpreter.errors))
        except (ValueError, TypeError, DecimalException) as exc:
            return Evaluation(None, tuple(interpreter.errors) + (str(exc),))


def evaluate_checked(text, variables=None):
    return Expression(text, (variables or {}).keys()).evaluate(variables)


def evaluate(text, variables=None):
    """FEEL null-on-evaluation-error API; decisions use checked diagnostics."""
    return evaluate_checked(text, variables).value
