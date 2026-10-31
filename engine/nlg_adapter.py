"""Finance receipt -> generic meaning/fact bundle. No sentence templates."""
from copy import deepcopy
from pathlib import Path
import json
import re

from ._legacy import execution_module
from .export_reporting import digest
from .structured import Refusal, amount, record, resolve_checked

HERE = Path(__file__).resolve().parent


class FinanceAdapter:
    def __init__(self, *, store=None, calendar=None, reporting=None, database=None):
        legacy = execution_module()
        self.lookup = legacy.fact_lookup
        self.store = legacy.STORE if store is None else store
        self.calendar = legacy.CAL if calendar is None else calendar
        self.metadata = json.loads((HERE / "data/domain.json").read_text())
        self.reporting = reporting if reporting is not None else json.loads((HERE / "data/reporting_basis.json").read_text())
        self.database = database or HERE / "data/finance_public.db"
        self.source_current = self.reporting.get("database_sha256") == digest(self.database)

    def domain(self):
        metrics = {}
        for key, attribute in self.metadata["attributes"].items():
            if key.endswith("_annual"):
                continue
            words = attribute["label"].split()
            noun = {"head": words[-1], "modifiers": words[:-1]}
            if key in self.lookup.INSTANT_METRICS:
                noun = {"head": "asset", "modifiers": ["total"], "number": "plural"}
            metrics[key] = {"noun": noun, "kind": "number", "unit": "USD",
                            "summable": key not in self.lookup.INSTANT_METRICS}
        return {"name": "finance", "provenance": "engine/data/finance_public.db; cached SEC EDGAR reporting context; stored values unchanged",
                "entities": {k: k for k in self.metadata["db_names"]}, "metrics": metrics,
                "units": {"USD": {"dimension": {"currency:USD": 1}, "prefix": "$"}}, "scopes": {}, "groups": {}}

    def scope(self, domain, case):
        period = case["period"]
        year, quarter = period["fiscal_year"], period.get("quarter")
        instant = case["metric"] in self.lookup.INSTANT_METRICS
        identity = f"FY{year}" + (f"Q{quarter}" if quarter else "") + ("end" if instant else "")
        label = (f"Q{quarter} of " if quarter else "") + f"fiscal year {year}"
        domain["scopes"][identity] = {"label": ("the end of " if instant else "") + label,
                                      "preposition": "at" if instant else "in"}
        return identity

    def fact(self, domain, original, case, visiting=()):
        if original["fact_id"] in visiting:
            raise Refusal("invalid_lineage", "cyclic source derivation")
        company = self.lookup.COMPANY_KEYS.get(case["company"], case["company"])
        metric = case["metric"]
        expected = metric + ("_annual" if case["period"]["kind"] == "fiscal_year" and metric not in self.lookup.INSTANT_METRICS else "")
        if original["company"] != company or original["predicate"] != expected:
            raise Refusal("invalid_receipt", "fact identity disagrees with the execution case")
        # Recheck membership against the actual store; an arbitrary receipt must
        # not substitute another value/source/period under an existing fact ID.
        found = self.lookup.facts_for(self.store, self.calendar, case)
        if len(found) != 1 or record(found[0]) != original:
            raise Refusal("invalid_receipt", "execution fact no longer matches the selected store record")
        fact = {"id": original["fact_id"], "entity": company, "metric": metric,
                "scope": self.scope(domain, case), "value": str(amount(original["value"])),
                "source": original["source"], "source_record": deepcopy(original)}
        source = original["source"]
        if source.startswith("derived:"):
            match = re.fullmatch(r"derived: FY(\d{4}) annual figure minus Q1-Q3; annual from (.+)", source)
            if not match or int(match[1]) != case["period"]["fiscal_year"] or case["period"].get("quarter") != 4:
                raise Refusal("invalid_lineage", "unrecognised stored derivation")
            year = int(match[1])
            dependencies = []
            for quarter in (None, 1, 2, 3):
                period = {"kind": "fiscal_year" if quarter is None else "fiscal_quarter", "fiscal_year": year}
                if quarter:
                    period["quarter"] = quarter
                dependency_case = {"company": company, "metric": metric, "period": period}
                matches = self.lookup.facts_for(self.store, self.calendar, dependency_case)
                if len(matches) != 1:
                    raise Refusal("invalid_lineage", "stored derivation has missing or ambiguous operands")
                dependencies.append(self.fact(domain, record(matches[0]), dependency_case, visiting + (fact["id"],)))
            if dependencies[0]["source"] != match[2]:
                raise Refusal("invalid_lineage", "annual operand source disagrees with stored derivation")
            expression = self.property(dependencies[0])
            for dependency in dependencies[1:]:
                expression = {"node": "Arithmetic", "op": "subtract", "left": expression, "right": self.property(dependency)}
            fact["source_record"]["derivation"] = {"expression": expression, "dependencies": dependencies}
        else:
            reporting = self.reporting["records"].get(fact["id"])
            if reporting is None:
                raise Refusal("reporting_context_unavailable", "cached reporting context is unavailable for " + fact["id"])
            fact["source_record"]["reporting"] = deepcopy(reporting)
        return fact

    @staticmethod
    def property(fact):
        return {"node": "Property", **{k: fact[k] for k in ("entity", "metric", "scope")}}

    def bundle(self, receipt):
        if receipt.get("outcome") != "answered":
            raise Refusal("invalid_receipt", "only answered engine receipts may enter NLG")
        if not self.source_current:
            raise Refusal("reporting_context_stale", "reporting context does not match the fact database")
        plan = receipt["plan"]
        annotation = receipt["annotation"]
        if annotation.get("unplaced") or annotation.get("ignored") or plan != resolve_checked(annotation["tree"], execution_module()):
            raise Refusal("invalid_receipt", "receipt plan disagrees with its parser annotation")
        cases, groups = plan["cases"], receipt["facts"]
        if len(cases) != len(groups) or any(len(group) != 1 for group in groups):
            raise Refusal("invalid_receipt", "execution receipt has incomplete evidence")
        domain = self.domain()
        facts = [self.fact(domain, group[0], case) for case, group in zip(cases, groups)]
        if receipt.get("evidence") != [group[0] for group in groups]:
            raise Refusal("invalid_receipt", "receipt evidence disagrees with selected facts")
        # All new reviewed shapes share this gate, including gt/lt comparisons.
        # Legacy four-quarter sums retain their existing derivation verifier.
        from .financial_binding import contracts, verify_reporting
        reviewed = [fact for fact in facts if fact['metric'] in contracts()]
        if reviewed and (plan['op'] not in ('sum', 'avg') or annotation['tree'].get('over', {}).get('quarter_set')):
            try:
                verify_reporting(reviewed, self.reporting, plan['op'] in ('sum', 'avg'))
            except ValueError as error:
                raise Refusal('reporting_context_unavailable', str(error)) from error
        quantity = lambda value: {"node": "Quantity", "value": str(value), "unit": "USD"}
        if plan["op"] in ("sum", "avg"):
            expected = [(c["company"], c["metric"], c["period"]["fiscal_year"], c["period"].get("quarter")) for c in cases]
            first = expected[0]
            qs = annotation['tree'].get('over', {}).get('quarter_set')
            quarters = qs['quarters'] if qs else [1, 2, 3, 4]
            if expected != [(first[0], first[1], first[2], q) for q in quarters] or plan["need"] != len(quarters):
                raise Refusal("invalid_receipt", "aggregate receipt must cover exactly its requested quarters")
            context_case = {**cases[0], "period": {"kind": "fiscal_year", "fiscal_year": first[2]}}
            context = self.scope(domain, context_case)
            group = context + "quarters"
            domain["groups"][group] = {"axis": "scope", "members": [f["scope"] for f in facts],
                                       "noun": {"head": "quarter"}, "context": context}
            left = {"node": "Aggregate", "fn": "average" if plan["op"] == "avg" else "sum", "over": {"node": "Series", "entity": facts[0]["entity"], "metric": facts[0]["metric"], "group": group}}
            statement, truth = {"node": "Relation", "op": "eq", "left": left, "right": quantity(receipt["result"])}, True
        elif plan['op'] == 'lookup' and len(facts) == 1:
            statement, truth = {'node': 'Relation', 'op': 'eq', 'left': self.property(facts[0]),
                                'right': quantity(receipt['result'])}, True
        elif plan["op"] in ("gt", "lt", "gte", "lte", "eq") and len(facts) == 1 and type(receipt["result"]) is bool:
            op = {'gte': 'lt', 'lte': 'gt'}.get(plan['op'], plan['op'])
            statement = {"node": "Relation", "op": op, "left": self.property(facts[0]), "right": quantity(plan["limit"])}
            if plan['op'] in ('gte', 'lte'):
                statement = {'node': 'Not', 'expr': statement}
            truth = receipt["result"]
        else:
            raise Refusal("unsupported_execution", "receipt operation is not supported")
        return {"domain": domain, "facts": facts, "input": {"version": 2, "statement": statement, "result": truth}}
