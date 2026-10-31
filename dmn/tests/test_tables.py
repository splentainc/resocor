"""Decision-table correctness, static partition witnesses and safe refusal."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import unittest

from dmn import DecisionModel, FeelError
from dmn.schema import load_json

ROOT = Path(__file__).resolve().parents[2]
SPEC = json.loads((ROOT / "dmn/examples/revenue_review.json").read_text())


class TableTest(unittest.TestCase):
    def test_exact_numeric_boundary_and_rule_identity(self):
        model = DecisionModel(SPEC)
        for revenue, rule in (("2999.99999999", "standard"), ("3000", "large"),
                              ("3000.00000001", "large"), ("0", "standard")):
            result = model.evaluate({"revenue": revenue})
            self.assertFalse(result["escalated"], result)
            self.assertEqual(result["rule_ids"], [rule])
            self.assertEqual(result["inputs"]["revenue"]["value"], revenue)
            self.assertEqual(result["model"]["sha256"], model.sha256)
        self.assertTrue(model.analysis["complete"])
        self.assertFalse(model.analysis["gaps"] or model.analysis["overlaps"])

    def test_zero_and_false_are_decisions(self):
        for kind, expression, expected in (("number", "0", "0"), ("boolean", "false", False)):
            spec = deepcopy(SPEC)
            spec["outputs"] = {"result": {"type": kind}}
            for rule in spec["rules"]:
                rule["then"] = {"result": expression}
            result = DecisionModel(spec).evaluate({"revenue": 1})
            self.assertFalse(result["escalated"])
            self.assertEqual(result["decision"]["result"], expected)

    def test_static_gaps_report_real_witnesses_and_case_escalates(self):
        spec = deepcopy(SPEC)
        spec["rules"][1]["when"]["revenue"] = "> 4000"
        model = DecisionModel(spec)
        self.assertTrue(model.analysis["gaps"])
        for witness in model.analysis["gaps"]:
            result = model.evaluate(witness["inputs"])
            self.assertEqual(result["escalate_reason"], "gap")
        self.assertFalse(model.evaluate({"revenue": 1})["escalated"])

    def test_static_overlap_blocks_even_nonoverlapping_case(self):
        spec = deepcopy(SPEC)
        spec["rules"][1]["when"]["revenue"] = ">= 2000"
        model = DecisionModel(spec)
        self.assertTrue(model.analysis["overlaps"])
        for revenue in (1, "2500"):
            result = model.evaluate({"revenue": revenue})
            self.assertTrue(result["escalated"])
            self.assertEqual(result["escalate_reason"], "overlap")

    def test_input_and_output_types_never_coerce(self):
        model = DecisionModel(SPEC)
        for inputs in ({}, {"revenue": None}, {"revenue": True}, {"revenue": 0.1},
                       {"revenue": "-1"}, {"revenue": "10001"}, {"revenue": "1", "extra": 2}):
            self.assertEqual(model.evaluate(inputs)["escalate_reason"], "invalid_inputs")
        spec = deepcopy(SPEC)
        spec["rules"][0]["then"]["band"] = '"unreviewed"'
        self.assertEqual(DecisionModel(spec).evaluate({"revenue": 1})["escalate_reason"], "invalid_output")
        spec["rules"][0]["then"]["band"] = "1"
        self.assertEqual(DecisionModel(spec).evaluate({"revenue": 1})["escalate_reason"], "invalid_output")

    def test_null_and_erroring_outputs_escalate(self):
        for expr in ("null", "1 / 0", 'if 1 / 0 = null then "large_review" else "standard_review"'):
            spec = deepcopy(SPEC)
            spec["rules"][0]["then"]["band"] = expr
            result = DecisionModel(spec).evaluate({"revenue": 1})
            self.assertTrue(result["escalated"], result)
            self.assertEqual(result["escalate_reason"], "feel_error")

    def test_cross_input_feel_calculations_case_gap_and_overlap(self):
        model = DecisionModel.load(ROOT / "dmn/examples/replenishment.json")
        self.assertFalse(model.analysis["complete"])
        self.assertEqual(model.analysis["status"], "unsupported")
        for stock, action, quantity in ((10, "replenish", "40"), (20, "replenish", "30"), (21, "hold", "0")):
            result = model.evaluate({"stock": stock, "minimum": 20, "target": 50})
            self.assertEqual(result["decision"], {"action": action, "quantity": quantity})
        self.assertEqual(model.evaluate({"stock": 10, "minimum": 20, "target": 5})["escalate_reason"], "gap")
        spec = model.spec
        spec["rules"][1]["when"]["stock"] = ">= minimum"
        result = DecisionModel(spec).evaluate({"stock": 20, "minimum": 20, "target": 50})
        self.assertEqual(result["escalate_reason"], "overlap")
        self.assertEqual(set(result["rule_ids"]), {"hold", "reorder"})

    def test_computed_predicates_do_not_claim_continuous_coverage(self):
        spec = deepcopy(SPEC)
        spec["rules"][0]["when"]["revenue"] = "? * ? < 4"
        model = DecisionModel(spec)
        self.assertFalse(model.analysis["complete"])
        self.assertEqual(model.evaluate({"revenue": 3})["escalate_reason"], "gap")

    def test_enumerated_domains_allow_complete_cross_input_analysis(self):
        spec = DecisionModel.load(ROOT / "dmn/examples/replenishment.json").spec
        spec["inputs"] = {k: {"type": "number", "values": [0, 1, 2]} for k in spec["inputs"]}
        model = DecisionModel(spec)
        self.assertTrue(model.analysis["complete"])
        self.assertEqual(model.analysis["cells"], 27)
        self.assertTrue(model.analysis["gaps"])

    def test_string_boolean_and_date_domains(self):
        for kind, domain, tests, values in (
            ("string", {"values": ["a", "b"]}, ['"a"', '"b"'], ["a", "b"]),
            ("boolean", {}, ["false", "true"], [False, True]),
            ("date", {"min": "2024-02-28", "max": "2024-03-01"},
             ['< date("2024-02-29")', '>= date("2024-02-29")'], ["2024-02-28", "2024-02-29"])):
            spec = deepcopy(SPEC)
            spec["inputs"] = {"revenue": {"type": kind, **domain}}
            for rule, test in zip(spec["rules"], tests):
                rule["when"]["revenue"] = test
            model = DecisionModel(spec)
            self.assertTrue(model.analysis["complete"], model.analysis)
            self.assertFalse(model.analysis["gaps"])
            for value in values:
                self.assertFalse(model.evaluate({"revenue": value})["escalated"])

    def test_single_point_holes_and_open_interval_holes_are_detected(self):
        spec = deepcopy(SPEC)
        spec["inputs"]["revenue"] = {"type": "number", "min": "0", "max": "3"}
        for left, right in (("< 1", "> 1"), ("<= 1", ">= 2")):
            for rule, test in zip(spec["rules"], (left, right)):
                rule["when"]["revenue"] = test
            model = DecisionModel(spec)
            self.assertTrue(model.analysis["gaps"])
            self.assertEqual(model.evaluate({"revenue": "1" if right == "> 1" else "1.5"})["escalate_reason"], "gap")

    def test_malformed_models_and_unsupported_rules_fail_at_load(self):
        variants = []
        for field, value in (("version", True), ("hit_policy", "FIRST"), ("rules", []), ("unexpected", 1)):
            spec = deepcopy(SPEC)
            spec[field] = value
            variants.append(spec)
        spec = deepcopy(SPEC)
        spec["rules"][1]["id"] = spec["rules"][0]["id"]
        variants.append(spec)
        for expr in ("unknown", "function(x) x", '__import__("os")'):
            spec = deepcopy(SPEC)
            spec["rules"][0]["then"]["band"] = expr
            variants.append(spec)
        for spec in variants:
            with self.assertRaises(ValueError):
                DecisionModel(spec)
        with self.assertRaises(ValueError):
            load_json('{"id":"a", "id":"b"}')

    def test_compilation_and_evaluation_do_not_mutate_sources(self):
        spec = deepcopy(SPEC)
        saved = deepcopy(spec)
        model = DecisionModel(spec)
        inputs = {"revenue": "4500"}
        model.evaluate(inputs)
        self.assertEqual(spec, saved)
        self.assertEqual(inputs, {"revenue": "4500"})
        report = model.analysis
        report["overlaps"].append({"bad": "mutation"})
        self.assertFalse(model.analysis["overlaps"])
        self.assertEqual(model.sha256, DecisionModel(dict(reversed(list(spec.items())))).sha256)


if __name__ == "__main__":
    unittest.main()
