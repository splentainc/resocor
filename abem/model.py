"""Finite typed predicates, attributed observations and rule closure."""
from copy import deepcopy
from hashlib import sha256
from itertools import product
import json

VERSION = "abem-poc-1"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def digest(kind, value):
    data = value if isinstance(value, bytes) else canonical(value)
    label = kind.encode()
    return sha256(len(label).to_bytes(4, "big") + label
                  + len(data).to_bytes(8, "big") + data).hexdigest()


def key(atom):
    return canonical(atom[:2]).decode()


def bind(value, bindings):
    if isinstance(value, list):
        return [bind(v, bindings) for v in value]
    return bindings.get(value, value) if isinstance(value, str) else value


def ground(pack, parameters):
    names = sorted(parameters)
    choices = [sorted(e for e, t in pack["entities"].items()
                      if t == parameters[n]) for n in names]
    for values in product(*choices):
        yield dict(zip(names, values))


def validate_atom(pack, atom):
    if not isinstance(atom, list) or len(atom) != 3:
        raise ValueError("invalid atom shape")
    name, args, value = atom
    if name not in pack["predicates"]:
        raise ValueError(f"unknown predicate: {name}")
    contract = pack["predicates"][name]
    if len(args) != len(contract["args"]):
        raise ValueError(f"arity mismatch: {name}")
    for arg, kind in zip(args, contract["args"]):
        if pack["entities"].get(arg) != kind:
            raise ValueError(f"entity type mismatch: {name}/{arg}")
    if not any(type(value) is type(v) and value == v for v in contract["values"]):
        raise ValueError(f"invalid value: {name}/{value}")


def validate_inputs(pack, intent, snapshot):
    if intent["domain_pack"] != pack["id"]:
        raise ValueError("domain version mismatch")
    if intent["fact_snapshot"] != snapshot["id"]:
        raise ValueError("snapshot version mismatch")
    if type(snapshot["as_of"]) is not int:
        raise ValueError("as_of must be an explicit integer fixture tick")
    for field in ("max_expansions", "max_depth", "max_observation_rounds"):
        n = intent["search"][field]
        if type(n) is not int or n < 0 or n > 10000:
            raise ValueError(f"invalid finite budget: {field}")
    ranking = intent["ranking"]
    if not 1 <= ranking["max_results"] <= 10 or not 0 <= ranking["selected_rank"] < ranking["max_results"]:
        raise ValueError("invalid candidate selection bound")
    if set(ranking["weights"]) != {"risk", "cost", "length", "observations"}:
        raise ValueError("unknown ranking dimension")
    if any(type(w) is not int or not 0 <= w <= 1000 for w in ranking["weights"].values()):
        raise ValueError("weights must be bounded nonnegative integers")
    validate_atom(pack, intent["goal"])
    for atom in intent["required_observations"] + intent["invariants"] + intent["forbidden_effects"]:
        validate_atom(pack, atom)
    identifiers = [r["id"] for r in snapshot["facts"]]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("duplicate observation identifier")
    for record in snapshot["facts"]:
        if record.get("epistemic_kind", "observation") not in ("observation", "assumption"):
            raise ValueError("hypothetical or unknown evidence kind cannot enter observed snapshot")
        validate_atom(pack, record["atom"])
        expected = pack["predicates"][record["atom"][0]]["unit"]
        if record["unit"] != expected:
            raise ValueError(f"unit mismatch: {record['id']}")
        if not record["source"] or not (record["valid_from"] <= record["observed_at"]
                                         < record["valid_to"]):
            raise ValueError("invalid observation provenance/validity")
    for rule in pack["rules"]:
        for bindings in ground(pack, rule["parameters"]):
            for atom in rule["if"] + [rule["then"]]:
                validate_atom(pack, bind(atom, bindings))
    ids = [op["id"] for op in pack["operators"]]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate operator")
    for op in pack["operators"]:
        if op["kind"] not in ("observation", "action"):
            raise ValueError("unsupported effect")
        if type(op["cost"]) is not int or op["cost"] < 1:
            raise ValueError("operator cost must be a positive integer")
        if type(op["risk"]) is not int or not 0 <= op["risk"] <= 1000:
            raise ValueError("operator risk must be a bounded nonnegative integer")
        for bindings in ground(pack, op["parameters"]):
            for atom in op["requires"] + op["effects"]:
                validate_atom(pack, bind(atom, bindings))
    for decision in pack["decisions"]:
        if decision["hit_policy"] != "UNIQUE":
            raise ValueError("only finite UNIQUE decision tables supported")
        for bindings in ground(pack, decision["parameters"]):
            for atom in decision["inputs"]:
                validate_atom(pack, bind(atom, bindings))
        for row in decision["rows"]:
            if len(row["when"]) != len(decision["inputs"]):
                raise ValueError("decision row arity mismatch")


