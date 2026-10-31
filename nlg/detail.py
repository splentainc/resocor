"""Crisp-default detail API over the compositional renderer."""
import json
from time import perf_counter_ns

from simplenlg.features import Feature, NumberAgreement, Tense
from simplenlg.framework import StringElement

from .detail_plan import DetailPlanner, LEVELS
from .renderer import CompositionalRealiser
from .schema import Domain, Fact, Property, Relation, Result


class DetailRealiser:
    def __init__(self, realiser=None):
        self.realiser = realiser or CompositionalRealiser()

    def step(self, domain, step, nlg=None):
        nlg = nlg or self.realiser
        if step.kind in ("calculation", "evidence", "lineage_evidence"):
            return nlg.realiser.realiseSentence(nlg.statement(domain, Relation.model_validate(step.meaning)))
        if step.kind in ("coverage", "support"):
            from pydantic import TypeAdapter
            from .schema import Expression

            subject = nlg.factory.createNounPhrase("the", "calculation" if step.kind == "coverage" else "answer")
            if step.kind == "coverage":
                expr = TypeAdapter(Expression).validate_python(step.meaning["expression"])
                subject.addPostModifier(nlg.pp("for", nlg.expression(domain, expr)))
            obj = nlg.factory.createNounPhrase("observation")
            count = step.meaning["count"]
            obj.setSpecifier(str(count))
            obj.setFeature(Feature.NUMBER, NumberAgreement.SINGULAR if count == 1 else NumberAgreement.PLURAL)
            clause = nlg.factory.createClause(subject, "use", obj)
        elif step.kind in ("source", "lineage_source"):
            subject = nlg.factory.createNounPhrase("the", "source")
            subject.addPostModifier(nlg.pp("for", nlg.expression(domain, Property.model_validate(step.meaning["expression"]))))
            value = json.dumps(f"{step.meaning['id']}: {step.meaning['source']}", ensure_ascii=False)
            clause = nlg.factory.createClause(subject, "be", StringElement(value))
        elif step.kind == "provenance":
            subject = nlg.factory.createNounPhrase("the", "provenance")
            subject.addPreModifier("evidence")
            clause = nlg.factory.createClause(subject, "be", StringElement(json.dumps(step.meaning["value"], ensure_ascii=False)))
        else:
            raise ValueError("unsupported detail step")
        clause.setFeature(Feature.TENSE, Tense.PAST)
        return nlg.realiser.realiseSentence(clause)

    def render(self, bundle, detail="crisp"):
        started = perf_counter_ns()
        if detail not in LEVELS:
            return {"outcome": "abstained", "answer": None, "reason": "unknown detail level", "evidence": []}
        base = self.realiser.render(bundle)
        if base["outcome"] != "answered":
            return {**base, "detail": detail}
        try:
            domain = Domain.model_validate(bundle["domain"])
            facts = [Fact.model_validate(f) for f in bundle["facts"]]
            meaning = Result.model_validate(bundle["input"])
            from .lineage import Lineage
            from .lineage_text import metadata_clause, render_lineage

            lineage = Lineage(domain, facts, [f["id"] for f in base["evidence"]])
            qualification = metadata_clause(self.realiser, "the reporting basis", "unresolved", present=True) if lineage.unresolved else None
            crisp = base["answer"] + (" " + qualification if qualification else "")
            common = {**base, "detail": detail, "crisp_answer": crisp, "base_answer": base["answer"],
                      "reporting_basis_status": "unresolved" if lineage.unresolved else "as_supplied",
                      "result_kind": "stored_data_calculation" if lineage.unresolved else "checked_result"}
            if detail == "crisp":
                return {**common, "answer": crisp, "sentences": [crisp], "detail_plan": [], "omitted_details": []}
            planner = DetailPlanner(domain, facts, [f["id"] for f in base["evidence"]], lineage=lineage)
            steps = planner.plan(meaning.statement, detail, truth=base["result"])
            from .discourse import DiscourseRealiser

            discourse = DiscourseRealiser(self.realiser, meaning.statement.model_dump(), steps)
            sentences, plans, seen = [crisp], [], {crisp, base["answer"]}
            for step in steps:
                if step.kind in ("derivation", "reporting"):
                    sentence = render_lineage(discourse, planner.domain, step, list(lineage.pool.values()))
                else:
                    sentence = self.step(planner.domain, step, discourse)
                discourse.establish(step.meaning)
                if sentence not in seen:
                    sentences.append(sentence)
                    seen.add(sentence)
                    plans.append({**step.to_dict(), "sentence": sentence})
            # The primary truth, meaning and selected evidence stay identical.
            return {**common, "answer": " ".join(sentences),
                    "sentences": sentences, "detail_plan": plans, "omitted_details": planner.omitted,
                    "lineage_evidence": [f.model_dump() for f in lineage.supporting] if detail == "audit" else [],
                    "planning_units": {planner.scalar: planner.domain.units[planner.scalar].model_dump()},
                    "detail_total_ms": (perf_counter_ns() - started) / 1e6}
        except (ValueError, TypeError) as exc:
            return {"outcome": "abstained", "answer": None, "reason": str(exc), "evidence": [], "detail": detail}
