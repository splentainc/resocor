"""Checked engine facts/results -> custom FEEL table -> decision response."""
import argparse
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, timedelta
import json
from pathlib import Path

from dmn import DecisionModel
from dmn.schema import load_json
from nlg.lineage import Lineage
from nlg.schema import Domain, Fact, Result
from nlg.semantics import Evaluator

from .structured import Refusal, amount, record


@dataclass
class DecisionTask:
    model: DecisionModel
    as_of: str
    bindings: dict

    @classmethod
    def load(cls, path):
        path = Path(path).resolve()
        spec = load_json(path.read_text())
        if not isinstance(spec, dict) or set(spec) != {"model", "as_of", "bindings"}:
            raise ValueError("decision config requires model, as_of and bindings")
        return cls(DecisionModel.load(path.parent / spec["model"]), spec["as_of"], spec["bindings"])

    def evaluate(self, adapter, *, receipt=None, audit=None):
        response = {**self.model.envelope(), "as_of": self.as_of, "evidence": [],
                    "provenance": {}, "time_basis": "valid_time_in_current_store"}
        try:
            if not adapter.source_current:
                raise Refusal("reporting_context_stale", "reporting context does not match the fact database")
            if not isinstance(self.as_of, str) or date.fromisoformat(self.as_of).isoformat() != self.as_of:
                raise ValueError("as_of requires YYYY-MM-DD")
            at = date.fromisoformat(self.as_of)
            if not isinstance(self.bindings, dict) or self.bindings.keys() != self.model.inputs.keys():
                raise ValueError("bindings must cover exactly the model inputs")
            bundle = adapter.bundle(receipt) if receipt is not None else None
            if bundle is not None:
                checked = Result.model_validate(bundle["input"])
                evaluator = Evaluator(Domain.model_validate(bundle["domain"]),
                                      [Fact.model_validate(f) for f in bundle["facts"]])
                if evaluator.statement(checked.statement) != checked.result:
                    raise Refusal("invalid_receipt", "execution result disagrees with its evidence")
            domain = deepcopy(bundle["domain"]) if bundle is not None else adapter.domain()
            values, grounded, provenance = {}, [], {}
            for name, binding in self.bindings.items():
                scalar = self.model.inputs[name]
                if isinstance(binding, dict) and set(binding) == {"result"} and binding["result"] is True:
                    if bundle is None:
                        raise ValueError("result binding requires a checked execution receipt")
                    expected_kind, unit = ("number", "USD") if receipt["plan"]["op"] == "sum" else ("boolean", None)
                    if (scalar.kind, scalar.unit) != (expected_kind, unit):
                        raise ValueError("model input type/unit disagrees with the execution result")
                    for original in receipt["evidence"]:
                        end = date.fromisoformat(original["valid_to"]) - timedelta(days=1) if original["valid_to"] else None
                        if end is None or end > at:
                            raise ValueError("execution result extends past as_of")
                    values[name] = receipt["result"]
                    grounded.extend(deepcopy(bundle["facts"]))
                    provenance[name] = {"kind": "execution_result", "plan": deepcopy(receipt["plan"]),
                                        "facts": deepcopy(receipt["evidence"])}
                elif isinstance(binding, dict) and set(binding) == {"fact"}:
                    if (scalar.kind, scalar.unit) != ("number", "USD"):
                        raise ValueError("finance fact inputs must declare number with unit USD")
                    case = binding["fact"]
                    if not isinstance(case, dict) or set(case) != {"company", "metric", "period"}:
                        raise ValueError("fact binding requires company, metric and period")
                    period = case["period"]
                    if not isinstance(period, dict) or period.get("kind") not in {"fiscal_year", "fiscal_quarter"}:
                        raise ValueError("finance decision bindings require an explicit fiscal period")
                    keys = {"kind", "fiscal_year"} | ({"quarter"} if period["kind"] == "fiscal_quarter" else set())
                    if set(period) != keys or type(period["fiscal_year"]) is not int:
                        raise ValueError("unknown or invalid fiscal-period fields")
                    if "quarter" in period and (type(period["quarter"]) is not int or period["quarter"] not in (1, 2, 3, 4)):
                        raise ValueError("fiscal quarter requires an integer from 1 to 4")
                    found = adapter.lookup.facts_for(adapter.store, adapter.calendar, case)
                    if len(found) != 1 or not found[0].valid_at(at):
                        raise ValueError("fact is missing, ambiguous or not valid at as_of")
                    original = record(found[0])
                    grounded.append(adapter.fact(domain, original, case))
                    values[name] = str(amount(found[0].value))
                    provenance[name] = {"kind": "fact", "case": deepcopy(case), "facts": [original]}
                else:
                    raise ValueError("binding must select a fact or the checked execution result")
            unique = {}
            for fact in grounded:
                if fact["id"] in unique and unique[fact["id"]] != fact:
                    raise ValueError("conflicting evidence identity")
                unique[fact["id"]] = fact
            lineage = Lineage(Domain.model_validate(domain), [Fact.model_validate(f) for f in unique.values()], unique)
            response.update(provenance=provenance, evidence=list(unique.values()))
            if lineage.unresolved:
                return {**response, "escalate_reason": "unresolved_reporting_basis"}
            evaluated = self.model.evaluate(values) if audit is None else self.model.evaluate(values, audit=audit)
            return {**response, **evaluated}
        except (ValueError, TypeError, KeyError, IndexError, AttributeError, ArithmeticError) as exc:
            return {**response, "escalate_reason": getattr(exc, "code", "invalid_fact_binding"), "reason": str(exc)}


def response(envelope):
    result = {"outcome": "escalated", "answer": None, "stage": "decision",
              "decision": envelope, "evidence": envelope["evidence"], "warnings": []}
    if envelope["escalated"]:
        return {**result, "reason_code": envelope["escalate_reason"]}
    return {**result, "outcome": "answered", "route": "decision",
            "answer": "Decision: " + json.dumps(envelope["decision"], ensure_ascii=False, sort_keys=True) + "."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    args = parser.parse_args()
    from .answer import Engine
    print(json.dumps(Engine().decide(DecisionTask.load(args.config)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
