"""Checked receipts for the executor's two supported shapes. No prose or model."""
from dataclasses import asdict
from decimal import Decimal, localcontext
import re

from ._legacy import execution_module
from .tracing import span


class Refusal(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def amount(value):
    """The stored USD representation, preserving signs and zero exactly."""
    text = str(value).strip().replace(",", "")
    if text.startswith("-$"):
        text = "-" + text[2:]
    elif text.startswith("$"):
        text = text[1:]
    if isinstance(value, Decimal):
        text = format(value, "f")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        raise Refusal("invalid_value", "stored value is not an exact USD amount")
    number = Decimal(text)
    if abs(number) >= Decimal("1e24") or number.as_tuple().exponent < -8:
        raise Refusal("invalid_value", "stored value exceeds numeric bounds")
    return number


def record(fact):
    return {k: v.isoformat() if hasattr(v, "isoformat") else v for k, v in asdict(fact).items()}


def fiscal_year(text):
    if not isinstance(text, str) or not re.fullmatch(r"\d{4}", text.strip()):
        raise Refusal("unsupported_period", "execution requires one explicit fiscal year")
    return int(text)


def quantity(text, legacy):
    """Validate all quantity tokens; never silently drop a sign/currency/unit."""
    value = None
    scale = Decimal(1)
    scaled = False
    currency = False
    from .number_words import integer
    tokens = [token for token, _, _ in legacy.tokenize(text)]
    normalized, k = [], 0
    while k < len(tokens):
        best = None
        for end in range(k + 1, min(k + 6, len(tokens)) + 1):
            parsed = integer(tokens[k:end])
            if parsed is not None:
                best = end, str(parsed)
        if best:
            k, number = best
            normalized.append(number)
        else:
            normalized.append(tokens[k]); k += 1
    for token in normalized:
        if re.fullmatch(r"\d[\d,]*(?:\.\d+)?", token) and value is None:
            value = Decimal(token.replace(",", ""))
        elif (token == "$" or (token.casefold() == "usd" and any(
                r['user_phrase'].casefold() == 'usd' and r['phrase_type'] == 'currency'
                and r['valid_phrase'] == 'USD' and r['reviewed'] == 1 for r in legacy.GLOSS))) and not currency:
            currency = True
        else:
            key = (token.lower(),)
            row = legacy.GLOSS[legacy.PHRASES[key]] if key in legacy.PHRASES else None
            if not scaled and row and row["phrase_type"] == "scale":
                scale, scaled = Decimal(row["valid_phrase"]), True
            else:
                raise Refusal("unsupported_quantity", "quantity contains an unsupported token")
    if value is None:
        raise Refusal("unsupported_quantity", "quantity has no numeric value")
    with localcontext() as context:
        context.prec = 64
        return str(amount(value * scale))


def lookup_case(tree, legacy, *, reviewed=False):
    if tree.get("node") != "Lookup" or not isinstance(tree.get("metric"), str):
        raise Refusal("unsupported_execution", "execution requires an unqualified lookup")
    company = legacy.valid(tree.get("entity") or "", "entity")
    metric = legacy.valid(tree["metric"], "metric")
    if not company or not metric:
        raise Refusal("unresolved_identity", "entity or metric did not resolve")
    # fact_lookup adds the annual suffix; already-annual predicates need a
    # different resolution contract and must not become _annual_annual.
    if metric.endswith("_annual"):
        raise Refusal("unsupported_execution", "explicit annual metric aliases are not executable yet")
    if reviewed:
        from .financial_binding import contracts, period
        if metric not in contracts():
            raise Refusal("unsupported_execution", "lookup is outside reviewed predicate contracts")
        return {"company": company, "metric": metric, "period": period(tree.get("period"), legacy)}
    return {"company": company, "metric": metric,
            "period": {"kind": "fiscal_year", "fiscal_year": fiscal_year(tree.get("period"))}}


def resolve_checked(tree, legacy):
    if tree["node"] == "Unsupported":
        raise Refusal("parser_unsupported", tree["reason"])
    if tree["node"] == "Lookup":
        return {"op": "lookup", "cases": [lookup_case(tree, legacy, reviewed=True)]}
    if tree["node"] == "Aggregate":
        series = tree["over"]
        if (tree["fn"] not in ("sum", "avg") or (tree["fn"] == "avg" and not series.get("quarter_set"))
                or tree.get("returns", "value") != "value"):
            raise Refusal("unsupported_execution", "executor supports sum/average aggregates returning a value")
        if series.get("except") or series.get("from") != series.get("to"):
            raise Refusal("unsupported_execution", "executor supports one complete fiscal year without exclusions")
        if legacy.valid(series.get("step") or "", "step") != "quarter":
            raise Refusal("unsupported_execution", "executor supports only quarter steps")
        coverage = series.get("coverage")
        if coverage and not re.fullmatch(r"all(?:\s+4)?", coverage.strip(), re.I):
            raise Refusal("unsupported_execution", "executor requires all four quarters")
        quarters = [1, 2, 3, 4]
        period = series.get("from")
        if series.get("quarter_set"):
            from .annotation_schema import QuarterSet
            qs = QuarterSet.model_validate(series["quarter_set"])
            if not re.fullmatch(r"(?:FY\s*)?" + str(qs.fiscal_year), period or "", re.I):
                raise Refusal("unsupported_period", "quarter set conflicts with its explicit fiscal year")
            quarters, period = qs.quarters, str(qs.fiscal_year)
            if coverage and len(quarters) != 4:
                raise Refusal("unsupported_period", "all coverage conflicts with quarter set")
        case = lookup_case({"node": "Lookup", "entity": series.get("entity"),
                            "metric": series.get("metric"), "period": period}, legacy)
        if case["metric"] in legacy.fact_lookup.INSTANT_METRICS:
            raise Refusal("unsupported_execution", "balance-sheet stocks cannot be summed across quarters")
        return {"op": tree["fn"], "cases": [{**case, "period": {**case["period"], "kind": "fiscal_quarter", "quarter": q}}
                                         for q in quarters], "need": len(quarters)}
    if tree["node"] == "Compare" and tree["op"] in ("gt", "lt", "gte", "lte", "eq") and tree["right"].get("node") == "Quantity":
        reviewed = not re.fullmatch(r"\d{4}", tree['left'].get('period') or '') or tree['op'] not in ('gt', 'lt')
        return {"op": tree["op"], "cases": [lookup_case(tree["left"], legacy, reviewed=reviewed)],
                "limit": quantity(tree["right"]["text"], legacy)}
    raise Refusal("unsupported_execution", f"executor does not support {tree['node']} in this shape")


def execute(question, qid=0, *, store=None, calendar=None, trace=None):
    """Always return a JSON-safe answer receipt or an explicit abstention."""
    receipt = {"version": 1, "question": question, "outcome": "abstained", "result": None,
               "annotation": None, "plan": None, "facts": [], "evidence": []}
    if trace is not None:
        trace.receipt = receipt
    try:
        if not isinstance(question, str) or not question.strip() or len(question) > 4096:
            raise Refusal("invalid_question", "question must contain 1 to 4096 characters")
        legacy = execution_module()
        with span(trace, "segments"):
            annotation = legacy.step1(question, qid).model_dump(by_alias=True, exclude_none=True)
        receipt["annotation"] = annotation
        if annotation["segments"] and all(s["type"] == "filler" for s in annotation["segments"]):
            raise Refusal("no_factual_request", "no factual request to execute")
        if annotation["unplaced"]:
            reason = annotation["tree"].get("reason", "parser left non-filler segments unplaced")
            raise Refusal("parser_unplaced", reason)
        # Ignored content may contain an omitted request. Do not turn it into a
        # confident answer to just the recognised part of the question.
        if annotation["ignored"]:
            raise Refusal("parser_ignored", "parser ignored content: " + "; ".join(annotation["ignored"]))
        with span(trace, "query"):
            from .execution_roles import check
            check(question, annotation["tree"])
            plan = resolve_checked(annotation["tree"], legacy)
        receipt["plan"] = plan
        store = legacy.STORE if store is None else store
        calendar = legacy.CAL if calendar is None else calendar
        with span(trace, "facts"):
            groups = [legacy.fact_lookup.facts_for(store, calendar, case) for case in plan["cases"]]
        receipt["facts"] = [[record(f) for f in group] for group in groups]
        receipt["evidence"] = [record(f) for group in groups for f in group]
        with span(trace, "execution"):
            if any(len(group) != 1 for group in groups):
                raise Refusal("missing_or_ambiguous_facts", "each required period must have exactly one stored fact")
            values = [amount(group[0].value) for group in groups]
            if plan["op"] in ("sum", "avg"):
                with localcontext() as context:
                    context.prec = 64
                    total = sum(values, Decimal(0))
                    result = str(total / len(values) if plan["op"] == "avg" else total)
            elif plan['op'] == 'lookup':
                result = str(values[0])
            else:
                limit = amount(plan["limit"])
                result = {'gt': values[0] > limit, 'lt': values[0] < limit, 'gte': values[0] >= limit,
                          'lte': values[0] <= limit, 'eq': values[0] == limit}[plan['op']]
        receipt.update(outcome="answered", result=result)
    except (Refusal, LookupError, KeyError, ValueError, TypeError) as exc:
        receipt.update(reason=str(exc), reason_code=getattr(exc, "code", "execution_error"))
    return receipt
