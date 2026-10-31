"""Steps 1-3: domain vocabulary/templates from knowledge/glossary.db,
with separate shared generic chatter and conservative domain syntax checks.

lookup_all: every phrase is looked up in one pass (longest phrase match, then regex rows for single tokens). A token no row explains
is `unlisted`. flags: step 1 view, derived: lookup is false only for grammar. build_tree: composes the tree from the looked-up types.
Generic chatter is a separate shared dataset; domain spans take precedence.
"""
import json
import os
import re

from annotation_schema import validate
from metric_precedence import check_competing
from number_words import compose as compose_numbers
from tokenizer import tokenize

HERE = os.path.dirname(os.path.abspath(__file__))
import composition
from phrase_rules import PhraseRules, PERIOD_TYPES
from knowledge_store import load as load_knowledge, load_phrases
from typos import alias_row
from chatter_guard import guard as guard_chatter
from seed_vocabulary import lexical_frame
from pydantic import ValidationError

GLOSS = load_phrases(os.path.join(HERE, "knowledge", "glossary.db"))
TEMPLATES = None  # loaded only when tree construction needs the historical template path
PHRASES, REGEX, LONGEST = {}, [], 1
for i, r in enumerate(GLOSS):
    if r["match"] == "regex":
        REGEX.append((re.compile(r["user_phrase"] + r"$", re.I), i))
    else:
        key = tuple(t.lower() for t, _, _ in tokenize(r["user_phrase"]))
        prior = PHRASES.get(key)
        if prior is None or (GLOSS[prior]["phrase_type"] == "grammar" and r["phrase_type"] != "grammar"):
            PHRASES[key] = i  # domain meanings override legacy grammar aliases
        LONGEST = max(LONGEST, len(key))

SEG = {"period_span_n": "period", "change_basis": "change", "bound_after": "period", "arithmetic_diff": "change", "change_series": "change", "modifier_tail": "modifier", "number_word": "period", "step_which": "period", "entity": "entity", "metric": "metric", "aggregation": "aggregation", "step": "period", "coverage": "period", "period_year": "period",
       "number": "period", "grammar": "filler", "unlisted": "unknown", "compare_op": "operator", "quantity": "quantity",
       "period_date": "period", "month": "unknown", "arithmetic": "operator",
       "negation": "negation", "bound": "period", "range_marker": "period", "hypothetical": "hypothetical", "forecast": "hypothetical",
       "compare": "operator", "quantifier": "modifier", "currency": "quantity", "scale": "quantity", "modifier": "modifier", "step_modifier": "modifier", "change": "change", "change_pct": "change",
       "period_phrase": "period", "period_except": "period", "period_range": "period", "period_bound": "period", "period_marker": "period", "period_unit": "period",
       "period_rel": "period", "ordinal": "period", "period_fy": "period", "period_quarter": "period", "period_yr2": "period", "period_date_num": "period"}
UNCOMPOSED = {"bound_after", "negation", "bound", "range_marker", "hypothetical", "compare", "quantifier", "currency", "scale"}  # typed, but no tree rule yet
SEG.update(amount_unit="change", change_units="change", hypothetical_aux="filler")
SEG.update(unsupported_op="operator", unsupported_aggregation="aggregation")
SEG["seed_construction"] = "unknown"
SEG["quarter_set"] = "period"
SEG["count_query"] = "aggregation"
SEG["source_qualifier"] = "modifier"
SEG["modifier_tail"] = "filler"
SEG["relation_link"] = "operator"
SEG["grouping_cue"] = "filler"
UNCOMPOSED.add("relation_link")
UNCOMPOSED.update(("unsupported_op", "unsupported_aggregation"))
_unknown = {r["phrase_type"] for r in GLOSS} - set(SEG)
if _unknown:  # Invalid phrase types must fail explicitly instead of becoming unlisted words.
    raise ValueError(f"glossary has phrase types step1 does not know: {sorted(_unknown)}")