def observations(snapshot):
    state, conflicts, unavailable = {}, [], []
    now = snapshot["as_of"]
    groups = {}
    for record in snapshot["facts"]:
        groups.setdefault(key(record["atom"]), []).append(record)
    for k, records in sorted(groups.items()):
        valid = [r for r in records if r["observed_at"] <= now
                 and r["valid_from"] <= now < r["valid_to"]]
        values = {canonical(r["atom"][2]) for r in valid}
        if len(values) > 1:
            conflicts.append({"key": k, "facts": [r["id"] for r in valid]})
        elif valid:
            state[k] = {"atom": valid[0]["atom"],
                        "evidence": sorted(r["id"] for r in valid)}
        else:
            unavailable.append({"key": k, "reason": "stale_or_future"})
    return state, conflicts, unavailable


def truth(state, atom):
    entry = state.get(key(atom))
    return None if entry is None else entry["atom"][2] == atom[2]


def closure(pack, base):
    state, derivations = deepcopy(base), []
    changed = True
    while changed:
        changed = False
        for rule in sorted(pack["rules"], key=lambda r: r["id"]):
            for bindings in ground(pack, rule["parameters"]):
                premises = bind(rule["if"], bindings)
                if not all(truth(state, a) is True for a in premises):
                    continue
                atom = bind(rule["then"], bindings)
                k = key(atom)
                if k in state:
                    if truth(state, atom) is False:
                        raise ValueError(f"model contradiction: {rule['id']}")
                    continue
                evidence = sorted({ref for a in premises
                                   for ref in state[key(a)]["evidence"]})
                ref = f"derive:{rule['id']}:{digest('binding', bindings)[:12]}"
                derivations.append({"id": ref, "rule": rule["id"],
                                    "bindings": bindings, "premises": premises,
                                    "supports": evidence, "conclusion": atom})
                state[k] = {"atom": atom, "evidence": [ref]}
                changed = True
    return state, derivations


def decision_result(pack, decision_id, bindings, state):
    table = next(d for d in pack["decisions"] if d["id"] == decision_id)
    inputs = bind(table["inputs"], bindings)
    values = [state.get(key(a), {}).get("atom", [None, [], None])[2] for a in inputs]
    if any(v is None for v in values):
        return {"table": decision_id, "result": None, "rows": [], "inputs": inputs}
    hits = [r for r in table["rows"] if r["when"] == values]
    if len(hits) > 1:
        raise ValueError(f"UNIQUE hit policy violation: {decision_id}")
    return {"table": decision_id, "inputs": inputs, "values": values,
            "rows": [r["id"] for r in hits],
            "result": hits[0]["allow"] if hits else False}


def apply_effects(base, effects, ref, invalidates=()):
    result = deepcopy(base)
    for atom in invalidates:
        result.pop(key(atom), None)
    for atom in effects:
        result[key(atom)] = {"atom": atom, "evidence": [ref]}
    return result


def atom(predicate, *args, value=True):
    return [predicate, list(args), value]
