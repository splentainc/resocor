"""Evaluate supplied meanings independently of their English realisation.

Facts/schema are trusted inputs; identity, coverage, dimensions and asserted
results are checked here. Exact Fraction arithmetic prevents hidden rounding.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction

from .schema import (Aggregate, And, Arithmetic, Domain, Fact, Not, Property,
                     Quantity, Relation, Series, Text)


class Rejected(ValueError):
    pass


def check(ok, reason):
    if not ok:
        raise Rejected(reason)


def numeric(text):
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise Rejected("invalid numeric value") from None
    check(value.is_finite() and abs(value) < Decimal("1e24") and value.as_tuple().exponent >= -8,
          "non-finite, oversized or overprecise number")
    return Fraction(value)


def dimensions(unit):
    return tuple(sorted((key, power) for key, power in unit.dimension.items() if power))


@dataclass(frozen=True)
class Value:
    value: Fraction | str
    dimension: tuple
    evidence: tuple[str, ...] = ()


class Evaluator:
    def __init__(self, domain: Domain, facts: list[Fact]):
        self.domain = domain
        self.facts = facts
        check(len({f.id for f in facts}) == len(facts), "duplicate evidence IDs")
        self.trace = []

    def metric(self, key, qualifiers):
        check(key in self.domain.metrics, "unknown metric")
        metric = self.domain.metrics[key]
        check(len(set(qualifiers)) == len(qualifiers) and set(qualifiers) <= metric.qualifiers.keys(),
              "duplicate or unsupported qualifiers")
        check((metric.unit is None) == (metric.kind == "text"), "invalid metric unit declaration")
        if metric.unit:
            check(metric.unit in self.domain.units, "unknown metric unit")
        return metric

    def group(self, series):
        check(series.group in self.domain.groups, "unknown group")
        group = self.domain.groups[series.group]
        if group.context:
            check(group.context in self.domain.scopes, "unknown group context")
        if group.axis == "scope":
            check(series.entity in self.domain.entities, "unknown series entity")
            check(set(group.members) <= self.domain.scopes.keys(), "unknown group member scope")
        else:
            check(series.entity is None, "entity group cannot also select one entity")
            check(set(group.members) <= self.domain.entities.keys(), "unknown group member entity")
        return group

    def expression(self, expr):
        if isinstance(expr, Property):
            check(expr.entity in self.domain.entities, "unknown entity")
            check(expr.scope is None or expr.scope in self.domain.scopes, "unknown scope")
            metric = self.metric(expr.metric, expr.qualifiers)
            found = [f for f in self.facts if (f.entity, f.metric, f.scope, sorted(f.qualifiers)) ==
                     (expr.entity, expr.metric, expr.scope, sorted(expr.qualifiers))]
            check(len(found) == 1, "missing or ambiguous evidence")
            fact = found[0]
            value = numeric(fact.value) if metric.kind == "number" else fact.value
            dim = dimensions(self.domain.units[metric.unit]) if metric.unit else (("text", 1),)
            out = Value(value, dim, (fact.id,))
        elif isinstance(expr, Quantity):
            check(expr.unit in self.domain.units, "unknown quantity unit")
            out = Value(numeric(expr.value), dimensions(self.domain.units[expr.unit]))
        elif isinstance(expr, Text):
            out = Value(expr.value, (("text", 1),))
        elif isinstance(expr, Series):
            group = self.group(expr)
            self.metric(expr.metric, expr.qualifiers)
            props = [Property(node="Property", entity=expr.entity if group.axis == "scope" else key,
                              metric=expr.metric, scope=key if group.axis == "scope" else group.context,
                              qualifiers=expr.qualifiers) for key in group.members]
            return [self.expression(prop) for prop in props]
        elif isinstance(expr, Aggregate):
            values = self.expression(expr.over)
            check(all(isinstance(v.value, Fraction) for v in values), "aggregation requires numbers")
            check(len({v.dimension for v in values}) == 1, "mixed dimensions in series")
            check(expr.fn != "sum" or self.domain.metrics[expr.over.metric].summable,
                  "metric does not permit summation")
            numbers = [v.value for v in values]
            value = {"sum": lambda: sum(numbers), "average": lambda: sum(numbers) / len(numbers),
                     "minimum": lambda: min(numbers), "maximum": lambda: max(numbers)}[expr.fn]()
            out = Value(value, values[0].dimension, tuple(i for v in values for i in v.evidence))
        elif isinstance(expr, Arithmetic):
            left, right = self.expression(expr.left), self.expression(expr.right)
            check(isinstance(left, Value) and isinstance(right, Value), "arithmetic operands must be scalar")
            check(isinstance(left.value, Fraction) and isinstance(right.value, Fraction), "arithmetic requires numbers")
            if expr.op in ("add", "subtract"):
                check(left.dimension == right.dimension, "arithmetic unit mismatch")
                dim = left.dimension
                value = left.value + right.value if expr.op == "add" else left.value - right.value
            else:
                check(expr.op != "divide" or right.value != 0, "division by zero")
                powers = dict(left.dimension)
                for k, v in right.dimension:
                    powers[k] = powers.get(k, 0) + (v if expr.op == "multiply" else -v)
                dim = tuple(sorted((k, v) for k, v in powers.items() if v))
                value = left.value * right.value if expr.op == "multiply" else left.value / right.value
            out = Value(value, dim, left.evidence + right.evidence)
        else:
            raise Rejected("unsupported expression")
        self.trace.append({"node": expr.node, "expression": expr.model_dump(), "value": str(out.value),
                           "dimension": list(out.dimension), "evidence": list(out.evidence)})
        return out

    def statement(self, stmt):
        if isinstance(stmt, Relation):
            left, right = self.expression(stmt.left), self.expression(stmt.right)
            check(isinstance(left, Value) and isinstance(right, Value), "relation operands must be scalar")
            check(left.dimension == right.dimension, "comparison unit mismatch")
            check(bool(left.evidence + right.evidence), "statement must be grounded in evidence")
            check(stmt.op == "eq" or isinstance(left.value, Fraction), "ordered comparison requires numbers")
            result = {"eq": lambda: left.value == right.value, "gt": lambda: left.value > right.value,
                      "lt": lambda: left.value < right.value}[stmt.op]()
        elif isinstance(stmt, Not):
            result = not self.statement(stmt.expr)
        elif isinstance(stmt, And):
            results = [self.statement(x) for x in stmt.items]  # never short-circuit missing evidence
            result = all(results)
        else:
            raise Rejected("unsupported statement")
        self.trace.append({"node": stmt.node, "result": result})
        return result
