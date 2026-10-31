"""Legacy tree execution helpers over caller-supplied facts and calendar data.
The checked answer API is provided by engine.answer."""
import json
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
t = time.perf_counter()
from destructure_question import GLOSS, PHRASES, step1  # noqa: E402
from tokenizer import tokenize  # noqa: E402
from typos import alias_row  # noqa: E402
import fact_lookup  # noqa: E402
import fact_store  # noqa: E402
import fiscal_calendar  # noqa: E402

T_IMPORT = (time.perf_counter() - t) * 1000
t = time.perf_counter()
STORE, CAL = fact_store.load_facts(), fiscal_calendar.load_calendar()
T_STORE = (time.perf_counter() - t) * 1000


def valid(leaf, ptype):
    """Step 2 for a verbatim leaf: its glossary row of the given type."""
    key = tuple(x.lower() for x, _, _ in tokenize(leaf))
    row = GLOSS[PHRASES[key]] if key in PHRASES else None
    if row is None:
        fuzzy = alias_row(" ".join(key), GLOSS, ptype)
        row = GLOSS[fuzzy] if fuzzy is not None else None
    if row and row["valid_phrase"].startswith("not_held:"):
        raise LookupError(f"{row['valid_phrase'][9:]!r} is not held")  # refused by name
    return row["valid_phrase"] if row and row["phrase_type"] == ptype else None


def money(s):
    return float(re.sub(r"[^\d.]", "", s))


def quantity(text):
    num = scale = 1
    for tok, _, _ in tokenize(text):
        if re.fullmatch(r"[\d.,]+", tok):
            num = float(tok.replace(",", ""))
        elif tok.lower() in {k[0] for k in PHRASES if len(k) == 1}:
            r = GLOSS[PHRASES[(tok.lower(),)]]
            if r["phrase_type"] == "scale":
                scale = float(r["valid_phrase"])
    return num * scale


def resolve(tree):
    """Step 2 on a tree: glossary keys and calendar cases. Returns a plan the executor can run."""
    node = tree["node"]
    if node == "Aggregate":
        if tree["over"].get("quarter_set"):
            raise ValueError("explicit quarter sets require structured checked execution")
        s = tree["over"]
        company, metric = valid(s["entity"], "entity"), valid(s["metric"], "metric")
        fy = int(s["from"])
        need = int(re.search(r"\d+", s["coverage"]).group()) if s.get("coverage") else None
        assert valid(s["step"], "step") == "quarter"
        cases = [{"company": company, "metric": metric, "period": {"kind": "fiscal_quarter", "fiscal_year": fy, "quarter": q}} for q in (1, 2, 3, 4)]
        return {"op": "sum", "cases": cases, "need": need}
    if node == "Compare":
        if tree["op"] not in ("gt", "lt") or tree["left"].get("node") != "Lookup":
            raise NotImplementedError("comparison shape is represented but not executable")
        l = tree["left"]
        company, metric = valid(l["entity"], "entity"), valid(l["metric"], "metric")
        return {"op": tree["op"], "cases": [{"company": company, "metric": metric, "period": {"kind": "fiscal_year", "fiscal_year": int(l["period"])}}],
                "limit": quantity(tree["right"]["text"])}
    raise NotImplementedError(node)


def fetch(case):
    return fact_lookup.facts_for(STORE, CAL, case)


def run(question, qid):
    ns = time.perf_counter_ns
    t0 = ns()
    ann = step1(question, qid)
    tree = ann.tree.model_dump(by_alias=True, exclude_none=True)
    t1 = ns()
    plan = resolve(tree)
    t2 = ns()
    facts = [fetch(c) for c in plan["cases"]]
    t3 = ns()
    if plan["op"] == "sum":
        ok = all(len(f) == 1 for f in facts) and (plan["need"] is None or len(facts) == plan["need"])
        result = sum(money(f[0].value) for f in facts) if ok else None  # incomplete coverage: no total
    else:
        v = money(facts[0][0].value)
        result = (v > plan["limit"]) if plan["op"] == "gt" else (v < plan["limit"])
    t4 = ns()
    return result, facts, plan, [(t1 - t0) / 1e3, (t2 - t1) / 1e3, (t3 - t2) / 1e3, (t4 - t3) / 1e3]


