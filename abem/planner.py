"""Stable bounded search over grounded primitive contracts, not a repair script."""
from copy import deepcopy
import heapq

from .model import (VERSION, apply_effects, bind, canonical, closure, decision_result,
                    digest, ground, key, observations, truth, validate_inputs)
from .ranking import rank_programs
from .verifier import verify


def signature(operator_id, bindings):
    return canonical([operator_id, bindings]).decode()


def hypotheses(pack, state):
    results = []
    for rule in sorted(pack["rules"], key=lambda r: r["id"]):
        if not rule.get("hypothesis"):
            continue
        for bindings in ground(pack, rule["parameters"]):
            premises = bind(rule["if"], bindings)
            values = [truth(state, a) for a in premises]
            if False not in values and True in values and None in values:
                results.append({"rule": rule["id"], "bindings": bindings,
                                "status": "CONDITIONAL_NOT_OBSERVED",
                                "evidence_score_basis_points": 10000 * values.count(True) // len(values),
                                "score_kind": "known_premise_fraction_not_probability",
                                "assumptions": [a for a, v in zip(premises, values) if v is None],
                                "prediction": bind(rule["then"], bindings)})
    return results


def step_for(pack, op, bindings, base):
    state, _ = closure(pack, base)
    requires = bind(op["requires"], bindings)
    if not all(truth(state, a) is True for a in requires):
        return None
    decision = None
    if op.get("decision"):
        decision = decision_result(pack, op["decision"], bindings, state)
        if decision["result"] is not True:
            return None
    effects = bind(op["effects"], bindings)
    invalidates = bind(op["invalidates"], bindings)
    node = {"op": op["id"], "kind": op["kind"], "bindings": bindings,
            "requires": requires, "expected": effects, "invalidates": invalidates,
            "capability": op["capability"], "capsule_hash": digest("capsule-contract", op),
            "decision": decision, "origin": "resolve_deviation/primitive-search",
            "on_unknown": "NEEDS_OBSERVATION", "on_different": "REPLAN",
            "on_failed": "EXECUTION_FAILED", "on_unauthorized": "REQUIRES_AUTHORIZATION"}
    predicted = apply_effects(base, effects, "HYPOTHETICAL:" + op["id"], invalidates)
    return node, predicted


