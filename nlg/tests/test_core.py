"""Primitive rendering and consistency checks."""
from copy import deepcopy
import unittest

from nlg.renderer import CompositionalRealiser


def prop(scope=None):
    return {"node": "Property", "entity": "a", "metric": "m", "scope": scope}


def qty(value, unit="n"):
    return {"node": "Quantity", "value": str(value), "unit": unit}


def relation(left, right, op="eq"):
    return {"node": "Relation", "op": op, "left": left, "right": right}


class CoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.renderer = CompositionalRealiser()

    def setUp(self):
        self.context = {
            "domain": {"name": "primitive_controls", "provenance": "explicit synthetic test fixture",
                       "entities": {"a": "Example"}, "metrics": {"m": {"noun": {"head": "value"}, "kind": "number", "unit": "n"}},
                       "units": {"n": {"dimension": {"count": 1}}, "other": {"dimension": {"length": 1}}},
                       "scopes": {"s1": {"label": "period 1"}, "s2": {"label": "period 2"}},
                       "groups": {"g": {"axis": "scope", "members": ["s1", "s2"], "noun": {"head": "period"}}}},
            "facts": [{"id": "f", "entity": "a", "metric": "m", "value": "-2.5", "source": "test"},
                      {"id": "f1", "entity": "a", "metric": "m", "scope": "s1", "value": "2", "source": "test"},
                      {"id": "f2", "entity": "a", "metric": "m", "scope": "s2", "value": "4", "source": "test"}]}

    def render(self, stmt, truth=True, context=None):
        return self.renderer.render({**(context or self.context), "input": {"version": 2, "statement": stmt, "result": truth}})

    def rejected(self, statement, truth=True, context=None):
        out = self.render(statement, truth, context)
        self.assertEqual(out["outcome"], "abstained", out)
        self.assertIsNone(out["answer"])

    def test_scalar_and_signed_value(self):
        self.assertEqual(self.render(relation(prop(), qty(-2.5)))["answer"], "The value of Example was -2.5.")

    def test_comparison_morphology_and_false_result(self):
        for op, threshold, truth, phrase in [("gt", 0, False, "was not greater than 0"),
                                            ("lt", 0, True, "was less than 0"),
                                            ("gt", -3, True, "was greater than -3")]:
            out = self.render(relation(prop(), qty(threshold), op), truth)
            self.assertEqual(out["outcome"], "answered", out)
            self.assertIn(phrase, out["answer"])

    def test_all_aggregate_primitives(self):
        for fn, value in [("sum", 6), ("average", 3), ("minimum", 2), ("maximum", 4)]:
            expr = {"node": "Aggregate", "fn": fn, "over": {"node": "Series", "entity": "a", "metric": "m", "group": "g"}}
            self.assertEqual(self.render(relation(expr, qty(value)))["outcome"], "answered")

    def test_arithmetic_operand_order_and_gerunds(self):
        for op, value, phrase in [("add", -1.5, "adding"), ("subtract", -3.5, "subtracting 1 from"),
                                  ("multiply", -2.5, "multiplying"), ("divide", -2.5, "dividing")]:
            context = deepcopy(self.context)
            # Multiplication/division by a dimensionless constant retain count units.
            context["domain"]["units"]["ratio"] = {"dimension": {}}
            expr = {"node": "Arithmetic", "op": op, "left": prop(),
                    "right": qty(1, "ratio" if op in ("divide", "multiply") else "n")}
            out = self.render(relation(expr, qty(value)), context=context)
            self.assertEqual(out["outcome"], "answered", out)
            self.assertIn(phrase, out["answer"])

    def test_plural_agreement_is_schema_data(self):
        self.context["domain"]["metrics"]["m"]["noun"] = {"head": "asset", "number": "plural"}
        self.assertIn("assets of Example were", self.render(relation(prop(), qty(-2.5)))["answer"])

    def test_explicit_not_and_double_not(self):
        stmt = relation(prop(), qty(0), "gt")
        self.assertIn("was not greater", self.render({"node": "Not", "expr": stmt})["answer"])
        double = {"node": "Not", "expr": {"node": "Not", "expr": stmt}}
        self.assertIn("was not greater", self.render(double, False)["answer"])

    def test_negation_of_conjunction_keeps_scope(self):
        stmt = {"node": "And", "items": [relation(prop(), qty(-2.5)), relation(prop(), qty(0), "gt")]}
        out = self.render(stmt, False)
        self.assertTrue(out["answer"].startswith("It was not the case that"), out)
        self.assertEqual(out["answer"].count("not"), 1)
        self.assertIn("and that", out["answer"])

    def test_missing_ambiguous_and_duplicate_evidence(self):
        self.context["facts"] = []
        self.rejected(relation(prop(), qty(-2.5)))
        self.setUp()
        self.context["facts"].append({**self.context["facts"][0], "id": "ambiguous"})
        self.rejected(relation(prop(), qty(-2.5)))
        self.context["facts"][-1]["id"] = "f"
        self.rejected(relation(prop(), qty(-2.5)))

    def test_complete_coverage_required(self):
        self.context["facts"] = self.context["facts"][:-1]
        expr = {"node": "Aggregate", "fn": "sum", "over": {"node": "Series", "entity": "a", "metric": "m", "group": "g"}}
        self.rejected(relation(expr, qty(2)))

    def test_units_invalid_results_and_zero_division(self):
        self.rejected(relation(prop(), qty(-2.5, "other")))
        self.rejected(relation(prop(), qty(-2.5)), False)
        expr = {"node": "Arithmetic", "op": "divide", "left": prop(), "right": qty(0)}
        self.rejected(relation(expr, qty(0)))

    def test_non_numeric_and_nonfinite_values(self):
        for text in ("NaN", "Infinity", "1e90", "0.0000000001", "nonsense"):
            self.context["facts"][0]["value"] = text
            self.rejected(relation(prop(), qty(0)))

    def test_unknown_fields_nodes_and_qualifiers(self):
        for key, value in [("forecast", True), ("unit", "n"), ("qualifiers", ["adjusted"])]:
            p = {**prop(), key: value}
            self.rejected(relation(p, qty(-2.5)))
        self.rejected(relation({"node": "Forecast"}, qty(0)))

    def test_literal_only_claims_and_naked_series_refuse(self):
        self.rejected(relation(qty(1), qty(1)))
        self.rejected(relation({"node": "Series", "entity": "a", "metric": "m", "group": "g"}, qty(6)))

    def test_no_short_circuit_through_missing_evidence(self):
        missing = {**prop(), "entity": "unknown"}
        self.rejected({"node": "And", "items": [relation(prop(), qty(0)), relation(missing, qty(0))]}, False)

    def test_metadata_rename_needs_no_renderer_changes(self):
        before = self.render(relation(prop(), qty(-2.5)))
        self.context["domain"]["entities"]["a"] = "Different Tenant"
        self.context["domain"]["metrics"]["m"]["noun"] = {"head": "temperature", "modifiers": ["inlet"]}
        after = self.render(relation(prop(), qty(-2.5)))
        self.assertNotEqual(before["answer"], after["answer"])
        self.assertIn("inlet temperature of Different Tenant", after["answer"])

    def test_compound_terms_and_three_way_coordination(self):
        self.context["domain"]["metrics"]["m"]["noun"] = {"head": "year", "modifiers": ["first", "release"]}
        stmt = relation(prop(), qty(-2.5))
        out = self.render({"node": "And", "items": [stmt, stmt, stmt]})
        self.assertEqual(out["outcome"], "answered", out)
        self.assertEqual(out["answer"].count("first release year"), 3)
        self.assertNotIn("first, release", out["answer"])

    def test_malformed_and_oversized_input(self):
        for bundle in (None, {}, {"domain": {}, "facts": [], "input": {}}):
            self.assertEqual(self.renderer.render(bundle)["outcome"], "abstained")
        stmt = relation(prop(), qty(-2.5))
        for _ in range(40):
            stmt = {"node": "Not", "expr": stmt}
        self.rejected(stmt)

    def test_inputs_preserved_and_evidence_selected(self):
        before = deepcopy(self.context)
        out = self.render(relation(prop(), qty(-2.5)))
        self.assertEqual(self.context, before)
        self.assertEqual([f["id"] for f in out["evidence"]], ["f"])


if __name__ == "__main__":
    unittest.main()
