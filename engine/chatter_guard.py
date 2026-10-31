"""Bridge shared generic chatter to the script-based engine's token spans.

Legacy 'grammar' rows are not proof of harmlessness. Only structural glue,
reviewed chatter, and context consumed by a typed rule can become filler.
"""
from reporting_verbs import license_reporting
from construction_rules import license_constructions
from seed_vocabulary import license_seed_requests, lexical_frame
from importlib import import_module
from pathlib import Path
import sys
from domain_syntax_rules import accounted

directory = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, directory)
try:
    _shared = import_module("chatter")
finally:
    sys.path.remove(directory)

# Domain syntax, not generic chatter. Open-class narrative, question intent,
# modal auxiliaries and numerical words deliberately have no blanket exemption.
STRUCTURAL = frozenset("a an the of in on at by for with from into onto during as to "
                       "is are was were be been being am do does did have has "
                       "it its their his her 's".split())
PUNCTUATION = frozenset(",.!?;:")


def domain_syntax(question, items, index, view):
    """Existing finance question syntax, outside the shared chatter dataset.

    Each open-class allowance requires the typed domain meaning it introduces:
    numeric interrogation, a named reported metric, a series, or entity choice.
    It cannot explain narrative, qualifiers, a missing operation or new nouns.
    """
    it = items[index]
    raw = question[it["ch0"]:it["ch1"]].casefold()
    types = [view(x)["phrase_type"] for x in items]
    metric = "metric" in types
    if raw in {"what", "whats"} and metric:
        return True
    if raw in {"how", "much"} and metric:
        if raw == "how" and "change_series" in types:
            return True  # movement interrogation, distinct from an amount request
        # Both words must form the numeric interrogative, never 'how ...' alone.
        neighbour = index + 1 if raw == "how" else index - 1
        return (0 <= neighbour < len(items)
                and question[items[neighbour]["ch0"]:items[neighbour]["ch1"]].casefold()
                == ("much" if raw == "how" else "how"))
    if raw in {"reported", "report", "reports", "earn", "earned"} and metric:
        return True
    if raw in {"figure", "figures", "number"} and index:
        return metric and types[index - 1] in {"metric", "modifier", "step_modifier", "period_unit"}
    if raw == "trended":
        return metric and bool(set(types) & {"period_range", "step", "step_modifier"})
    entities = sum(t == "entity" for t in types)
    if raw in {"who", "which", "or", "had"}:
        return entities >= 2 and bool(set(types) & {"compare", "compare_op"})
    if raw == "and" and 0 < index < len(items) - 1:
        return types[index - 1] != "grammar" and types[index + 1] != "grammar"
    return False


def guard(question, items, view, glossary):
    """Preserve domain spans, apply shared chatter, quarantine unsafe grammar.

    Runs after typed phrase composition: semantic domain spans veto an entire
    chatter match; reviewed generic chatter can explain only remaining text.
    """
    license_seed_requests(question, items, view, glossary)
    license_constructions(question, items, view)
    license_reporting(question, items, view, glossary)
    def lexical_candidate(item):
        typ = view(item)['phrase_type']
        if typ in {'grammar', 'unlisted'}:
            return True
        if typ != 'seed_construction' or item['row'] is None:
            return False
        return lexical_frame(glossary[item['row']])
    protected = [(it['ch0'], it['ch1']) for it in items if not lexical_candidate(it)]
    lookup = _shared.generic().lookup(question, protected=protected)
    for index, it in enumerate(items):
        if it.get("consumed"):
            continue  # a typed composition rule already accounted for this span
        if view(it)["phrase_type"] == "grammar" and accounted(items, index, view, glossary):
            it["domain_syntax"] = True
            continue
        typ = view(it)["phrase_type"]
        original = glossary[it["row"]] if it["row"] is not None else {}
        original_type = original.get("phrase_type")
        if (not it.get("in_premise") and not it.get("consumed")
                and (original_type == "modifier_tail" or typ in {"grouping_cue", "hypothetical_aux"})
                and not (typ == "hypothetical_aux" and domain_syntax(question, items, index, view))):
            # 'only'/'alone', grouping and a bare hypothetical auxiliary cannot
            # disappear just because an old SEG mapping calls them filler.
            it["ptype"], it["seg_type"] = "unlisted", "unknown"
            continue
        if not lexical_candidate(it) or it.get("in_premise"):
            continue
        raw = question[it["ch0"]:it["ch1"]].casefold()
        if it.get("seg_type") == "hypothetical":
            continue  # a typed premise rule retains this auxiliary's meaning
        if (original.get("phrase_type") == "count_query" and typ == "grammar"
                and any(view(x)["phrase_type"] == "metric"
                        and (view(x)["valid_phrase"] or "").startswith("not_held:") for x in items)):
            it["seg_type"] = "aggregation"  # retain count intent even for an unavailable metric
            continue
        match = next((m for m in lookup.matches if m.start <= it["ch0"] and it["ch1"] <= m.end), None)
        if match:
            it["ptype"], it["chatter"] = "grammar", match.as_dict()
        elif typ == "grammar" and domain_syntax(question, items, index, view):
            continue
        elif raw in STRUCTURAL or raw in PUNCTUATION:
            # Unknown lexical items cannot be rescued by a structural allowlist.
            if typ == "grammar" or raw in PUNCTUATION:
                continue
        else:
            # Even known grammar rows can be meaningful (why, margin, only).
            it["ptype"], it["seg_type"] = "unlisted", "unknown"
    preserve_unknown_glue(question, items, view)
    return lookup


def preserve_unknown_glue(question, items, view):
    """Keep syntax adjacent to unexplained content in its unknown span.

    This preserves multiword qualifications whose full domain phrase is not yet
    registered, without adding phrase-specific exceptions to generic chatter.
    It affects only requests already forced to refuse by an unknown word.
    """
    glue = {"a", "an", "the", "at", "to", "of", "for", "with", "by", "as", "in", "on", "from"}
    changed = True
    while changed:
        changed = False
        for index, item in enumerate(items):
            if view(item)["phrase_type"] != "grammar" or item.get("chatter") or item.get("consumed"):
                continue
            if question[item["ch0"]:item["ch1"]].casefold() not in glue:
                continue
            if any(0 <= n < len(items) and view(items[n])["phrase_type"] == "unlisted"
                   for n in (index - 1, index + 1)):
                item["ptype"], item["seg_type"] = "unlisted", "unknown"
                changed = True