def lookup_all(question, split_metric=None):
    """One item per matched phrase or token: {start, end, ch0, ch1, text, row}; row is a glossary row index, or None (unlisted).
    Longest phrase first, left to right; a single token that matches a regex row is classified by it."""
    toks = tokenize(question)
    low = [t.lower() for t, _, _ in toks]
    items, k = [], 0
    domain_spans = []
    for pos in range(len(toks)):
        for size in range(1, min(LONGEST, len(toks) - pos) + 1):
            hit = PHRASES.get(tuple(low[pos:pos + size]))
            if hit is not None and GLOSS[hit]["phrase_type"] != "grammar" and not lexical_frame(GLOSS[hit]):
                domain_spans.append((pos, pos + size))
    while k < len(toks):
        e, row = None, None
        for n in range(min(LONGEST, len(toks) - k), 0, -1):
            hit = PHRASES.get(tuple(low[k:k + n]))
            if hit is not None:
                if GLOSS[hit]["phrase_type"] == "grammar" and any(k < b and a < k + n for a, b in domain_spans):
                    continue
                e, row = k + n, hit
                break
        if split_metric and k == split_metric[0]:
            _, e, row = split_metric
        if row is None:
            e = k + 1
            row = next((i for pat, i in REGEX if pat.match(toks[k][0])), None)
        if row is None:
            for n in range(min(LONGEST, len(toks) - k), 0, -1):
                candidate = alias_row(" ".join(low[k:k + n]), GLOSS)
                if candidate is not None:
                    row, e = candidate, k + n
                    break
        items.append({"start": k, "end": e, "ch0": toks[k][1], "ch1": toks[e - 1][2], "row": row})
        k = e
    items = compose_numbers(items, question, view)
    for compose in (_quantities, _operands, _verb_metric):  # each pass reads the previous one's items
        items = compose(items, question)
    items = PhraseRules(view, GLOSS).compose(items, question)
    guard_chatter(question, items, view, GLOSS)
    return toks, items


def _quantities(items, question):
    """[currency] number [scale], with at least one of currency or scale, is one quantity: '$300 billion'."""
    t = lambda it: view(it)["phrase_type"]
    out, k = [], 0
    while k < len(items):
        if t(items[k]) == "currency" and k + 1 < len(items) and t(items[k + 1]) == "quantity":  # $10bn
            out.append(PhraseRules(view, GLOSS)._span(items[k:k + 2], "quantity"))
            k += 2
            continue
        j = k + 1 if t(items[k]) == "currency" else k
        if j < len(items) and t(items[j]) == "number":
            e = j + 1 + (1 if j + 1 < len(items) and t(items[j + 1]) == "scale" else 0)
            if e - k > 1:
                run = items[k:e]
                out.append({"start": run[0]["start"], "end": run[-1]["end"], "ch0": run[0]["ch0"], "ch1": run[-1]["ch1"], "row": None, "ptype": "quantity"})
                k = e
                continue
        out.append(items[k])
        k += 1
    return out


def _verb_metric(items, question):
    """"How much did X earn in 2022?": when the question names no metric, 'earn' or 'earned' is the metric (net income)."""
    if any(view(it)["phrase_type"] == "metric" for it in items):
        return items
    for it in items:
        if question[it["ch0"]:it["ch1"]].lower() in ("earn", "earned"):
            it["ptype"], it["valid"] = "metric", "net_income"
            break
    return items


def _operands(items, question):
    """A number straight after an arithmetic word (optionally 'by') is a quantity operand: 'multiplied by 3'."""
    t = lambda n: view(items[n])["phrase_type"] if 0 <= n < len(items) else None
    for k in range(len(items)):
        if t(k) == "arithmetic":
            j = k + 1
            if t(j) == "grammar" and question[items[j]["ch0"]:items[j]["ch1"]].lower() == "by":
                j += 1
            if t(j) == "number":
                items[j]["ptype"] = "quantity"
    for k in range(1, len(items)):  # "ExampleCo and SampleCo together": an operator only straight after an entity
        if t(k) == "grouping_cue" and t(k - 1) == "entity":
            items[k]["ptype"], items[k]["valid"] = "aggregation", "add"
    return items


