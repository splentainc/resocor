"""Exact rectangular-domain partition analysis; explicit limits for other FEEL."""
from datetime import date, timedelta
from decimal import Decimal, localcontext
from itertools import product
from math import prod

from .values import DECIMAL, FeelError, wire

LIMIT = 4096


def representatives(scalar, tests):
    if scalar.values is not None:
        # Cross-input predicates still need a complete product. Only finite
        # enums can be exhaustively enumerated without boundary extraction.
        return list(scalar.values)
    points = {scalar.low, scalar.high}
    for test in tests:
        for value in test.boundaries():
            if type(value) is not type(scalar.low):
                raise FeelError("incompatible unary-test boundary")
            if scalar.low <= value <= scalar.high:
                points.add(value)
    points = sorted(points)
    result = list(points)
    for low, high in zip(points, points[1:]):
        if type(low) is date:
            mid = low + timedelta(days=1)
        else:
            with localcontext(DECIMAL):
                mid = low + (high - low) / Decimal(2)
        if low < mid < high:
            result.append(mid)
    return sorted(set(result))


def analyze(model):
    base = {"status": "unsupported", "complete": False, "cells": 0,
            "gaps": [], "overlaps": [], "diagnostics": []}
    names = list(model.inputs)
    try:
        # If every domain is finite, the full product proves even arbitrary
        # supported FEEL predicates. Continuous domains require independent,
        # constant unary-test boundaries on every column.
        all_finite = all(v.values is not None for v in model.inputs.values())
        if not all_finite and any(test.references for _, tests, _ in model.rules for test in tests.values()):
            raise FeelError("cross-input conditions over continuous domains are not statically analyzed")
        axes = [representatives(model.inputs[name], [tests[name] for _, tests, _ in model.rules]) for name in names]
        total = prod(len(axis) for axis in axes)
        if total > LIMIT:
            raise FeelError("static partition exceeds " + str(LIMIT) + " cells")
        if total * len(model.rules) * len(names) > 100000:
            raise FeelError("static partition exceeds the rule-check work budget")
        gaps, overlaps = [], []
        for row in product(*axes):
            values = dict(zip(names, row))
            matches, errors = model.match(values)
            if errors:
                raise FeelError("a partition cell has indeterminate/erroring FEEL tests")
            witness = {"inputs": wire(values), "rule_ids": [m[0] for m in matches]}
            if not matches:
                gaps.append(witness)
            elif len(matches) > 1:
                overlaps.append(witness)
        return {**base, "status": "complete", "complete": True, "cells": total,
                "gaps": gaps, "overlaps": overlaps,
                "diagnostics": ["partition analysis covers declared domains only"]}
    except (ValueError, TypeError) as exc:
        return {**base, "diagnostics": [str(exc)]}
