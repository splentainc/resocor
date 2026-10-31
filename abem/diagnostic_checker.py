"""Separate static diagnostic checker and source-record score oracle."""
from .diagnostic_ast import diagnostic_instances, mechanism_prerequisites, validate_diagnostics
from .model import bind, digest, key


def verify_diagnostic_ast(pack, intent, snapshot, tree):
    validate_diagnostics(pack, intent, snapshot)
    if (tree["kind"] != "DiagnosticSequence" or tree["effects"] != []
            or tree["return_type"] != "DiagnosticEnvelope"
            or tree["model_hash"] != digest("diagnostic-model", pack["diagnostics"])
            or tree["weight_kind"] != pack["diagnostics"]["weight_kind"]):
        raise ValueError("invalid diagnostic AST contract")
    nodes = iter(tree["body"])

    def take(kind, argument):
        node = next(nodes, None)
        if node is None or node.get("kind") != kind or node.get("argument") != argument:
            raise ValueError("diagnostic coverage/order mismatch")
        return node

    take("read_symptom", intent["diagnostic_target"])
    evidence_count, cause_count = 0, 0
    for h, bindings in diagnostic_instances(pack, intent):
        indices = []
        for rule in h["evidence"]:
            node = take("read_evidence", evidence_count)
            contract = {**rule, "atom": bind(rule["atom"], bindings)}
            if (node["contract"] != contract or node["cause_index"] != cause_count
                    or node["input_type"] != pack["predicates"][contract["atom"][0]]
                    or node["multiplier"] != intent["diagnosis"]["rule_multipliers"].get(rule["id"], 1)):
                raise ValueError("diagnostic evidence/type/weight substitution")
            indices.append(evidence_count)
            evidence_count += 1
        node = take("evaluate_cause", cause_count)
        if (node["bindings"] != bindings or node["evidence_indices"] != indices
                or node["hypothesis"] != {k: v for k, v in h.items() if k not in ("evidence", "parameters")}):
            raise ValueError("diagnostic cause substitution")
        if node["mechanism_prerequisites"] != mechanism_prerequisites(pack, h, bindings):
            raise ValueError("diagnostic mechanism precondition substitution")
        cause_count += 1
    take("rank_causes", 0)
    take("complete_diagnosis", "DIAGNOSIS_EVALUATED")
    if next(nodes, None) is not None or len(tree["body"]) > 4096:
        raise ValueError("undeclared or unbounded diagnostic instructions")
    return {"accepted": True, "assurance": ["CONTRACT_CHECKED"], "causes": cause_count,
            "evidence_reads": evidence_count, "effects": [], "ast_hash": digest("ast", tree),
            "scope": "typed complete bounded evidence program; no proof of physical causal truth"}


def oracle_receipt(snapshot, atom):
    """Scan original attributed observations independently of runtime receipt code."""
    matching = sorted([r for r in snapshot["facts"] if r["atom"][:2] == atom[:2]], key=lambda r: r["id"])
    live, unavailable = [], []
    for record in matching:
        tick = snapshot["as_of"]
        if record["valid_from"] <= tick < record["valid_to"] and record["observed_at"] <= tick:
            live.append(record)
        else:
            unavailable.append(record["id"])
    measurements = [r for r in live if r.get("epistemic_kind") != "assumption"]
    used = measurements if measurements else live
    if not used:
        outcome = "STALE" if matching else "UNKNOWN"
    elif any(r["atom"][2] != used[0]["atom"][2] for r in used):
        outcome = "CONFLICT"
    else:
        outcome = "MATCH" if used[0]["atom"][2] == atom[2] else "MISMATCH"
        if not measurements:
            outcome = "ASSUMED_" + outcome
    return {"atom": atom, "outcome": outcome, "fact_ids": [r["id"] for r in used],
            "unavailable_ids": unavailable, "records": used,
            "overridden_assumption_ids": [r["id"] for r in live if measurements
                                          and r.get("epistemic_kind") == "assumption"]}


