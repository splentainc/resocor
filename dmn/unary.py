"""Compile FEEL decision-table unary tests, with three-valued matching."""
from decimal import localcontext

from .feel import Evaluation, Interpreter, Range
from .parser import Parser, references
from .values import DECIMAL, FeelError, equal, logic, normalize


def part(parser):
    token = parser.peek()
    if token == "-" and parser.items[parser.pos + 1] in {",", ")", "<end>"}:
        parser.take()
        return ("any",)
    if token == "not" and parser.items[parser.pos + 1] == "(":
        parser.take()
        parser.take("(")
        children = parts(parser)
        parser.take(")")
        return ("not", children)
    if token in {"<", "<=", ">", ">=", "=", "!="}:
        parser.take()
        return ("predicate", ("binary", token, ("name", "?"), parser.expression()))
    tree = parser.expression()
    return ("predicate" if "?" in references(tree) else "value", tree)


def parts(parser):
    result = [part(parser)]
    while parser.peek() == ",":
        parser.take()
        result.append(part(parser))
    return tuple(result)


class UnaryTest:
    def __init__(self, text, names=()):
        parser = Parser(text, names)
        self.parts = parts(parser)
        if parser.peek() != "<end>":
            raise FeelError("unsupported or trailing unary-test syntax: " + parser.peek())
        self.references = frozenset(self._refs(self.parts)) - {"?"}

    def _refs(self, nodes):
        return set().union(*(self._refs(p[1]) if p[0] == "not" else references(p[1]) if p[0] != "any" else set() for p in nodes))

    def evaluate(self, value, variables=None):
        interpreter = Interpreter()
        try:
            env = normalize({**(variables or {}), "?": value})
            with localcontext(DECIMAL):
                result = self._run(self.parts, interpreter, env)
            return Evaluation(result, tuple(interpreter.errors))
        except (ValueError, TypeError) as exc:
            return Evaluation(None, (str(exc),))

    def _run(self, nodes, interpreter, env):
        result = False
        for node in nodes:
            if node[0] == "any":
                value = True
            elif node[0] == "not":
                inner = self._run(node[1], interpreter, env)
                value = not inner if type(inner) is bool else None
            else:
                found = interpreter.run(node[1], env)
                if node[0] == "predicate":
                    value = found if type(found) is bool else None
                elif isinstance(found, Range):
                    value = found.contains(env["?"])
                elif isinstance(found, list):
                    value = False
                    for candidate in found:
                        value = logic("or", value, equal(env["?"], candidate))
                else:
                    value = equal(env["?"], found)
            result = logic("or", result, value)
        return result

    def boundaries(self):
        """Exact partition endpoints for constant scalar tests; else unsupported."""
        if self.references:
            raise FeelError("cross-input unary tests need case-time matching")
        result = []
        def visit(nodes):
            for node in nodes:
                kind = node[0]
                if kind == "any":
                    continue
                if kind == "not":
                    visit(node[1])
                    continue
                expr = node[1]
                if kind == "predicate":
                    if expr[0] != "binary" or expr[1] not in {"<", "<=", ">", ">=", "=", "!="} or expr[2] != ("name", "?") or references(expr[3]):
                        raise FeelError("computed predicates need case-time matching")
                    expr = expr[3]
                interpreter = Interpreter()
                with localcontext(DECIMAL):
                    value = interpreter.run(expr, {})
                if interpreter.errors:
                    raise FeelError("invalid constant test")
                if isinstance(value, Range):
                    result.extend([value.low, value.high])
                elif isinstance(value, list):
                    result.extend(value)
                else:
                    result.append(value)
        visit(self.parts)
        return result
