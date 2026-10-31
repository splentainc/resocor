"""Transparent preference weights for checked candidates; no probability claims."""
from fractions import Fraction

from .model import canonical, digest


def rank_programs(pack, intent, plans):
    operators = {o["id"]: o for o in pack["operators"]}
    weights = intent["ranking"]["weights"]
    unique = {}
    for plan in plans:
        signature = canonical([[n["op"], n["bindings"]] for n in plan["nodes"]])
        if signature not in unique:
            unique[signature] = plan
    results = []
    for plan in unique.values():
        features = {"risk": sum(operators[n["op"]]["risk"] for n in plan["nodes"]),
                    "cost": plan["model_cost"], "length": len(plan["nodes"]),
                    "observations": sum(n["kind"] == "observation" for n in plan["nodes"])}
        penalty = sum(weights[name] * value for name, value in features.items())
        required = sorted({n["capability"] for n in plan["nodes"]})
        results.append({"id": digest("plan", plan), "plan": plan, "features": features,
                        "weighted_penalty": penalty, "required_permissions": required,
                        "missing_permissions": sorted(set(required) - set(intent["permissions"])),
                        "weight_kind": "relative_preference_not_probability"})
    results.sort(key=lambda r: (r["weighted_penalty"], canonical(r["plan"])))
    results = results[:intent["ranking"]["max_results"]]
    # Exact rational inverse-penalty weights with deterministic largest remainders.
    raw = [Fraction(1, 1 + r["weighted_penalty"]) for r in results]
    total = sum(raw)
    normalized = [r / total * 10000 for r in raw]
    floors = [n.numerator // n.denominator for n in normalized]
    remainder_order = sorted(range(len(results)), key=lambda i: (-(normalized[i] - floors[i]), i))
    for i in remainder_order[:10000 - sum(floors)]:
        floors[i] += 1
    for i, (result, weight) in enumerate(zip(results, floors)):
        result.update(rank=i, weight_basis_points=weight, weights=weights,
                      assurance="MODEL_CHECKED", execution="proposal_requires_current_authority")
    return results
