"""Evaluate cause evidence through generated AST calls; no remedy selection."""
from copy import deepcopy
from fractions import Fraction

from .model import canonical, digest, key


def read_receipt(snapshot, atom):
    records = [r for r in snapshot["facts"] if key(r["atom"]) == key(atom)]
    now = snapshot["as_of"]
    live = [r for r in records if r["observed_at"] <= now and r["valid_from"] <= now < r["valid_to"]]
    observed = [r for r in live if r.get("epistemic_kind", "observation") != "assumption"]
    chosen = observed or live
    assumed = bool(chosen) and not observed
    values = {canonical(r["atom"][2]) for r in chosen}
    if len(values) > 1:
        outcome = "CONFLICT"
    elif not chosen:
        outcome = "STALE" if records else "UNKNOWN"
    else:
        outcome = "MATCH" if chosen[0]["atom"][2] == atom[2] else "MISMATCH"
        if assumed:
            outcome = "ASSUMED_" + outcome
    return {"atom": atom, "outcome": outcome, "fact_ids": sorted(r["id"] for r in chosen),
            "unavailable_ids": sorted(r["id"] for r in records if r not in live),
            "records": deepcopy(sorted(chosen, key=lambda r: r["id"])),
            "overridden_assumption_ids": sorted(r["id"] for r in live if observed
                                               and r.get("epistemic_kind") == "assumption")}


