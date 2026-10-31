"""Domain-neutral validation of explicitly supplied derivations/report history.

Reporting histories are trusted source metadata, not authenticated filings.
A later or changed observation is never itself evidence of a restatement.
"""
from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from .schema import Expression, Fact, Strict, bounded
from .semantics import Evaluator, Value, check, dimensions, numeric


class Observation(Strict):
    value: str
    unit: str
    period_start: str
    period_end: str
    reported_on: str
    document_type: str
    accession: str
    source: str
    measure: str = "not established"

    @model_validator(mode="after")
    def dates(self):
        numeric(self.value)
        start, end, reported = map(date.fromisoformat, (self.period_start, self.period_end, self.reported_on))
        if start > end or reported < end:
            raise ValueError("invalid reporting-period/filing dates")
        return self


class Reporting(Strict):
    policy: Literal["earliest_cached", "latest_cached", "selected_snapshot"]
    selected: Observation
    earliest: Observation
    restatement: Literal["not_established", "explicitly_confirmed"] = "not_established"
    restatement_evidence: list[str] = Field(default_factory=list)
    compatibility: Literal["unverified", "same_measure", "different_measures"] = "unverified"
    review: str = ""
    review_sources: list[str] = Field(default_factory=list)
    review_observations: list[Observation] = Field(default_factory=list)

    @model_validator(mode="after")
    def history(self):
        a, b = self.selected, self.earliest
        if (a.period_start, a.period_end, a.unit) != (b.period_start, b.period_end, b.unit):
            raise ValueError("reporting history compares different periods or units")
        if a.reported_on < b.reported_on:
            raise ValueError("selected observation precedes earliest cached observation")
        if self.restatement == "explicitly_confirmed" and (not self.restatement_evidence or not all(x.strip() for x in self.restatement_evidence)):
            raise ValueError("restatement assertion requires explicit supporting evidence")
        for other in self.review_observations:
            if (other.period_start, other.period_end, other.unit) != (a.period_start, a.period_end, a.unit):
                raise ValueError("source-context observation has a different period or unit")
        return self


class Derivation(Strict):
    expression: Expression
    dependencies: list[Fact] = Field(min_length=1, max_length=32)


class Lineage:
    def __init__(self, domain, facts, selected_ids):
        self.domain = domain
        self.primary = {f.id: f for f in facts}
        self.pool = dict(self.primary)
        self.selected = set(selected_ids)
        self.visiting = set()
        self.visited = set()
        self.derivations = []
        self.reports = []
        for identity in sorted(self.selected):
            self.visit(identity)

    def visit(self, identity):
        check(identity not in self.visiting, "cyclic derivation lineage")
        if identity in self.visited:
            return
        check(len(self.visiting) < 16 and len(self.pool) <= 128, "lineage exceeds bounds")
        fact = self.pool[identity]
        check(fact.entity in self.domain.entities and fact.metric in self.domain.metrics, "unknown lineage fact identity")
        self.visiting.add(identity)
        if "reporting" in fact.source_record:
            report = Reporting.model_validate(fact.source_record["reporting"])
            metric = self.domain.metrics[fact.metric]
            check(metric.kind == "number" and metric.unit in self.domain.units, "reporting history requires a declared numeric unit")
            check(report.selected.unit == metric.unit, "reporting unit disagrees with fact")
            check(numeric(report.selected.value) == numeric(fact.value), "selected report value disagrees with fact")
            check(report.selected.source == fact.source, "selected report source disagrees with fact")
            if "valid_from" in fact.source_record:
                check(report.selected.period_start == fact.source_record["valid_from"], "reporting period disagrees with fact")
            self.reports.append({"fact_id": identity, **report.model_dump()})
        if "derivation" in fact.source_record:
            raw = fact.source_record["derivation"]
            check(isinstance(raw, dict) and bounded(raw.get("expression")) <= 2048, "invalid derivation")
            derivation = Derivation.model_validate(raw)
            ids = [f.id for f in derivation.dependencies]
            check(len(ids) == len(set(ids)) and identity not in ids, "duplicate/self derivation operands")
            for dep in derivation.dependencies:
                if dep.id in self.pool:
                    check(dep.model_dump() == self.pool[dep.id].model_dump(), "conflicting lineage record identity")
                else:
                    self.pool[dep.id] = dep
            for dep in derivation.dependencies:
                self.visit(dep.id)
            evaluator = Evaluator(self.domain, list(self.pool.values()))
            value = evaluator.expression(derivation.expression)
            check(isinstance(value, Value), "derivation must be scalar")
            metric = self.domain.metrics[fact.metric]
            check(metric.kind == "number" and metric.unit in self.domain.units, "derivation requires a declared numeric unit")
            check(value.value == numeric(fact.value) and value.dimension == dimensions(self.domain.units[metric.unit]),
                  "derived value or unit disagrees with stored fact")
            check(set(value.evidence) == set(ids), "derivation operands do not match declared dependencies")
            self.derivations.append({"fact_id": identity, "expression": derivation.expression.model_dump(),
                                     "value": fact.value, "unit": metric.unit, "evidence": ids})
        self.visiting.remove(identity)
        self.visited.add(identity)

    @property
    def unresolved(self):
        return any(r["compatibility"] != "same_measure" and
                   (r["compatibility"] == "different_measures" or
                    numeric(r["selected"]["value"]) != numeric(r["earliest"]["value"])) for r in self.reports)

    @property
    def supporting(self):
        return [self.pool[i] for i in sorted(self.visited - self.selected)]