def check_diagnostic_result(pack, intent, snapshot, tree, result):
    report = verify_diagnostic_ast(pack, intent, snapshot, tree)
    found = (result["ranked_causes"] + result["assumption_challenges"]
             + result["conflicted_causes"] + result["unscored_causes"])
    actual = {c["id"]: c for c in found}
    if len(actual) != len(found) or len(actual) != report["causes"]:
        raise ValueError("missing/duplicate diagnostic result")
    expected = []
    for hypothesis, bindings in diagnostic_instances(pack, intent):
        cid = hypothesis["id"] + ":" + digest("binding", bindings)[:12]
        cause = actual.get(cid)
        if cause is None:
            raise ValueError("diagnostic result has substituted hypothesis")
        if (cause["bindings"] != bindings or cause["label"] != hypothesis["label"]
                or cause["kind"] != hypothesis["kind"] or cause["score_policy"] != hypothesis["score_policy"]):
            raise ValueError("diagnostic result identity mismatch")
        statuses, points, refutations, challenges, roots = [], 0, [], [], []
        leaves = mechanism_prerequisites(pack, hypothesis, bindings)
        if len(cause["evaluations"]) != len(hypothesis["evidence"]):
            raise ValueError("missing diagnostic evaluation")
        for rule, evaluation in zip(hypothesis["evidence"], cause["evaluations"]):
            atom = bind(rule["atom"], bindings)
            receipt = oracle_receipt(snapshot, atom)
            outcome = receipt["outcome"]
            multiplier = intent["diagnosis"]["rule_multipliers"].get(rule["id"], 1)
            contribution = multiplier * (rule["support_points"] if outcome == "MATCH"
                            else -rule["contradiction_points"] if outcome == "MISMATCH" else 0)
            if evaluation["receipt"] != receipt or evaluation["points"] != contribution:
                raise ValueError("diagnostic evidence receipt/contribution mismatch")
            if (evaluation["rule"] != rule["id"] or evaluation["multiplier"] != multiplier
                    or evaluation["role"] != rule["role"] or evaluation["hard_refute"] != rule["hard_refute"]):
                raise ValueError("diagnostic rule/multiplier mismatch")
            points += contribution
            statuses.append(outcome)
            if atom in leaves:
                roots.append(outcome)
            if outcome == "MISMATCH" and (rule["hard_refute"] or atom in leaves):
                refutations.append(rule["id"])
            if outcome == "ASSUMED_MISMATCH":
                challenges.append(receipt)
        state = ("EVIDENCE_CONFLICT" if "CONFLICT" in statuses else "REFUTED_FOR_CURRENT_SNAPSHOT"
                 if refutations else "CHALLENGES_ASSUMPTION" if challenges else "SUPPORTED_UNDER_MODEL"
                 if set(roots) == {"MATCH"} else "PARTIAL_SUPPORT")
        score = 0 if refutations else max(points, 0)
        reported_points = points
        direct_observed = any(e["role"] == "cause" and e["receipt"]["outcome"] == "MATCH"
                              for e in cause["evaluations"])
        if hypothesis["score_policy"] == "observed_cause_required" and not refutations and (
                not direct_observed or "CONFLICT" in statuses or challenges):
            score, reported_points = None, None
            if "CONFLICT" not in statuses and not challenges:
                state = "UNRESOLVED_COVERAGE_GAP"
        if state == "PARTIAL_SUPPORT" and score == 0:
            state = "NO_SUPPORT_YET"
        if (cause["status"] != state or cause["signed_evidence_points"] != reported_points
                or cause["support_score"] != score or cause["refuting_rule_ids"] != refutations
                or cause["assumptions_challenged"] != challenges
                or cause["mechanisms"] != hypothesis["mechanisms"]):
            raise ValueError("diagnostic assessment disagrees with independent score oracle")
        coverage = {"required": len(roots), "observed_matches": roots.count("MATCH"),
                    "unknown_or_stale": sum(o in ("UNKNOWN", "STALE") for o in roots)}
        direct = sum(max(0, e["points"]) for e in cause["evaluations"] if e["role"] == "cause")
        if cause["mechanism_premise_coverage"] != coverage or cause["direct_cause_support_points"] != direct:
            raise ValueError("diagnostic mechanism coverage mismatch")
        expected.append((cid, score, state))
    eligible = sorted((r for r in expected if r[1] is not None and
                       r[2] not in ("CHALLENGES_ASSUMPTION", "EVIDENCE_CONFLICT")),
                      key=lambda r: (-r[1], r[0]))
    symptom = oracle_receipt(snapshot, intent["diagnostic_target"])
    conflict = symptom["outcome"] == "CONFLICT" or any(r[2] == "EVIDENCE_CONFLICT" for r in expected)
    total = sum(r[1] for r in eligible)
    unscored = [r[0] for r in expected if r[2] == "UNRESOLVED_COVERAGE_GAP"]
    expected_coverage = {"status": "INCOMPLETE", "catalog_complete": False,
                         "unscored_candidate_ids": unscored,
                         "assumption_excluded_candidate_ids": [r[0] for r in expected if r[2] == "CHALLENGES_ASSUMPTION"],
                         "normalization_scope": "scored_candidates_only", "unmodeled_cause_weight": None,
                         "reason_codes": ["NONEXHAUSTIVE_CAUSE_CATALOG"] +
                                         (["UNSCORED_RESIDUAL_ALTERNATIVES"] if unscored else [])}
    if result["candidate_coverage"] != expected_coverage or [c["id"] for c in result["unscored_causes"]] != unscored:
        raise ValueError("diagnostic coverage gap mismatch")
    if [c["id"] for c in result["ranked_causes"]] != [r[0] for r in eligible]:
        raise ValueError("diagnostic rank order mismatch")
    if result["normalization_score_total"] != total or result["symptom"] != symptom:
        raise ValueError("diagnostic normalization/symptom mismatch")
    weights = {r[0]: None for r in expected}
    if total and not conflict and symptom["outcome"] == "MATCH":
        divisions = {cid: divmod(score * 10000, total) for cid, score, _ in eligible}
        for cid, (quotient, _) in divisions.items():
            weights[cid] = quotient
        leftovers = 10000 - sum(weights[cid] for cid in divisions)
        for cid in sorted(divisions, key=lambda cid: (-divisions[cid][1], cid))[:leftovers]:
            weights[cid] += 1
    for cid, weight in weights.items():
        if actual[cid]["weight_basis_points"] != weight:
            raise ValueError("diagnostic normalized weight mismatch")
        if cid not in {r[0] for r in eligible} and actual[cid]["rank"] is not None:
            raise ValueError("unscored or excluded diagnostic cause has a numeric rank")
    for index, (cid, _, _) in enumerate(eligible):
        if actual[cid]["rank"] != (index if weights[cid] is not None else None):
            raise ValueError("diagnostic rank index mismatch")
    status = ("MODEL_CONFLICT" if conflict else "SYMPTOM_NOT_OBSERVED" if symptom["outcome"] == "MISMATCH"
              else "NEEDS_OBSERVATION" if symptom["outcome"] != "MATCH"
              else "NO_WEIGHTED_SUPPORT" if not total else "CAUSES_RANKED_WITH_COVERAGE_GAPS"
              if unscored else "CAUSES_RANKED")
    if result["status"] != status:
        raise ValueError("diagnostic result status mismatch")
    if (result["snapshot_hash"] != digest("snapshot", snapshot) or result["ast_hash"] != digest("ast", tree)
            or result["target"] != intent["diagnostic_target"] or result["independently_validated_physics"] is not False
            or result["weight_kind"] != pack["diagnostics"]["weight_kind"] or result["unknown_is_false"] is not False
            or result["complete_cause_catalog"] is not False or result["causes_may_cooccur"] is not True):
        raise ValueError("diagnostic result scope/binding mismatch")
    return {**report, "result_recomputed": True, "assurance": ["CONTRACT_CHECKED", "RESULT_RECOMPUTED"],
            "scope": "independent source-record evidence/score recomputation for this snapshot"}