class DiagnosticSession:
    def __init__(self, tree, pack, intent, snapshot, receipt_reader=read_receipt):
        self.tree, self.pack, self.intent, self.snapshot = tree, pack, intent, snapshot
        self.pc, self.evidence, self.causes, self.trace = 0, {}, [], []
        self.symptom, self.result = None, None
        self.receipt_reader = receipt_reader

    def node(self, method, argument):
        if self.pc >= len(self.tree["body"]):
            raise ValueError("diagnostic instruction budget exceeded")
        node = self.tree["body"][self.pc]
        if (node["kind"], node["argument"]) != (method, argument):
            raise ValueError("diagnostic program deviates from checked AST")
        self.pc += 1
        return node

    def read_symptom(self, atom):
        self.node("read_symptom", atom)
        self.symptom = self.receipt_reader(self.snapshot, atom)
        self.trace.append({"event": "symptom", **self.symptom})

    def read_evidence(self, index):
        node = self.node("read_evidence", index)
        rule = node["contract"]
        receipt = self.receipt_reader(self.snapshot, rule["atom"])
        outcome = receipt["outcome"]
        points = (rule["support_points"] if outcome == "MATCH" else
                  -rule["contradiction_points"] if outcome == "MISMATCH" else 0)
        evaluation = {"index": index, "rule": rule["id"], "role": rule["role"],
                      "hard_refute": rule["hard_refute"], "receipt": receipt,
                      "points": points * node["multiplier"], "multiplier": node["multiplier"]}
        self.evidence[index] = evaluation
        self.trace.append({"event": "evidence", **evaluation})

    def evaluate_cause(self, index):
        node = self.node("evaluate_cause", index)
        h = node["hypothesis"]
        evaluations = [self.evidence[i] for i in node["evidence_indices"]]
        conflicts = [e for e in evaluations if e["receipt"]["outcome"] == "CONFLICT"]
        refutations = [e for e in evaluations if (e["hard_refute"] or
                       e["receipt"]["atom"] in node["mechanism_prerequisites"])
                       and e["receipt"]["outcome"] == "MISMATCH"]
        challenges = [e for e in evaluations if e["receipt"]["outcome"] == "ASSUMED_MISMATCH"]
        missing = [e for e in evaluations if e["receipt"]["outcome"] in ("UNKNOWN", "STALE")]
        root = [e for e in evaluations if e["receipt"]["atom"] in node["mechanism_prerequisites"]]
        status = ("EVIDENCE_CONFLICT" if conflicts else "REFUTED_FOR_CURRENT_SNAPSHOT" if refutations
                  else "CHALLENGES_ASSUMPTION" if challenges else "SUPPORTED_UNDER_MODEL"
                  if all(e["receipt"]["outcome"] == "MATCH" for e in root) else "PARTIAL_SUPPORT")
        raw = sum(e["points"] for e in evaluations)
        score = 0 if refutations else max(0, raw)
        direct_observed = any(e["role"] == "cause" and e["receipt"]["outcome"] == "MATCH"
                              for e in evaluations)
        if h["score_policy"] == "observed_cause_required" and not refutations and (
                not direct_observed or conflicts or challenges):
            score, raw = None, None
            if not conflicts and not challenges:
                status = "UNRESOLVED_COVERAGE_GAP"
        if status == "PARTIAL_SUPPORT" and score == 0:
            status = "NO_SUPPORT_YET"
        result = {"id": h["id"] + ":" + digest("binding", node["bindings"])[:12],
                  "hypothesis_id": h["id"], "label": h["label"], "kind": h["kind"],
                  "bindings": node["bindings"], "mechanisms": h["mechanisms"], "status": status,
                  "score_policy": h["score_policy"],
                  "signed_evidence_points": raw, "support_score": score,
                  "direct_cause_support_points": sum(max(0, e["points"]) for e in evaluations if e["role"] == "cause"),
                  "mechanism_premise_coverage": {"required": len(root),
                      "observed_matches": sum(e["receipt"]["outcome"] == "MATCH" for e in root),
                      "unknown_or_stale": sum(e["receipt"]["outcome"] in ("UNKNOWN", "STALE") for e in root)},
                  "evaluations": evaluations, "missing_observations": [e["receipt"]["atom"] for e in missing],
                  "assumptions_used": [e["receipt"] for e in evaluations
                                       if e["receipt"]["outcome"] == "ASSUMED_MATCH"],
                  "assumptions_challenged": [e["receipt"] for e in challenges],
                  "refuting_rule_ids": [e["rule"] for e in refutations],
                  "weight_basis_points": None, "rank": None}
        self.causes.append(result)
        self.trace.append({"event": "cause", "id": result["id"], "status": status, "support_score": score})

    def rank_causes(self, argument):
        self.node("rank_causes", argument)
        conflict = self.symptom["outcome"] == "CONFLICT" or any(
            c["status"] == "EVIDENCE_CONFLICT" for c in self.causes)
        symptom_observed = self.symptom["outcome"] == "MATCH"
        eligible = [c for c in self.causes if c["support_score"] is not None and
                    c["status"] not in ("CHALLENGES_ASSUMPTION", "EVIDENCE_CONFLICT")]
        eligible.sort(key=lambda c: (-c["support_score"], c["id"]))
        total = sum(c["support_score"] for c in eligible)
        if symptom_observed and not conflict and total:
            exact = [Fraction(c["support_score"] * 10000, total) for c in eligible]
            weights = [n.numerator // n.denominator for n in exact]
            order = sorted(range(len(eligible)), key=lambda i: (-(exact[i] - weights[i]), eligible[i]["id"]))
            for i in order[:10000 - sum(weights)]:
                weights[i] += 1
            for rank, (cause, weight) in enumerate(zip(eligible, weights)):
                cause.update(rank=rank, weight_basis_points=weight)
        unscored = [c for c in self.causes if c["status"] == "UNRESOLVED_COVERAGE_GAP"]
        coverage = {"status": "INCOMPLETE", "catalog_complete": False,
                    "unscored_candidate_ids": [c["id"] for c in unscored],
                    "assumption_excluded_candidate_ids": [c["id"] for c in self.causes
                                                          if c["status"] == "CHALLENGES_ASSUMPTION"],
                    "normalization_scope": "scored_candidates_only", "unmodeled_cause_weight": None,
                    "reason_codes": ["NONEXHAUSTIVE_CAUSE_CATALOG"] +
                                    (["UNSCORED_RESIDUAL_ALTERNATIVES"] if unscored else [])}
        status = ("MODEL_CONFLICT" if conflict else "SYMPTOM_NOT_OBSERVED"
                  if self.symptom["outcome"] == "MISMATCH" else "NEEDS_OBSERVATION"
                  if not symptom_observed else "NO_WEIGHTED_SUPPORT" if not total else
                  "CAUSES_RANKED_WITH_COVERAGE_GAPS" if unscored else "CAUSES_RANKED")
        self.result = {"status": status, "target": self.intent["diagnostic_target"], "symptom": self.symptom,
                       "ranked_causes": eligible, "assumption_challenges": [c for c in self.causes
                           if c["status"] == "CHALLENGES_ASSUMPTION"],
                       "conflicted_causes": [c for c in self.causes if c["status"] == "EVIDENCE_CONFLICT"],
                       "unscored_causes": unscored, "candidate_coverage": coverage,
                       "normalization_score_total": total, "weight_kind": self.tree["weight_kind"],
                       "weight_formula": "max(0, signed evidence points) / eligible support total",
                       "unknown_is_false": False, "causes_may_cooccur": True, "complete_cause_catalog": False,
                       "time_scope": "current pinned snapshot; no retroactive attribution",
                       "snapshot_hash": digest("snapshot", self.snapshot), "ast_hash": digest("ast", self.tree),
                       "independently_validated_physics": False}

    def complete_diagnosis(self, argument):
        self.node("complete_diagnosis", argument)
        if self.result is None or self.pc != len(self.tree["body"]):
            raise ValueError("incomplete diagnostic execution")
        return {**self.result, "trace": self.trace}


def evaluate_ast(tree, pack, intent, snapshot):
    session = DiagnosticSession(tree, pack, intent, snapshot)
    for node in tree["body"]:
        if node["kind"] not in {"read_symptom", "read_evidence", "evaluate_cause", "rank_causes", "complete_diagnosis"}:
            raise ValueError("unregistered diagnostic instruction")
        result = getattr(session, node["kind"])(node["argument"])
        if result is not None:
            return result
    raise ValueError("diagnostic program lacks a terminal instruction")
