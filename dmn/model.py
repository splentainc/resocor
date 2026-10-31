"""Authored FEEL tables: typed UNIQUE matching, calculation and escalation."""
from copy import deepcopy
import hashlib
from pathlib import Path
from types import MappingProxyType
from datetime import datetime, timezone
from time import perf_counter_ns

from .analysis import analyze
from .feel import Expression
from .schema import Scalar, canonical, fields, identifier, load_json
from .unary import UnaryTest
from .values import FeelError, wire


class DecisionModel:
    def __init__(self, spec):
        self._spec, text = canonical(spec)
        spec = self._spec
        fields(spec, ("version", "id", "hit_policy", "inputs", "outputs", "rules"), ("description",))
        if type(spec["version"]) is not int or spec["version"] != 1 or spec["hit_policy"] != "UNIQUE":
            raise FeelError("model version 1 with UNIQUE hit policy is required")
        self.id = identifier(spec["id"])
        self.sha256 = hashlib.sha256(text.encode()).hexdigest()
        for key in ("inputs", "outputs"):
            if not isinstance(spec[key], dict) or not 1 <= len(spec[key]) <= 8:
                raise FeelError("one to eight " + key + " are required")
            for name in spec[key]:
                identifier(name)
        self.inputs = MappingProxyType({name: Scalar.parse(value, input=True) for name, value in spec["inputs"].items()})
        self.outputs = MappingProxyType({name: Scalar.parse(value) for name, value in spec["outputs"].items()})
        if not isinstance(spec["rules"], list) or not 1 <= len(spec["rules"]) <= 128:
            raise FeelError("one to 128 rules are required")
        self.rules, seen = [], set()
        for rule in spec["rules"]:
            fields(rule, ("id", "when", "then"), ("description",))
            rule_id = identifier(rule["id"])
            if rule_id in seen:
                raise FeelError("duplicate rule ID")
            seen.add(rule_id)
            if not isinstance(rule["when"], dict) or rule["when"].keys() != self.inputs.keys():
                raise FeelError("each rule needs exactly one unary test for every input; use '-' explicitly")
            if not isinstance(rule["then"], dict) or rule["then"].keys() != self.outputs.keys():
                raise FeelError("each rule must provide every output")
            tests = {name: UnaryTest(expr, self.inputs) for name, expr in rule["when"].items()}
            outputs = {name: Expression(expr, self.inputs) for name, expr in rule["then"].items()}
            if any(expr.references - self.inputs.keys() for expr in [*tests.values(), *outputs.values()]):
                raise FeelError("rule refers to an undeclared input")
            self.rules.append((rule_id, MappingProxyType(tests), MappingProxyType(outputs)))
        self.rules = tuple(self.rules)
        self._analysis = analyze(self)

    @property
    def analysis(self):
        return deepcopy(self._analysis)

    @classmethod
    def load(cls, path):
        return cls(load_json(Path(path).read_text(encoding="utf-8")))

    @property
    def spec(self):
        return deepcopy(self._spec)

    def envelope(self):
        return {"decision": None, "escalated": True, "escalate_reason": None, "inputs": {},
                "rule_ids": [], "engine": "resocor-python-feel/0.1.0",
                "model": {"id": self.id, "sha256": self.sha256, "hit_policy": "UNIQUE"},
                "analysis": deepcopy(self.analysis)}

    def match(self, values, *, audit=None):
        matches, errors = [], []
        for index, (rule_id, tests, outputs) in enumerate(self.rules):
            started_at = datetime.now(timezone.utc).isoformat(timespec="microseconds") if audit is not None else None
            start = perf_counter_ns() if audit is not None else 0
            results = [test.evaluate(values[name], values) for name, test in tests.items()]
            if audit is not None:
                audit.append({"rule_id": rule_id, "started_at": started_at,
                              "ended_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                              "elapsed_us": (perf_counter_ns() - start) / 1000,
                              "tests": [{"input": name, "expression": self._spec["rules"][index]["when"][name],
                                         "value": wire(result.value), "errors": list(result.errors)}
                                        for name, result in zip(tests, results)],
                              "matched": all(r.value is True and not r.errors for r in results)})
            # Every row is checked: UNIQUE never silently chooses the first.
            if any(result.errors for result in results):
                errors.append({"rule_id": rule_id, "errors": [e for r in results for e in r.errors]})
            elif any(r.value is False for r in results):
                continue
            elif any(r.value is not True for r in results):
                errors.append({"rule_id": rule_id, "errors": ["indeterminate unary test"]})
            else:
                matches.append((rule_id, tests, outputs))
        return matches, errors

    def evaluate(self, inputs, *, audit=None):
        response = self.envelope()
        try:
            if not isinstance(inputs, dict) or inputs.keys() != self.inputs.keys():
                raise FeelError("inputs must exactly match the declared names")
            values = {name: scalar.accept(inputs[name]) for name, scalar in self.inputs.items()}
            response["inputs"] = {name: {"type": self.inputs[name].kind, "value": wire(value),
                                         "unit": self.inputs[name].unit} for name, value in values.items()}
        except (ValueError, TypeError, ArithmeticError) as exc:
            return {**response, "escalate_reason": "invalid_inputs", "reason": str(exc)}
        if self.analysis["overlaps"]:
            return {**response, "escalate_reason": "overlap", "reason": "static analysis found overlapping UNIQUE rules"}
        matches, errors = self.match(values, audit=audit)
        response["rule_ids"] = [rule[0] for rule in matches]
        if errors:
            return {**response, "escalate_reason": "feel_error", "diagnostics": errors}
        if len(matches) != 1:
            return {**response, "escalate_reason": "gap" if not matches else "overlap"}
        outputs = {}
        try:
            for name, expression in matches[0][2].items():
                result = expression.evaluate(values)
                if result.errors or result.value is None:
                    return {**response, "escalate_reason": "feel_error", "diagnostics": list(result.errors) or ["null decision output"]}
                outputs[name] = wire(self.outputs[name].accept_result(result.value))
        except (ValueError, TypeError, ArithmeticError) as exc:
            return {**response, "escalate_reason": "invalid_output", "reason": str(exc)}
        return {**response, "escalated": False, "decision": outputs}

    def describe(self):
        """A reviewable table and requirements graph, without executing rules."""
        return {"id": self.id, "inputs": self.spec["inputs"], "outputs": self.spec["outputs"],
                "rules": self.spec["rules"], "analysis": deepcopy(self.analysis),
                "requirements": [{"input": name, "decision": self.id} for name in self.inputs]}
