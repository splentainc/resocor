"""Deterministic content selection over checked meanings; no domain imports.

Every added assertion is evaluated against the same facts before it is emitted.
Metadata steps describe only selected observations/source records. This module
does not choose business explanations or retrieve additional facts.
"""
from dataclasses import asdict, dataclass
import json

from .schema import (Aggregate, And, Arithmetic, Domain, Fact, Not, Property,
                     Quantity, Relation, Series, Statement, Text, Unit)
from .semantics import Evaluator, Value, check, dimensions, numeric

LEVELS = ("crisp", "detailed", "audit")


def key(model):
    return json.dumps(model.model_dump(), sort_keys=True)


def decimal_text(value):
    """An exact finite decimal within the existing meaning language, or None."""
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    places = max(twos, fives)
    if denominator != 1 or places > 8:
        return None
    if not places:
        return str(value.numerator)
    scaled = abs(value.numerator) * (10 ** places // value.denominator)
    digits = str(scaled).zfill(places + 1)
    return ("-" if value < 0 else "") + digits[:-places] + "." + digits[-places:]


@dataclass
class Step:
    kind: str
    meaning: dict
    evidence: list[str]

    def to_dict(self):
        return asdict(self)


class DetailPlanner:
    def __init__(self, domain: Domain, facts: list[Fact], selected_ids, lineage=None):
        self.domain = domain
        self.facts = facts
        self.selected_ids = set(selected_ids)
        self.lineage = lineage
        self.supporting_ids = {f.id for f in lineage.supporting} if lineage else set()
        if lineage:
            self.facts = list(lineage.pool.values())
        self.steps = []
        self.omitted = []
        self.seen = set()
        self.assertions = set()
        self.coverage = set()
        self.scalar = "__detail_scalar__"
        while self.scalar in self.domain.units:
            self.scalar += "_"
        # A canonical dimensionless number for counts/denominators; no unit
        # conversion or domain-specific vocabulary is introduced.
        self.domain = self.domain.model_copy(update={"units": {**self.domain.units,
                     self.scalar: Unit(dimension={}, grouping=False)}})

    def evaluate(self, expr):
        return Evaluator(self.domain, self.facts).expression(expr)

    def unit_for(self, expr, value, preferred=None):
        if preferred and dimensions(self.domain.units[preferred]) == value.dimension:
            return preferred
        if isinstance(expr, Property):
            return self.domain.metrics[expr.metric].unit
        if isinstance(expr, Aggregate):
            return self.domain.metrics[expr.over.metric].unit
        return next((name for name, unit in self.domain.units.items() if dimensions(unit) == value.dimension), None)

    def literal(self, expr, value, preferred=None):
        if isinstance(value.value, str):
            return Text(node="Text", value=value.value)
        unit = self.unit_for(expr, value, preferred)
        check(unit is not None, "no declared unit for this derived dimension")
        finite = decimal_text(value.value)
        if finite is not None:
            numeric(finite)
            return Quantity(node="Quantity", value=finite, unit=unit)
        # Exact fractions are expressed compositionally, never silently rounded.
        numerator, denominator = str(value.value.numerator), str(value.value.denominator)
        numeric(numerator)
        numeric(denominator)
        return Arithmetic(node="Arithmetic", op="divide", left=Quantity(node="Quantity", value=numerator, unit=unit),
                          right=Quantity(node="Quantity", value=denominator, unit=self.scalar))

    def add_assertion(self, expr, kind="calculation", preferred=None):
        try:
            value = self.evaluate(expr)
            check(isinstance(value, Value) and value.evidence, "detail assertion needs scalar source support")
            allowed = self.selected_ids | (self.supporting_ids if kind == "lineage_evidence" else set())
            check(set(value.evidence) <= allowed, "detail expansion escaped selected evidence")
            identity = key(expr)
            if identity in self.assertions:
                return
            statement = Relation(node="Relation", op="eq", left=expr, right=self.literal(expr, value, preferred))
            check(Evaluator(self.domain, self.facts).statement(statement), "unsupported detail assertion")
            self.assertions.add(identity)
            self.steps.append(Step(kind, statement.model_dump(), list(dict.fromkeys(value.evidence))))
        except ValueError as exc:
            # Keep the already verified main answer if an optional numeric step
            # exceeds the finite display algebra; record the omission explicitly.
            self.omitted.append({"expression": expr.model_dump(), "reason": str(exc)})

    def expression(self, expr, preferred=None):
        if key(expr) in self.seen:
            return
        self.seen.add(key(expr))
        if isinstance(expr, Property):
            self.add_assertion(expr, preferred=preferred)
        elif isinstance(expr, Arithmetic):
            self.expression(expr.left)
            self.expression(expr.right)
            self.add_assertion(expr, preferred=preferred)
        elif isinstance(expr, Aggregate):
            values = self.evaluate(expr.over)
            ids = list(dict.fromkeys(i for v in values for i in v.evidence))
            check(set(ids) <= self.selected_ids, "coverage escaped selected evidence")
            if key(expr.over) not in self.coverage:
                self.coverage.add(key(expr.over))
                self.steps.append(Step("coverage", {"expression": expr.model_dump(), "count": len(values)}, ids))
            if expr.fn == "average" and self.domain.metrics[expr.over.metric].summable:
                total = Aggregate(node="Aggregate", fn="sum", over=expr.over)
                self.add_assertion(total)
                division = Arithmetic(node="Arithmetic", op="divide", left=total,
                                      right=Quantity(node="Quantity", value=str(len(values)), unit=self.scalar))
                self.add_assertion(division, preferred=preferred)
            self.add_assertion(expr, preferred=preferred)

    def statement(self, stmt):
        if isinstance(stmt, Relation):
            self.expression(stmt.left, stmt.right.unit if isinstance(stmt.right, Quantity) else None)
            self.expression(stmt.right, stmt.left.unit if isinstance(stmt.left, Quantity) else None)
        elif isinstance(stmt, Not):
            self.statement(stmt.expr)
        elif isinstance(stmt, And):
            for child in stmt.items:
                self.statement(child)

    def already_stated(self, stmt, invert=False):
        """Avoid repeating literal values that the main sentence already states."""
        if isinstance(stmt, Not):
            self.already_stated(stmt.expr, not invert)
        elif isinstance(stmt, And) and not invert:
            for child in stmt.items:
                self.already_stated(child)
        elif isinstance(stmt, Relation) and stmt.op == "eq" and not invert:
            if isinstance(stmt.right, (Quantity, Text)):
                self.assertions.add(key(stmt.left))
            if isinstance(stmt.left, (Quantity, Text)):
                self.assertions.add(key(stmt.right))

    def plan(self, statement: Statement, level, truth=True):
        check(level in LEVELS, "unknown detail level")
        if level == "crisp":
            return []
        self.already_stated(statement, invert=not truth)
        self.statement(statement)
        if not self.steps:
            self.steps.append(Step("support", {"count": len(self.selected_ids)}, sorted(self.selected_ids)))
            self.steps.append(Step("provenance", {"value": self.domain.provenance}, sorted(self.selected_ids)))
        if level == "audit":
            for fact in self.facts:
                if fact.id not in self.selected_ids:
                    continue
                expr = Property(node="Property", entity=fact.entity, metric=fact.metric,
                                scope=fact.scope, qualifiers=fact.qualifiers)
                self.add_assertion(expr, kind="evidence")
                self.steps.append(Step("source", {"expression": expr.model_dump(), "id": fact.id,
                                                 "source": fact.source}, [fact.id]))
            if self.lineage:
                for fact in self.lineage.supporting:
                    expr = Property(node="Property", entity=fact.entity, metric=fact.metric,
                                    scope=fact.scope, qualifiers=fact.qualifiers)
                    self.add_assertion(expr, kind="lineage_evidence")
                    self.steps.append(Step("lineage_source", {"expression": expr.model_dump(), "id": fact.id,
                                                             "source": fact.source}, [fact.id]))
                for derivation in self.lineage.derivations:
                    self.steps.append(Step("derivation", derivation, derivation["evidence"]))
                for report in self.lineage.reports:
                    if report["selected"] != report["earliest"] or report.get("review"):
                        self.steps.append(Step("reporting", report, [report["fact_id"]]))
            self.steps.append(Step("provenance", {"value": self.domain.provenance}, sorted(self.selected_ids)))
        return self.steps
