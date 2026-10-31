"""Composition by template: the query tree for a bag of typed phrases, learned from an annotator's tree and applied by the engine.

signature = the sorted types of the question's significant phrases (entities, metrics, periods, relation words, modifiers, ...).
template  = the annotator's tree with every leaf replaced by a reference to a phrase of the question:
            "$entity.1"            the text of the first entity
            "@compare_op.1"        the glossary valid_phrase of the first comparison word (gt, lt, ...)
            "$period_range.1.from" the start of the first range
Two questions with the same signature get the same tree shape. A signature the table does not hold is not composed: the engine abstains.
"""
import json

from annotation_schema import leaf

LEAF_KEYS = {"entity", "metric", "name", "period", "from", "to", "step", "coverage", "horizon", "text", "value", "except", "premise", "basis"}  # strings that come from the question
VALUE_KEYS = {"fn", "op", "kind", "returns"}  # strings chosen by a glossary row's valid_phrase
PREFER = {"entity": ["entity"], "metric": ["metric"], "name": ["metric"], "step": ["step", "step_modifier", "step_which.unit"], "coverage": ["coverage"],
          "text": ["quantity", "modifier", "modifier_not"], "period": ["period_phrase", "period_span_n"], "horizon": ["period_phrase", "period_span_n"],
          "from": ["period_range.from", "period_phrase", "period_span_n", "period_bound.value"], "to": ["period_range.to", "period_phrase", "period_span_n", "period_bound.value"],
          "value": ["period_bound.value"], "except": ["period_except"], "premise": ["premise"], "basis": ["change_basis"]}


def signature(phrases: list) -> str:
    return json.dumps(sorted(p["type"] for p in phrases if not p["type"].startswith("p:")))  # words inside a premise do not shape the tree


def numbered(phrases: list):
    """({"entity.1": text, "period_range.1.from": text, ...}, {"compare_op.1": valid_phrase, ...}), numbered in sentence order per type."""
    count, text, valid = {}, {}, {}
    for p in phrases:
        count[p["type"]] = n = count.get(p["type"], 0) + 1
        key = f"{p['type']}.{n}"
        text[key], valid[key] = p["text"], p.get("valid")
        for sub, t in p.get("parts", {}).items():
            text[f"{key}.{sub}"] = t
    return text, valid


def _kind(key: str) -> str:
    parts = key.split(".")
    return parts[0] + ("." + parts[2] if len(parts) == 3 else "")


def induce(tree: dict, text: dict, valid: dict):
    """(template, []) or (None, [what could not be tied to a phrase of the question])."""
    missing = []

    def find(key, s):
        keys = sorted(k for k, t in text.items() if leaf(t) == leaf(s))
        for want in PREFER.get(key, []):
            for k in keys:
                if _kind(k) == want:
                    return "$" + k
        return "$" + keys[0] if keys else None

    def value(v):
        for k in sorted(valid):
            if valid[k] == v:
                return "@" + k
        missing.append(f"unbacked value {v!r}")  # no phrase of the question carries it, so every question with this signature would get it
        return v

    def walk(t, key=None):
        if isinstance(t, dict):
            return {k: (v if k == "node" else value(v) if k in VALUE_KEYS and isinstance(v, str) else walk(v, k)) for k, v in t.items() if not (k == "returns" and v == "value")}  # "value" is the default
        if isinstance(t, list):
            return [walk(v, key) for v in t]
        if isinstance(t, str) and key in LEAF_KEYS:
            ph = find(key, t)
            if ph is None:
                missing.append(t)
                return t
            return ph
        return t

    template = walk(tree)
    return (None, missing) if missing else (template, [])


def fill(template, text: dict, valid: dict):
    """The tree, or None when a reference points at a phrase the question does not have."""
    bad = []

    def walk(t):
        if isinstance(t, dict):
            return {k: walk(v) for k, v in t.items()}
        if isinstance(t, list):
            return [walk(v) for v in t]
        if isinstance(t, str) and t[:1] in ("$", "@"):
            v = (text if t[0] == "$" else valid).get(t[1:])
            if v is None:
                bad.append(t)
            return v
        return t

    out = walk(template)
    return None if bad else out
