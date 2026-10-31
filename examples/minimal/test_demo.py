"""Usage checks for the supplied synthetic example."""
from contextlib import ExitStack
from copy import deepcopy
import unittest
from unittest.mock import patch

from examples.minimal.demo import answer, load_example
from nlg import NLG


class SyntheticDemoTest(unittest.TestCase):
    def test_detached_demo_runs_without_database_network_or_processes(self):
        with ExitStack() as stack:
            for target in ("sqlite3.connect", "socket.create_connection", "socket.socket.connect",
                           "socket.socket.connect_ex", "subprocess.Popen"):
                stack.enter_context(patch(target, side_effect=AssertionError("external resource accessed")))
            for detail in ("crisp", "detailed", "audit"):
                result = answer(detail=detail)
                self.assertEqual(result["outcome"], "answered", result)
                self.assertTrue(result["synthetic"])
                self.assertEqual(result["selected_fact_ids"], ["synthetic-revenue-2024"])
                self.assertIn("greater than $1,000", result["answer"])

    def test_missing_period_or_record_abstains(self):
        data = load_example()
        for year, rows in ((2025, data["facts"]), (2024, [])):
            supplied = {**data, "facts": rows}
            result = answer(year, data=supplied)
            self.assertEqual(result["outcome"], "abstained")
            self.assertIsNone(result["answer"])

    def test_conflicting_records_do_not_choose_a_value(self):
        data = load_example()
        data["facts"].append({**data["facts"][0], "fact_id": "conflict", "value": "900"})
        result = answer(data=data)
        self.assertEqual(result["outcome"], "abstained")
        self.assertEqual(result["selected_count"], 2)

    def test_false_comparison_remains_a_supported_answer(self):
        data = load_example()
        data["facts"][0]["value"] = "900"
        result = answer(data=data)
        self.assertEqual(result["outcome"], "answered", result)
        self.assertFalse(result["comparison"]["result"])
        self.assertIn("not greater than", result["answer"])

    def test_rendering_rejects_a_tampered_comparison_receipt(self):
        bundle = deepcopy(answer()["supplied_bundle"])
        bundle["input"]["result"] = False
        self.assertEqual(NLG().render(bundle)["outcome"], "abstained")
