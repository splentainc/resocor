"""Checker independent of search: contract replay and finite guarded outcomes."""
from copy import deepcopy
from itertools import product

from .model import (bind, closure, decision_result, digest, key, observations,
                    truth, validate_atom, validate_inputs)


def verify(pack, intent, snapshot, plan):
    validate_inputs(pack, intent, snapshot)
    if plan["goal"] != intent["goal"] or plan["success"] != "GOAL_CONFIRMED":
        raise ValueError("plan goal/result substitution")
    nodes = plan["nodes"]
    if len(nodes) > intent["search"]["max_depth"]:
        raise ValueError("plan exceeds depth bound")
    if sum(n["kind"] == "observation" for n in nodes) > intent["search"]["max_observation_rounds"]:
        raise ValueError("plan exceeds observation bound")
    base, conflicts, _ = observations(snapshot)
    if conflicts:
        raise ValueError("conflicting starting observations")
    state, _ = closure(pack, base)
    if any(truth(state, a) is None for a in intent["required_observations"]):
        raise ValueError("required starting observation unknown")
    checked, count = [], 0
    seen = set()
    for i, node in enumerate(nodes):
        op = next((o for o in pack["operators"] if o["id"] == node["op"]), None)
        if op is None:
            raise ValueError("unregistered operator")
        bindings = node["bindings"]
        if set(bindings) != set(op["parameters"]):
            raise ValueError("unbound operator input")
        for var, kind in op["parameters"].items():
            if pack["entities"].get(bindings[var]) != kind:
                raise ValueError("operator input type mismatch")
        identity = (op["id"], tuple(sorted(bindings.items())))
        if identity in seen:
            raise ValueError("repeated operator instance outside finite grammar")
        seen.add(identity)
        for field, expected in (("requires", bind(op["requires"], bindings)),
                                ("expected", bind(op["effects"], bindings)),
                                ("invalidates", bind(op["invalidates"], bindings)),
                                ("capability", op["capability"]), ("kind", op["kind"]),
                                ("capsule_hash", digest("capsule-contract", op))):
            if node[field] != expected:
                raise ValueError(f"contract mismatch: {node['op']}/{field}")
        for field, status in (("on_unknown", "NEEDS_OBSERVATION"),
                              ("on_different", "REPLAN"), ("on_failed", "EXECUTION_FAILED"),
                              ("on_unauthorized", "REQUIRES_AUTHORIZATION")):
            if node.get(field) != status:
                raise ValueError("missing safe branch")
        if any(a in intent["forbidden_effects"] for a in node["expected"]):
            raise ValueError("operator violates intent effect constraint")
        state, _ = closure(pack, base)
        for a in intent["invariants"] + node["requires"]:
            if truth(state, a) is not True:
                raise ValueError(f"unproved precondition at node {i}: {a}")
        if op.get("decision"):
            decision = decision_result(pack, op["decision"], bindings, state)
            if decision["result"] is not True or node["decision"] != decision:
                raise ValueError("policy gate not established")
        elif node["decision"] is not None:
            raise ValueError("unexpected policy decision")
        # Exhaust all finite combinations of outputs. Only the expected tuple
        # continues; every other tuple must terminate the current program.
        domains = [pack["predicates"][a[0]]["values"] for a in node["expected"]]
        alternatives = 0
        for values in product(*domains):
            different = any(v != a[2] for v, a in zip(values, node["expected"]))
            if different:
                assert node["on_different"] == "REPLAN"
                alternatives += 1
        count += alternatives + 4  # expected, unknown, failed, unauthorized
        # Deliberately separate transition implementation from planner.
        for a in node["invalidates"]:
            base.pop(key(a), None)
        for a in node["expected"]:
            validate_atom(pack, a)
            base[key(a)] = {"atom": deepcopy(a), "evidence": [f"model-check:{i}"]}
        state, _ = closure(pack, base)
        if not all(truth(state, a) is True for a in intent["invariants"]):
            raise ValueError("modeled invariant violation")
        checked.append({"node": i, "operator": op["id"], "alternate_output_stops": alternatives,
                        "conditional": True})
    state, derivations = closure(pack, base)
    if truth(state, intent["goal"]) is not True:
        raise ValueError("modeled goal is not established")
    return {"accepted": True, "assurance": ["MODEL_CHECKED"],
            "scope": "finite contract outputs and guarded stops; fictional transition model only",
            "real_world_success": False, "plan_hash": digest("plan", plan),
            "explicit_assumptions": [f for f in snapshot["facts"] if f.get("epistemic_kind") == "assumption"],
            "checked_branches": count, "nodes": checked, "goal_derivations": derivations}
