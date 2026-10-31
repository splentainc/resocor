"""Bounded Pratt parser for a documented FEEL subset. Never uses eval/exec."""
import json
import re

from .functions import NAMES
from .values import FeelError, number

KEYWORDS = {"if", "then", "else", "and", "or", "in", "between", "true", "false", "null"}
PRECEDENCE = {"or": 10, "and": 20, "=": 30, "!=": 30, "<": 30, "<=": 30,
              ">": 30, ">=": 30, "in": 30, "between": 30,
              "+": 40, "-": 40, "*": 50, "/": 50, "**": 60}
TOKEN = re.compile(r'\s+|"(?:[^"\\]|\\.)*"|\d+(?:\.(?!\.)\d+)?|<=|>=|!=|\*\*|\.\.|[+*/=<>(),\[\]{}:.?\-]|[A-Za-z_][A-Za-z_0-9]*')


def tokens(text, names=()):
    if not isinstance(text, str) or not text.strip() or len(text) > 4096:
        raise FeelError("FEEL expression requires 1 to 4096 characters")
    # Known multiword variables/functions use longest-match tokenization.
    phrases = sorted({n for n in set(names) | NAMES if " " in n}, key=len, reverse=True)
    result, position = [], 0
    while position < len(text):
        phrase = next((n for n in phrases if text.startswith(n, position)
                       and (position + len(n) == len(text) or not re.match(r"[A-Za-z_0-9]", text[position + len(n)]))), None)
        if phrase:
            result.append(phrase)
            position += len(phrase)
            continue
        match = TOKEN.match(text, position)
        if not match:
            raise FeelError("unsupported FEEL syntax at character " + str(position))
        value = match[0]
        position = match.end()
        if not value.isspace():
            result.append(value)
        if len(result) > 512:
            raise FeelError("FEEL expression exceeds token limit")
    return result


class Parser:
    def __init__(self, text, names=()):
        self.items = tokens(text, names) + ["<end>"]
        self.pos = 0
        self.depth = 0

    def peek(self):
        return self.items[self.pos]

    def take(self, expected=None):
        value = self.peek()
        if expected is not None and expected != value:
            raise FeelError("expected " + expected + ", found " + value)
        if value == "<end>":
            raise FeelError("unexpected end of FEEL expression")
        self.pos += 1
        return value

    def expression(self, minimum=0, range_bound=False):
        self.depth += 1
        if self.depth > 32:
            raise FeelError("FEEL expression exceeds nesting limit")
        node = self.prefix()
        while True:
            op = self.peek()
            if op == ".":
                self.take()
                node = ("get", node, self.take())
                continue
            if op == "[" and range_bound:
                break
            if op == "[" and self.items[self.pos + 1] in {"<end>", ",", ")", "]", "and", "or", "then", "else"}:
                break
            if op == "[":
                self.take()
                index = self.expression()
                self.take("]")
                node = ("index", node, index)
                continue
            power = PRECEDENCE.get(op, -1)
            if power < minimum:
                break
            self.take()
            right = self.expression(power if op == "**" else power + 1)
            if op == "between":
                self.take("and")
                right = ("range", right, self.expression(power + 1), True, True)
                op = "in"
            node = ("binary", op, node, right)
        self.depth -= 1
        return node

    def prefix(self):
        token = self.take()
        if token.startswith('"'):
            return ("literal", json.loads(token))
        if token[0].isdigit():
            return ("literal", number(token))
        if token in {"true", "false", "null"}:
            return ("literal", {"true": True, "false": False, "null": None}[token])
        if token in {"+", "-"}:
            return ("unary", token, self.expression(60))
        if token == "if":
            condition = self.expression()
            self.take("then")
            yes = self.expression()
            self.take("else")
            return ("if", condition, yes, self.expression())
        if token in {"[", "(", "]"}:
            if self.peek() == "]" and token == "[":
                self.take()
                return ("list", ())
            first = self.expression()
            if self.peek() == "..":
                self.take()
                last = self.expression(range_bound=True)
                end = self.take()
                if end not in {"]", ")", "["}:
                    raise FeelError("expected end of FEEL range")
                return ("range", first, last, token == "[", end == "]")
            if token == "(":
                self.take(")")
                return first
            if token != "[":
                raise FeelError("unsupported interval syntax")
            items = [first]
            while self.peek() == ",":
                self.take()
                items.append(self.expression())
            self.take("]")
            return ("list", tuple(items))
        if token == "{":
            pairs = []
            while self.peek() != "}":
                key = self.take()
                key = json.loads(key) if key.startswith('"') else key
                if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9 ]*", key) or key in {p[0] for p in pairs}:
                    raise FeelError("context keys must be unique supported names")
                self.take(":")
                pairs.append((key, self.expression()))
                if self.peek() != ",":
                    break
                self.take()
            self.take("}")
            return ("context", tuple(pairs))
        if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9 ]*|\?", token) and token not in KEYWORDS:
            if self.peek() != "(":
                return ("name", token)
            if token not in NAMES:
                raise FeelError("unsupported FEEL function: " + token)
            self.take()
            args = []
            while self.peek() != ")":
                args.append(self.expression())
                if self.peek() != ",":
                    break
                self.take()
            self.take(")")
            return ("call", token, tuple(args))
        raise FeelError("unsupported FEEL construct: " + token)


def compile_expression(text, names=()):
    parser = Parser(text, names)
    tree = parser.expression()
    if parser.peek() != "<end>":
        raise FeelError("unsupported or trailing FEEL syntax: " + parser.peek())
    return tree


def references(node, bound=frozenset()):
    kind = node[0]
    if kind == "name":
        return {node[1]} - bound
    if kind == "literal":
        return set()
    if kind == "context":
        result = set()
        for key, value in node[1]:
            result |= references(value, bound)
            bound = bound | {key}
        return result
    if kind in {"list", "call"}:
        children = node[-1]
    elif kind == "binary":
        children = node[2:]
    elif kind == "unary":
        children = node[2:]
    elif kind == "range":
        children = node[1:3]
    elif kind == "get":
        children = node[1:2]
    else:
        children = node[1:]
    return set().union(*(references(child, bound) for child in children))
