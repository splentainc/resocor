"""Reviewed glossary roles explained by typed domain structure, never generic chatter."""


def accounted(items, index, view, glossary):
    item = items[index]
    row = glossary[item["row"]] if item["row"] is not None else {}
    value = row.get("valid_phrase", "")
    if not value.startswith("syntax:"):
        return False
    role = value.removeprefix("syntax:")
    types = [view(x)["phrase_type"] for x in items]
    entities = [n for n, t in enumerate(types) if t == "entity"]
    metrics = [n for n, t in enumerate(types) if t == "metric"]
    periods = [n for n, t in enumerate(types) if t.startswith("period_")]
    relations = {"compare", "compare_op", "aggregation", "arithmetic", "arithmetic_diff", "change", "change_pct", "change_series", "negation"}
    comparison = bool(set(types) & {"compare", "compare_op"})
    forecast = bool(set(types) & {"forecast", "hypothetical"})
    ratio = len(metrics) == 2 and any(t == "arithmetic" and view(x)["valid_phrase"] == "div" for t, x in zip(types, items))
    domain = [n for n, t in enumerate(types) if t not in {"grammar", "unlisted", "grouping_cue"}]
    if role == "prefix":
        return bool(entities and metrics) and (not domain or index < domain[0])
    if role == "suffix":
        return bool(entities and metrics) and (not domain or index > domain[-1])
    if role == "ratio_tail":
        return ratio and index > metrics[-1]
    if role == "ratio_wrapper":
        return ratio
    if role == "aggregation_frame":
        return "aggregation" in types and len(metrics) == 1 and len(entities) == 1
    if role == "coordination":
        return bool(set(types) & relations) and (len(entities) > 1 or len(metrics) > 1 or len(periods) > 1 or "negation" in types)
    if role == "proform":
        return bool(set(types) & {"modifier", "modifier_tail", "compare", "compare_op", "negation"})
    if role == "period_link":
        return any(n > index and types[n] in {"period_phrase", "period_span_n", "period_range", "step", "step_modifier"} for n in domain[:])
    if role == "motion_frame":
        return bool(metrics) and bool(set(types) & {"change", "change_pct", "change_series"})
    if role == "selection_frame":
        return comparison or "step_which" in types
    if role == "forecast_frame":
        return bool(metrics) and forecast
    if role == "comparison_frame":
        return bool(metrics) and comparison
    if role == "measure_verb":
        return bool(metrics and entities)
    if role == "series_frame":
        return bool(metrics) and bool(set(types) & {"step", "step_modifier"})
    if role == "frequency_frame":
        return bool(metrics and periods) and "negation" in types
    if role == "absolute_change_frame":
        return "change" in types and "change_pct" not in types
    if role == "clause_separator":
        right = types[index + 1] if index + 1 < len(types) else None
        left = items[index - 1] if index else {}
        return right == "grammar" or bool(left.get("in_premise")) or ratio or (index > 0 and types[index - 1] == "period_except")
    if role == "parenthesis":
        return types.count("arithmetic") <= 1 and sum(t in {"compare", "compare_op"} for t in types) <= 1
    if role.startswith("metric:"):
        return any(view(items[n])["valid_phrase"] == role.removeprefix("metric:") for n in metrics)
    return False
