"""Conservative references for already introduced, unambiguous expressions.

Uses meaning identities, never output string replacement. Ambiguous references
retain their full noun phrases. Base expressions retain their meaning.
"""
from collections import defaultdict

from pydantic import TypeAdapter
from simplenlg.features import Feature, Form, Tense
from simplenlg.framework import StringElement

from .detail_plan import key
from .renderer import ARITHMETIC, CompositionalRealiser
from .schema import Aggregate, Arithmetic, Expression, Relation, Series


def expressions(tree):
    if isinstance(tree, dict):
        if tree.get("node") in ("Property", "Series", "Aggregate", "Arithmetic", "Quantity", "Text"):
            yield TypeAdapter(Expression).validate_python(tree)
        for value in tree.values():
            yield from expressions(value)
    elif isinstance(tree, list):
        for value in tree:
            yield from expressions(value)


def reference(expr):
    if isinstance(expr, Series):
        return "these observations"
    if isinstance(expr, Aggregate):
        return {"sum": "that total", "average": "that average", "minimum": "that minimum", "maximum": "that maximum"}[expr.fn]
    if isinstance(expr, Arithmetic):
        return {"add": "that sum", "subtract": "that difference", "multiply": "that product", "divide": "that ratio"}[expr.op]
    return None


class DiscourseRealiser(CompositionalRealiser):
    def __init__(self, base, main, steps):
        self.factory = base.factory
        self.realiser = base.realiser
        self.known = set()
        candidates = defaultdict(set)
        for tree in [main] + [step.meaning for step in steps]:
            for expr in expressions(tree):
                label = reference(expr)
                if label:
                    candidates[label].add(key(expr))
        self.references = {next(iter(ids)): label for label, ids in candidates.items() if len(ids) == 1}
        self.establish(main)

    def establish(self, tree):
        self.known.update(key(expr) for expr in expressions(tree))

    def expression(self, domain, expr):
        identity = key(expr)
        if identity in self.known and identity in self.references:
            return StringElement(self.references[identity])
        return super().expression(domain, expr)

    def statement(self, domain, stmt, invert=False):
        if isinstance(stmt, Relation) and stmt.op == "eq" and isinstance(stmt.left, Arithmetic) and not invert:
            expr = stmt.left
            verb, prep = ARITHMETIC[expr.op]
            first, second = (expr.right, expr.left) if expr.op == "subtract" else (expr.left, expr.right)
            action = self.factory.createClause(None, verb, self.expression(domain, first))
            action.setFeature(Feature.FORM, Form.GERUND)
            action.addComplement(self.pp(prep, self.expression(domain, second)))
            clause = self.factory.createClause(action, "give", self.expression(domain, stmt.right))
            clause.setFeature(Feature.TENSE, Tense.PAST)
            return clause
        return super().statement(domain, stmt, invert)
