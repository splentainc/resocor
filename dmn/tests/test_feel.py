"""FEEL semantic controls, including null logic and hostile expressions."""
from datetime import date
from decimal import Decimal, localcontext
import unittest
from unittest.mock import patch

from dmn import Expression, FeelError, UnaryTest, evaluate, evaluate_checked
from dmn.values import wire


class FeelTest(unittest.TestCase):
    def test_decimal_arithmetic_and_precedence(self):
        cases = {"0.1 + 0.2": "0.3", "2 + 3 * 4": "14", "(2 + 3) * 4": "20",
                 "2 ** 3 ** 2": "512", "-2 ** 2": "-4", "2 ** -2": "0.25",
                 "(-2) ** 2": "4", "-10 + +2": "-8"}
        for expression, expected in cases.items():
            with self.subTest(expression=expression):
                self.assertEqual(evaluate(expression), Decimal(expected))

    def test_decimal_context_is_local_and_inputs_never_round_silently(self):
        with localcontext() as ctx:
            ctx.prec = 6
            self.assertEqual(evaluate("1 / 3"), Decimal("0." + "3" * 34))
            self.assertEqual(evaluate("999999999999.99999999 + 0.00000001"), Decimal("1000000000000.00000000"))
            self.assertEqual(ctx.prec, 6)
        self.assertTrue(evaluate_checked("x", {"x": 0.1}).errors)
        self.assertTrue(evaluate_checked("x", {"x": Decimal("0." + "1" * 35)}).errors)

    def test_three_valued_boolean_truth_tables(self):
        values = (True, False, None)
        labels = {True: "true", False: "false", None: "null"}
        for a in values:
            for b in values:
                for op in ("and", "or"):
                    expected = (False if a is False or b is False else True if a is True and b is True else None) if op == "and" else (
                        True if a is True or b is True else False if a is False and b is False else None)
                    self.assertIs(evaluate(f"{labels[a]} {op} {labels[b]}"), expected)
        self.assertIsNone(evaluate("not(null)"))
        self.assertIs(evaluate("not(false)"), True)
        self.assertIsNone(evaluate("1 and true"))

    def test_conditional_only_evaluates_selected_branch(self):
        result = evaluate_checked('if true then "ok" else 1 / 0')
        self.assertEqual(result.value, "ok")
        self.assertFalse(result.errors)
        self.assertEqual(evaluate('if null then "a" else "b"'), "b")
        self.assertEqual(evaluate('if false then 1 else if true then 2 else 3'), Decimal(2))

    def test_equality_null_and_type_identity(self):
        for expr, expected in (("null = null", True), ("null = 0", False), ("null != 0", True),
                               ("true = 1", None), ('"1" = 1', None), ("[1,2] = [1,2]", True),
                               ("{a:1} = {a:2}", False), ("[] = []", True)):
            self.assertIs(evaluate(expr), expected, expr)
        self.assertIsNone(evaluate("null < 2"))

    def test_lists_ranges_between_and_indexing(self):
        cases = {"5 in [1..5]": True, "5 in [1..5)": False, "1 in (1..5]": False,
                 "2 in ]1..5[": True, "2 between 1 and 3": True,
                 "4 in [1,2,3]": False, "[1,2,3][1]": Decimal(1),
                 "[1,2,3][-1]": Decimal(3), "[1,2,3][0]": None, "[][1]": None}
        for expr, expected in cases.items():
            self.assertEqual(evaluate(expr), expected, expr)
        self.assertEqual(wire(evaluate("[1..2]"))["range"]["low"], "1")

    def test_contexts_and_multiword_variables(self):
        self.assertEqual(evaluate("{a: 2, b: a + 3}.b"), Decimal(5))
        self.assertEqual(evaluate("target stock - current stock", {"target stock": 30, "current stock": 10}), Decimal(20))
        self.assertEqual(Expression("{a: 2, b: a + x}.b").references, frozenset({"x"}))
        self.assertIsNone(evaluate("{a: 1}.missing"))
        self.assertIsNone(evaluate("{a: 1}.__class__"))

    def test_core_functions(self):
        cases = {'sum([1,2,3])': Decimal(6), 'min(2,1)': Decimal(1), 'max([1,2])': Decimal(2),
                 'mean([1,2])': Decimal("1.5"), 'count([])': Decimal(0), 'abs(-2)': Decimal(2),
                 'floor(-1.2)': Decimal(-2), 'ceiling(-1.2)': Decimal(-1),
                 'decimal(2.345, 2)': Decimal("2.34"), 'decimal(2.355, 2)': Decimal("2.36"),
                 'lower case("ABC")': "abc", 'upper case("abc")': "ABC", 'string length("a你")': Decimal(2),
                 'starts with("abc","a")': True, 'ends with("abc","c")': True, 'contains("abc","b")': True,
                 'list contains([1,2], 2)': True, 'get value({a:2}, "a")': Decimal(2),
                 'number("12.34")': Decimal("12.34"), 'string(false)': "false",
                 'all([true,null])': None, 'any([false,null])': None, 'all([])': True, 'any([])': False}
        for expr, expected in cases.items():
            self.assertEqual(evaluate(expr), expected, expr)

    def test_date_functions_and_comparisons(self):
        self.assertEqual(evaluate('date("2024-02-29")'), date(2024, 2, 29))
        self.assertIs(evaluate('date("2024-02-29") < date("2024-03-01")'), True)
        self.assertIs(evaluate('d in [date("2024-02-28")..date("2024-03-01"))', {"d": date(2024, 2, 29)}), True)

    def test_evaluation_errors_return_null_and_checked_diagnostics(self):
        for expr in ("1 / 0", "unknown", 'number("bad")', 'date("2023-02-29")',
                     "2 ** 1000", 'min([])', 'true + 1', "[1..0]", "sum(1, true)"):
            result = evaluate_checked(expr)
            self.assertIsNone(result.value, expr)
            self.assertTrue(result.errors, expr)

    def test_unsupported_constructs_and_code_injection_are_rejected(self):
        for expr in ('__import__("os")', 'open("file")', 'x.__class__()', "lambda x: x",
                     "for x in [1,2] return x", "function(x) x + 1", 'date and time("2023")',
                     "1; 2", "1 +", "unknownFunction()", "max(n: 1)"):
            with self.subTest(expr=expr), self.assertRaises(ValueError):
                Expression(expr)
        with patch("builtins.eval", side_effect=AssertionError("host eval")):
            self.assertEqual(evaluate("1 + 2"), Decimal(3))

    def test_resource_limits(self):
        for text in ("(" * 80 + "1" + ")" * 80, "1" * 4097, "+".join(["1"] * 300)):
            with self.assertRaises(ValueError):
                Expression(text)
        value = [0]
        for _ in range(16):
            value = [value, value]
        result = evaluate_checked("x", {"x": value})
        self.assertTrue(result.errors)


class UnaryTestCase(unittest.TestCase):
    def test_comparisons_values_lists_and_negation(self):
        for text, values in (("< 5", [(4, True), (5, False)]), ("not(< 1, > 5)", [(1, True), (6, False)]),
                             ("1, 3, 5", [(3, True), (2, False)]), ("[1,2,3]", [(2, True), (4, False)]),
                             ('"a", "b"', [("b", True), ("c", False)]), ("false", [(False, True), (True, False)]),
                             ("-", [(None, True), (1, True)]), ("? > limit", [(6, True), (4, False)])):
            for value, expected in values:
                self.assertIs(UnaryTest(text, ["limit"]).evaluate(value, {"limit": 5}).value, expected, text)

    def test_unary_interval_boundaries(self):
        for text, lo, hi in (("[1..5]", True, True), ("(1..5)", False, False),
                             ("[1..5)", True, False), ("]1..5[", False, False)):
            test = UnaryTest(text)
            self.assertIs(test.evaluate(1).value, lo, text)
            self.assertIs(test.evaluate(5).value, hi, text)
            self.assertIs(test.evaluate(3).value, True, text)
        self.assertIs(UnaryTest("< 5").evaluate(None).value, None)


if __name__ == "__main__":
    unittest.main()