def view(item):
    if item.get("ptype"):
        return {"phrase_type": item["ptype"], "valid_phrase": item.get("valid")}
    r = GLOSS[item["row"]] if item["row"] is not None else None
    return {"phrase_type": r["phrase_type"] if r else "unlisted", "valid_phrase": (r["valid_phrase"] or None) if r else None}


def flags(question):
    """Step 1 view: segments with the derived flag (false only for grammar)."""
    doc, items = lookup_all(question)
    return [{"text": it["text"], "lookup": view(it)["phrase_type"] != "grammar"} for it in items]


def unlisted_groups(items, question):
    """Runs of adjacent unlisted tokens (ignoring bare punctuation), as (first_item_index, last_item_index, text)."""
    out, k = [], 0
    while k < len(items):
        if view(items[k])["phrase_type"] == "unlisted" and re.search(r"\w", question[items[k]["ch0"]:items[k]["ch1"]]):
            j = k
            while j + 1 < len(items) and view(items[j + 1])["phrase_type"] == "unlisted":
                j += 1
            out.append((k, j, question[items[k]["ch0"]:items[j]["ch1"]]))
            k = j + 1
        else:
            k += 1
    return out


SLOT = {"quarter_set", "period_span_n", "step_which", "entity", "metric", "period_year", "period_date", "period_phrase", "period_except", "period_range", "period_bound", "step", "step_modifier", "coverage", "quantity"}  # words that fill the query's slots
RELATION = {"compare", "compare_op", "aggregation", "negation", "hypothetical", "forecast", "bound", "range_marker", "arithmetic", "arithmetic_diff", "change", "change_pct"}


def _phrases(items, types, question):
    """The significant phrases of a question, in sentence order: what composition.signature and numbered work on."""
    out = []
    for n, (it, t) in enumerate(zip(items, types)):
        if t in ("grammar", "unlisted", "modifier_tail", "change_series", "change_units", "hypothetical_aux", "grouping_cue") or it.get("ignored") or it.get("consumed"):
            continue
        if t == "number" and n > 0 and types[n - 1] == "coverage":  # the 4 of "all 4 quarters"
            continue
        p = {"type": ("p:" if it.get("in_premise") else "") + ("modifier_not" if it.get("negated") else t), "text": question[it["ch0"]:it["ch1"]].strip(), "valid": view(it)["valid_phrase"]}
        if t == "coverage" and n + 1 < len(items) and types[n + 1] == "number":
            p["text"] += " " + question[items[n + 1]["ch0"]:items[n + 1]["ch1"]]
        if t == "step_which":
            p["parts"] = {"unit": p["text"].split()[-1]}
        if t == "period_range":
            p["parts"] = {"from": question[slice(*it["from_ch"])], "to": question[slice(*it["to_ch"])]}
        if t == "period_bound":
            p["parts"], p["valid"] = {"value": question[slice(*it["value_ch"])]}, it["op"]
        out.append(p)
        if it.get("premise"):
            out.append({"type": "premise", "text": question[slice(*it["premise"])].strip(), "valid": None})
    return out


def phrases_of(question, split_metric=None):
    """(text, valid, signature) for a question, as composition templates see it."""
    doc, items = lookup_all(question, split_metric)
    ph = _phrases(items, [view(it)["phrase_type"] for it in items], question)
    text, valid = composition.numbered(ph)
    return text, valid, composition.signature(ph)


def missing_slots(t):
    """Slots a Lookup or Series needs and lacks: entity, metric, and a period (a Series needs a from or a to). Other nodes are checked through their children."""
    n = t["node"]
    if n == "Lookup":
        return [k for k in ("entity", "metric", "period") if not t.get(k)]
    if n == "Series":
        return [k for k in ("entity", "metric") if not t.get(k)] + ([] if t.get("from") or t.get("to") else ["period"])
    if n == "Forecast":  # a forecast is never executed, so it carries no period of its own
        return [k for k in ("entity", "metric") if not t["expr"].get(k)]
    if n == "Change":
        return [k for k in ("entity", "metric") if not t["of"].get(k)]
    return [g for k in ("over", "left", "right", "expr", "of") if isinstance(t.get(k), dict) and t[k].get("node") for g in missing_slots(t[k])]


