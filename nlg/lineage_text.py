"""Shared pySimpleNLG constructions for validated lineage/reporting metadata."""
import json

from pydantic import TypeAdapter
from simplenlg.features import Feature, Form, Tense
from simplenlg.framework import StringElement

from .schema import Arithmetic, Expression, Quantity, Relation
from .semantics import Evaluator


def metadata_clause(nlg, subject, value, present=False):
    clause = nlg.factory.createClause(subject, "be", StringElement(value))
    clause.setFeature(Feature.TENSE, Tense.PRESENT if present else Tense.PAST)
    return nlg.realiser.realiseSentence(clause)


def render_lineage(nlg, domain, step, facts):
    meaning = step.meaning
    if step.kind == "derivation":
        expr = TypeAdapter(Expression).validate_python(meaning["expression"])
        # Flatten a left-associated subtraction chain while preserving its order
        # and exact checked value: (((a-b)-c)-d) = a-(b+c+d).
        rights = []
        current = expr
        while isinstance(current, Arithmetic) and current.op == "subtract":
            rights.append(current.right)
            current = current.left
        if rights:
            operands = [current] + list(reversed(rights))
            numbers = [Evaluator(domain, facts).expression(x).value for x in operands]
            values = [nlg.expression(domain, Quantity(node="Quantity", value=str(v.numerator), unit=meaning["unit"]))
                      if v.denominator == 1 else nlg.expression(domain, x) for x, v in zip(operands, numbers)]
            action = nlg.factory.createClause(None, "subtract", nlg.coordinate(values[1:]))
            action.addComplement(nlg.pp("from", values[0]))
            action.setFeature(Feature.FORM, Form.GERUND)
            result = nlg.expression(domain, Quantity(node="Quantity", value=meaning["value"], unit=meaning["unit"]))
            clause = nlg.factory.createClause(action, "give", result)
            clause.setFeature(Feature.TENSE, Tense.PAST)
            return nlg.realiser.realiseSentence(clause)
        statement = Relation(node="Relation", op="eq", left=expr,
                             right=Quantity(node="Quantity", value=meaning["value"], unit=meaning["unit"]))
        return nlg.realiser.realiseSentence(nlg.statement(domain, statement))
    if step.kind == "reporting":
        selected, earliest = meaning["selected"], meaning["earliest"]
        def value(record):
            return nlg.realiser.realise(nlg.expression(domain, Quantity(node="Quantity", value=record["value"], unit=record["unit"]))).getRealisation()
        # Values, periods, document dates and classification remain distinct.
        lines = [metadata_clause(nlg, "the reporting period", f"{selected['period_start']} to {selected['period_end']}"),
                 metadata_clause(nlg, "the selected observation", f"{value(selected)} from {selected['document_type']} {selected['accession']}, filed {selected['reported_on']}"),
                 metadata_clause(nlg, "the selection policy", {"latest_cached": "latest cached observation",
                     "earliest_cached": "earliest cached observation", "selected_snapshot": "selected snapshot"}[meaning["policy"]]),
                 metadata_clause(nlg, "the earliest cached observation", f"{value(earliest)} from {earliest['document_type']} {earliest['accession']}, filed {earliest['reported_on']}")]
        if selected["measure"] != earliest["measure"]:
            lines.extend([metadata_clause(nlg, "the selected accounting measure", json.dumps(selected["measure"])),
                          metadata_clause(nlg, "the earliest accounting measure", json.dumps(earliest["measure"]))])
        status = "explicitly confirmed" if meaning["restatement"] == "explicitly_confirmed" else "not established"
        lines.append(metadata_clause(nlg, "the restatement status", status, present=True))
        for record in meaning.get("review_observations", []):
            lines.append(metadata_clause(nlg, "the source-context observation",
                         f"{value(record)} for {record['measure']} from {record['document_type']} {record['accession']}, filed {record['reported_on']}"))
        if meaning.get("review"):
            lines.append(metadata_clause(nlg, "the source-context review", json.dumps(meaning["review"])))
        return " ".join(lines)
    raise ValueError("unsupported lineage step")