def construct(pack, intent, snapshot, banned=()):
    validate_inputs(pack, intent, snapshot)
    report = {"planner_version": VERSION, "expansions": 0, "rejected": [],
              "search_config": intent["search"], "assurance": [], "plan": None}

    def stop(status, reason, **extra):
        return {**report, "status": status, "reason": reason, **extra}

    base, conflicts, unavailable = observations(snapshot)
    if conflicts:
        return stop("MODEL_CONFLICT", "contradictory valid observations", conflicts=conflicts)
    missing = sorted(set(pack["required_mechanisms"]) - {r["id"] for r in pack["rules"]})
    if missing:
        return stop("KNOWLEDGE_GAP", "required mechanism unavailable", missing=missing)
    try:
        state, derivations = closure(pack, base)
    except ValueError as error:
        return stop("MODEL_CONFLICT", str(error))
    report.update(derivations=derivations, hypotheses=hypotheses(pack, state),
                  alternatives=pack["alternatives"], unavailable=unavailable)
    needed = [a for a in intent["required_observations"] + intent["invariants"]
              if truth(state, a) is None]
    if needed:
        return stop("NEEDS_OBSERVATION", "required evidence unknown", missing=needed)
    if not all(truth(state, a) is True for a in intent["invariants"]):
        return stop("MODEL_CONFLICT", "safety invariant not established")
    budget = intent["search"]
    accepted_plans = []

    def accept(plan):
        try:
            verify(pack, intent, snapshot, plan)
        except ValueError as error:
            report["rejected"].append({"plan": digest("plan", plan), "counterexample": str(error)})
            return
        accepted_plans.append(plan)

    def found(exhausted):
        ranked = rank_programs(pack, intent, accepted_plans)
        selected = intent["ranking"]["selected_rank"]
        if selected >= len(ranked):
            raise ValueError("selected candidate rank is unavailable")
        return stop("PLAN_FOUND", "ranked checked conditional programs", plan=ranked[selected]["plan"],
                    candidate_programs=ranked, selected_rank=selected,
                    search_space_exhausted=exhausted,
                    search_stop_reason="FINITE_SPACE_EXHAUSTED" if exhausted else "SEARCH_BUDGET_EXHAUSTED",
                    weight_scope="relative preferences among retained candidates; no likelihood or global optimum claim",
                    assurance=["MODEL_CHECKED"])
    operations = [(op, bindings) for op in sorted(pack["operators"], key=lambda o: o["id"])
                  for bindings in ground(pack, op["parameters"])]
    # Reuse a catalog remedy only when its full guarded contract applies.
    # Otherwise primitive search constructs a previously unstored composition.
    for remedy in sorted(pack["known_remedies"], key=lambda r: r["id"]):
        candidate, nodes, cost = deepcopy(base), [], 0
        for operator_id in remedy["steps"]:
            options = [(op, b) for op, b in operations if op["id"] == operator_id
                       and signature(op["id"], b) not in banned
                       and not any(a in intent["forbidden_effects"] for a in bind(op["effects"], b))]
            built = next(((op, step_for(pack, op, b, candidate)) for op, b in options
                          if step_for(pack, op, b, candidate) is not None), None)
            if built is None:
                break
            op, (node, candidate) = built
            node["origin"] = "known_remedy:" + remedy["id"]
            nodes.append(node)
            cost += op["cost"]
        if (len(nodes) == len(remedy["steps"]) and len(nodes) <= budget["max_depth"]
                and len(nodes) + 1 <= budget["max_expansions"]
                and sum(n["kind"] == "observation" for n in nodes) <= budget["max_observation_rounds"]
                and truth(closure(pack, candidate)[0], intent["goal"]) is True):
            plan = {"schema_version": VERSION, "goal": intent["goal"],
                    "method": "resolve_deviation", "nodes": nodes,
                    "success": "GOAL_CONFIRMED", "model_cost": cost,
                    "observations_are_conditional": True}
            report["expansions"] += len(nodes) + 1
            accept(plan)
    frontier = [(0, 0, "[]", 0, base, [])]
    visited, counter = {}, 0
    while frontier:
        if report["expansions"] >= budget["max_expansions"]:
            if accepted_plans:
                return found(False)
            return stop("SEARCH_BUDGET_EXHAUSTED", "frontier remains at expansion limit")
        cost, depth, _, _, candidate, nodes = heapq.heappop(frontier)
        report["expansions"] += 1
        try:
            state, _ = closure(pack, candidate)
        except ValueError:
            continue
        if truth(state, intent["goal"]) is True:
            plan = {"schema_version": VERSION, "goal": intent["goal"],
                    "method": "resolve_deviation", "nodes": nodes,
                    "success": "GOAL_CONFIRMED", "model_cost": cost,
                    "observations_are_conditional": True}
            accept(plan)
            continue
        if depth >= budget["max_depth"]:
            continue
        used = {signature(n["op"], n["bindings"]) for n in nodes} | set(banned)
        for op, bindings in operations:
            sig = signature(op["id"], bindings)
            if sig in used:
                continue
            if any(a in intent["forbidden_effects"] for a in bind(op["effects"], bindings)):
                continue
            built = step_for(pack, op, bindings, candidate)
            if built is None:
                reason = "preconditions_or_policy_not_true"
                record = {"operator": op["id"], "bindings": bindings, "reason": reason}
                if record not in report["rejected"]:
                    report["rejected"].append(record)
                continue
            node, child = built
            if not all(truth(closure(pack, child)[0], a) is True for a in intent["invariants"]):
                continue
            child_nodes = nodes + [node]
            observations_count = sum(n["kind"] == "observation" for n in child_nodes)
            if observations_count > budget["max_observation_rounds"]:
                continue
            # Remaining operators are part of belief state: consumed steps affect reachability.
            stamp = canonical([sorted((k, v["atom"][2]) for k, v in child.items()),
                               sorted(used | {sig})])
            child_cost = cost + op["cost"]
            if stamp in visited and visited[stamp] <= child_cost:
                continue
            visited[stamp] = child_cost
            counter += 1
            heapq.heappush(frontier, (child_cost, depth + 1, canonical(child_nodes).decode(),
                                     counter, child, child_nodes))
    if accepted_plans:
        return found(True)
    # Missing leaf evidence cannot be manufactured by operator composition.
    produced = {a[0] for op in pack["operators"] for a in op["effects"]}
    produced |= {r["then"][0] for r in pack["rules"]}
    leaf_unknown = []
    for op, bindings in operations:
        requirements = bind(op["requires"], bindings)
        if any(truth(state, a) is False for a in requirements):
            continue
        for a in requirements:
            if a[0] not in produced and truth(state, a) is None and a not in leaf_unknown:
                leaf_unknown.append(a)
    if leaf_unknown:
        return stop("NEEDS_OBSERVATION", "unacquirable prerequisite evidence", missing=leaf_unknown)
    return stop("NO_PLAN_WITHIN_BOUNDS", "finite grammar exhausted at declared bounds")


def explanation(report, trace):
    lines = []
    for h in report.get("hypotheses", []):
        lines.append({"text": f"{h['rule']} proposes a conditional explanation; its assumptions require observations.",
                      "refs": [h["rule"]], "status": h["status"]})
    derivations = report.get("derivations", []) + [d for event in trace
                                                 for d in event.get("derivations", [])]
    seen = set()
    for d in derivations:
        if d["id"] in seen:
            continue
        seen.add(d["id"])
        lines.append({"text": f"{d['rule']} derives {d['conclusion'][0]} from explicit premises.",
                      "refs": [d["id"], *d["supports"]], "rule": d["rule"],
                      "premises": d["premises"], "conclusion": d["conclusion"]})
    for event in trace:
        if event["event"] == "adapter":
            lines.append({"text": f"Simulated {event['operator']} recorded {event['outcome']}.",
                          "refs": [event["id"], event["capsule_hash"]]})
    return lines