def segments_of_items(items):
    """Export the engine's independently inferred types, retaining every character."""
    segs, cur = [], None
    for it in items:
        typ = "hypothetical" if it.get("in_premise") else "filler" if it.get("ignored") else it.get("seg_type", SEG[view(it)["phrase_type"]])
        if cur and cur["type"] == typ:
            cur["text"] += it["text"]
        else:
            cur = {"id": f"s{len(segs) + 1}", "text": it["text"], "type": typ}
            segs.append(cur)
    return segs


def segment_question(question, qid=0):
    """Raw question -> generated segments. No expected segments or tree required."""
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a nonempty string")
    _, items = lookup_all(question)
    segs = segments_of_items(items)
    return {"id": qid, "segments": segs,
            "unplaced": [s["id"] for s in segs if s["type"] == "unknown"]}


def _step1(question, qid=0, split_metric=None):
    """Only reviewed, context-valid chatter is filler. Unknown content anywhere
    remains an explicit unknown segment and forces abstention, even with a template."""
    doc, items = lookup_all(question, split_metric)
    types = [view(it)["phrase_type"] for it in items]
    groups = [g for g in unlisted_groups(items, question) if not items[g[0]].get("in_premise")]  # a premise is quoted, not read
    bindings = [it["scope_binding"] for it in items if it.get("scope_binding")]
    ignored = []  # compatibility field: no unknown text is ever dropped
    segs = segments_of_items(items)
    unk = [s["id"] for s in segs if s["type"] == "unknown"]
    reasons = []
    if groups:
        reasons.append("unlisted content: " + ", ".join(repr(g[2]) for g in groups))
    elif unk:
        reasons.append("unlisted content: " + ", ".join(repr(s["text"]) for s in segs if s["type"] == "unknown"))
    has_rel = any(t in RELATION for t in types)
    for it, t in zip(items, types):
        if t == "source_qualifier":
            reasons.append(f"unsupported source qualifier: {it['text'].strip()}")
        if t in UNCOMPOSED and not it.get("consumed"):
            reasons.append(f"{t} word {it['text'].strip()!r} has no composition rule yet")
        if t == "month":
            reasons.append(f"month {it['text'].strip()!r} is not part of a date with a year")
    n_per = sum(types.count(x) for x in ("quarter_set", "period_phrase", "period_span_n", "period_range", "period_bound"))
    if n_per > 1:
        reasons.append(f"{n_per} periods need a composition rule that is not built yet")
    for it, t in zip(items, types):
        if t in PERIOD_TYPES:
            reasons.append(f"period word {it['text'].strip()!r} does not form a period")
    cats = {}
    for it, t in zip(items, types):
        c = {"aggregation": "aggregation", "change": "change", "change_pct": "change", "compare_op": "comparison", "forecast": "forecast", "arithmetic": "arithmetic"}.get(t)
        if c:
            cats.setdefault(c, []).append(it)
    if len(cats) > 1:
        reasons.append("more than one relation (" + ", ".join(f"{c}: {'/'.join(x['text'].strip() for x in v)}" for c, v in cats.items()) + ") has no composition rule yet")
    if len({view(x)["valid_phrase"] for x in cats.get("aggregation", [])}) > 1:
        reasons.append("aggregation words disagree: " + ", ".join(x["text"].strip() for x in cats["aggregation"]))
    has_series = any(t in ("quarter_set", "step", "step_modifier", "period_range", "period_bound") for t in types)
    if "aggregation" in cats and not has_series:
        reasons.append("aggregation needs a series (a range or a step)")
    if "change" in cats and "period_range" not in types:
        reasons.append("change needs a from-to range")
    if "comparison" in cats and "quantity" not in types:
        reasons.append("comparison needs a quantity (a second entity is not built yet)")
    if "forecast" in cats and any(t in ("period_range", "period_bound") for t in types):
        reasons.append("forecast with a range has no composition rule yet")
    if any(it.get("future") for it in items) and "forecast" not in {c for c in types}:
        reasons.append("future period without a forecast word")
    if "period_except" in types:  # the hand composition has no exclusion; only a template can carry it
        reasons.append("exclusion of periods needs a composition template")
    if "step_which" in types:
        reasons.append("a which-period question needs a composition template")
    if "period_span_n" in types:  # the hand composition reads single periods only
        reasons.append("a counted period needs a composition template")
    if "arithmetic_diff" in types:  # no hand composition reads it, so only a template may build the tree
        reasons.append("an arithmetic difference needs a composition template")
    arith = [n for n, t in enumerate(types) if t == "arithmetic"]
    met_idx = [n for n, t in enumerate(types) if t == "metric"]
    arith_ok = len(arith) == 1 and len([n for n in met_idx if n < arith[0]]) == 1 and len([n for n in met_idx if n > arith[0]]) == 1
    if arith and not arith_ok:
        reasons.append("arithmetic needs exactly one metric on each side")
    for name, kind in (("entities", "entity"), ("metrics", "metric")):
        found = sorted({GLOSS[it["row"]]["valid_phrase"] for it, t in zip(items, types) if t == kind})
        if kind == "metric" and arith_ok:
            continue
        if len(found) > 1:
            reasons.append(f"{len(found)} {name} ({', '.join(found)}) " + ("need a composition rule that is not built yet" if has_rel else "with no relation between them"))
    # the hand shapes below read entity, metric and period only: anything else they would drop silently goes to a template
    hand_shape = arith_ok or "change" in cats or "forecast" in cats or ("comparison" in cats and "quantity" in types)
    if hand_shape and any(t in ("quarter_set", "step", "step_modifier", "coverage") for t in types):
        reasons.append("a step with this relation needs a composition template")
    if arith_ok and any(t in ("modifier", "modifier_not") for t in types):
        reasons.append("an arithmetic question with modifiers needs a composition template")
    hard = [r for r in reasons if r.startswith(("unlisted content", "unsupported source qualifier", "month ", "period word ", "future period"))]  # no template can supply a missing meaning
    tree = None
    if reasons and not hard:
        text_, valid_, sig = phrases_of(question, split_metric)
        global TEMPLATES
        if TEMPLATES is None:
            _, TEMPLATES = load_knowledge(os.path.join(HERE, "knowledge", "glossary.db"))
        tpl = TEMPLATES.get(sig)
        tree = composition.fill(tpl, text_, valid_) if tpl else None
        if tree is None:
            reasons.append(f"no composition for signature {sig}")
    if reasons and tree is None:
        return validate({"id": qid, "segments": segs, "bindings": bindings or None, "tree": {"node": "Unsupported", "reason": "; ".join(reasons), "segments": unk}, "unplaced": unk, "ignored": ignored}, question)
    by = lambda t: [it for it in items if view(it)["phrase_type"] == t]
    text = lambda t: " ".join(it["text"].strip() for it in by(t)) or None
    raw = lambda it: question[it["ch0"]:it["ch1"]]
    # a number right before a step unit, after a coverage word, belongs to the coverage: "all 4 quarters"
    cov = []
    for k, it in enumerate(items):
        t = view(it)["phrase_type"]
        if t == "coverage":
            cov.append(it["text"].strip())
            if k + 2 < len(items) and view(items[k + 1])["phrase_type"] == "number" and view(items[k + 2])["phrase_type"] in ("step", "step_modifier"):
                cov.append(items[k + 1]["text"].strip())
    cmp_ = [it for it in items if view(it)["phrase_type"] == "compare_op"]
    qty = by("quantity")
    ent, met, step = text("entity"), text("metric"), text("step") or text("step_modifier")
    pp, rng, bnd = by("period_phrase"), by("period_range"), by("period_bound")
    per = (pp[0].get("resolved_period") or raw(pp[0])) if pp else None
    quals = [{"node": "Not", "expr": {"node": "Modifier", "text": raw(it)}} if it.get("negated") else {"node": "Modifier", "text": raw(it)} for it in by("modifier")]
    metric = {"name": met, "qualifiers": quals} if quals and met else met
    ops = by("aggregation")
    clean = lambda d: {k: v for k, v in d.items() if v is not None}
    if tree is not None:
        pass  # composed from a template
    elif arith_ok:
        mets = [items[n]["text"].strip() for n in met_idx]
        look = lambda m: {"node": "Lookup", "entity": ent, "metric": m, "period": per}
        tree = {"node": "Arith", "op": GLOSS[items[arith[0]]["row"]]["valid_phrase"], "left": look(mets[0]), "right": look(mets[1])}
    elif "change" in cats:
        kind = "pct" if any(view(it)["phrase_type"] == "change_pct" for it in items) else "abs"
        tree = {"node": "Change", "kind": kind, "of": {"node": "Lookup", "entity": ent, "metric": metric, "period": None},
                "from": question[slice(*rng[0]["from_ch"])], "to": question[slice(*rng[0]["to_ch"])]}
    elif "forecast" in cats:
        tree = {"node": "Forecast", "expr": {"node": "Lookup", "entity": ent, "metric": metric}, "horizon": per}
    elif cmp_ and qty:
        tree = {"node": "Compare", "op": view(cmp_[0])["valid_phrase"], "left": {"node": "Lookup", "entity": ent, "metric": metric, "period": per},
                "right": {"node": "Quantity", "text": qty[0]["text"].strip()}}
    elif by("quarter_set"):
        qs = by("quarter_set")[0]
        year = question[slice(*qs["year_ch"])]
        series = clean({"node": "Series", "entity": ent, "metric": metric,
                        "from": year, "to": year, "step": qs["step_text"],
                        "coverage": qs["coverage_text"], "quarter_set": qs["quarter_set"]})
        tree = {"node": "Aggregate", "fn": view(ops[0])["valid_phrase"], "over": series} if ops else series
    elif rng or bnd:
        if rng:
            lo_, hi_ = question[slice(*rng[0]["from_ch"])], question[slice(*rng[0]["to_ch"])]
        else:
            b, val = bnd[0], question[slice(*bnd[0]["value_ch"])]
            bound = {"node": "Bound", "op": b["op"], "value": val}
            lo_, hi_ = (None, bound) if b["op"] in ("lt", "lte") else (bound, None)
        series = clean({"node": "Series", "entity": ent, "metric": metric, "from": lo_, "to": hi_, "step": step, "coverage": " ".join(cov) or None})
        tree = {"node": "Aggregate", "fn": view(ops[0])["valid_phrase"], "over": series} if ops else series
    elif step or ops:
        series = clean({"node": "Series", "entity": ent, "metric": metric, "from": per, "to": per, "step": step, "coverage": " ".join(cov) or None})
        tree = {"node": "Aggregate", "fn": view(ops[0])["valid_phrase"], "over": series} if ops else series
    else:
        tree = {"node": "Lookup", "entity": ent, "metric": metric, "period": per}
    gaps, note = sorted(set(missing_slots(tree))), ""
    if gaps == ["period"] and tree["node"] == "Lookup" and not ignored:  # decision: no period means the latest figure, applied and stated in step 2
        gaps, note = [], "no period given: step 2 uses the latest reported figure and states its period and whether it is quarterly or annual"
    if gaps:
        tree = {"node": "Unsupported", "reason": "missing " + ", ".join(gaps), "segments": unk}
    try:
        return validate({"id": qid, "segments": segs, "bindings": bindings or None, "tree": tree, "unplaced": [], "ignored": ignored, "note": note}, question)
    except ValidationError:
        return validate({"id": qid, "segments": segs, "bindings": bindings or None, "tree": {"node": "Unsupported", "reason": "a composition template produced an invalid tree", "segments": unk}, "unplaced": unk, "ignored": ignored}, question)


def step1(question, qid=0):
    """Prefer complete reviewed metrics; abstain on distinct viable operator readings."""
    primary = _step1(question, qid)
    return check_competing(question, qid, primary, _step1, lookup_all, view, GLOSS, PHRASES)
