"""Question -> checked engine receipt -> compositional pySimpleNLG. No LLM.

Run from the repository root: python -m engine.answer 'QUESTION' --detail audit
"""
import argparse
from copy import deepcopy
import json

from nlg import LEVELS, NLG
from chatter import generic
from .nlg_adapter import FinanceAdapter
from .structured import execute
from .tracing import span


class Engine:
    """Reusable loaded engine; crisp is the default at both public boundaries."""
    def __init__(self, *, store=None, calendar=None, reporting=None):
        self.adapter = FinanceAdapter(store=store, calendar=calendar, reporting=reporting)
        self.nlg = NLG()

    def render_result(self, receipt, *, detail="crisp", decision=None, trace=None):
        response = {"outcome": "abstained", "answer": None, "detail": detail, "execution": receipt,
                    "warnings": deepcopy(receipt.get("warnings", [])), "result": receipt.get("result")}
        # A refusal is an engine result, not a proposition to be paraphrased.
        if receipt.get("outcome") != "answered":
            return {**response, "stage": "execution", "reason": receipt.get("reason", "engine abstained"),
                    "reason_code": receipt.get("reason_code", "engine_abstained"), "evidence": receipt.get("evidence", [])}
        if detail not in LEVELS:
            return {**response, "stage": "nlg", "reason_code": "invalid_detail", "reason": "unknown detail level"}
        try:
            with span(trace, "validation"):
                bundle = self.adapter.bundle(receipt)
            response["nlg_input"] = bundle
            if decision is not None:
                from .decisions import response as decision_response
                with span(trace, "dmn") as step:
                    envelope = decision.evaluate(self.adapter, receipt=receipt,
                                                 audit=trace.rules if trace else None)
                    if envelope["escalated"]:
                        step.update(status="blocked", reason=envelope["escalate_reason"])
                response["decision"] = envelope
                if envelope["escalated"]:
                    return {**response, **decision_response(envelope), "warnings": response["warnings"]}
            with span(trace, "nlg") as step:
                rendered = self.nlg.render(bundle, detail=detail)
                if rendered["outcome"] != "answered":
                    step.update(status="blocked", reason=rendered["reason"])
            response["nlg"] = rendered
            if rendered["outcome"] != "answered":
                return {**response, "stage": "nlg", "reason_code": "nlg_validation_failed", "reason": rendered["reason"]}
            warnings = response["warnings"]
            if rendered["reporting_basis_status"] == "unresolved":
                warnings.append({"code": "reporting_basis_unresolved", "message": "The reporting basis is unresolved."})
            text = rendered["answer"]
            if decision is not None:
                text += " " + decision_response(envelope)["answer"]
            return {**response, "outcome": "answered", "answer": text, "warnings": warnings,
                    "reporting_basis_status": rendered["reporting_basis_status"], "result_kind": rendered["result_kind"],
                    "evidence": rendered["evidence"]}
        except (ValueError, TypeError, KeyError, IndexError) as exc:
            return {**response, "stage": "adapter", "reason_code": getattr(exc, "code", "invalid_receipt"), "reason": str(exc)}

    def decide(self, task):
        from .decisions import response
        return response(task.evaluate(self.adapter))

    def answer(self, question, *, qid=0, detail="crisp", decision=None, trace=None):
        receipt = execute(question, qid, store=self.adapter.store, calendar=self.adapter.calendar, trace=trace)
        annotation = receipt.get("annotation")
        # The domain parser runs first. A social response is allowed only when
        # it found no domain meaning, unknown text or unsupported operation.
        if (decision is None and annotation and annotation["segments"] and not annotation["unplaced"]
                and not annotation["ignored"]
                and all(s["type"] == "filler" for s in annotation["segments"])):
            with span(trace, "chatter"):
                lookup = generic().lookup(question)
            if lookup.response and detail not in LEVELS:
                return {"outcome": "abstained", "answer": None, "detail": detail, "result": None,
                        "reason_code": "invalid_detail", "reason": "unknown detail level", "evidence": [], "warnings": []}
            if lookup.response:
                return {"outcome": "answered", "route": "chatter", "answer": lookup.response,
                        "detail": detail, "execution": None, "result": None, "evidence": [], "warnings": [],
                        "chatter": [m.as_dict() for m in lookup.matches]}
        return self.render_result(receipt, detail=detail, decision=decision, trace=trace)


def answer(question, *, qid=0, detail="crisp", decision=None):
    return Engine().answer(question, qid=qid, detail=detail, decision=decision)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--detail", choices=LEVELS, default="crisp")
    parser.add_argument("--decision", help="explicit JSON decision task to apply before response")
    args = parser.parse_args()
    task = None
    if args.decision:
        from .decisions import DecisionTask
        task = DecisionTask.load(args.decision)
    print(json.dumps(answer(args.question, detail=args.detail, decision=task), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
