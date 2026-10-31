"""Construct an executable diagnostic AST from typed hypothesis contracts."""
from .model import bind, digest, ground, validate_atom, validate_inputs


def mechanism_prerequisites(pack, hypothesis, bindings):
    registry = {r["id"]: r for r in pack["rules"]}
    produced, needed, previous = [], [], None
    for rid in hypothesis["mechanisms"]:
        rule = registry[rid]
        premises = bind(rule["if"], bindings)
        if previous is not None and previous not in premises:
            raise ValueError("disconnected diagnostic mechanism chain")
        for atom in premises:
            if atom not in produced and atom not in needed:
                needed.append(atom)
        previous = bind(rule["then"], bindings)
        produced.append(previous)
    if previous[0] != pack["diagnostics"]["explanation_outputs"][hypothesis["kind"]]:
        raise ValueError("diagnostic mechanism does not reach its declared explanation")
    return needed


def diagnostic_instances(pack, intent):
    config = pack["diagnostics"]
    target = intent["diagnostic_target"]
    common = [b for b in ground(pack, config["parameters"])
              if bind(config["symptom"], b) == target]
    if len(common) != 1:
        raise ValueError("diagnostic target must bind exactly one symptom")
    for hypothesis in sorted(config["hypotheses"], key=lambda h: h["id"]):
        for bindings in ground(pack, hypothesis["parameters"]):
            if all(bindings.get(k) == v for k, v in common[0].items()):
                yield hypothesis, bindings


def validate_diagnostics(pack, intent, snapshot):
    validate_inputs(pack, intent, snapshot)
    if "diagnostics" not in pack:
        raise ValueError("missing diagnostic model")
    validate_atom(pack, intent["diagnostic_target"])
    ids = [h["id"] for h in pack["diagnostics"]["hypotheses"]]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("empty or duplicate diagnostic hypotheses")
    rules = {r["id"] for r in pack["rules"]}
    known_evidence_ids = set()
    count = 0
    for hypothesis, bindings in diagnostic_instances(pack, intent):
        count += 1
        if not hypothesis["mechanisms"] or not set(hypothesis["mechanisms"]) <= rules:
            raise ValueError("missing diagnostic mechanism")
        if hypothesis["prior_points"] != 0 or not hypothesis["evidence"]:
            raise ValueError("diagnostic scores require explicit evidence and no hidden prior")
        if hypothesis["score_policy"] not in ("contextual_evidence", "observed_cause_required"):
            raise ValueError("unknown diagnostic score policy")
        if hypothesis["score_policy"] == "observed_cause_required" and any(
                r["role"] != "cause" and (r["support_points"] or r["contradiction_points"])
                for r in hypothesis["evidence"]):
            raise ValueError("residual context cannot supply a numeric cause score")
        evidence_ids = [r["id"] for r in hypothesis["evidence"]]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("duplicate evidence rule within hypothesis")
        if not any(r["role"] == "cause" for r in hypothesis["evidence"]):
            raise ValueError("hypothesis lacks a cause condition")
        leaves = mechanism_prerequisites(pack, hypothesis, bindings)
        evidence_atoms = [bind(r["atom"], bindings) for r in hypothesis["evidence"]]
        if any(a not in evidence_atoms for a in leaves):
            raise ValueError("diagnostic mechanism has an unscored prerequisite")
        for evidence in hypothesis["evidence"]:
            known_evidence_ids.add(evidence["id"])
            validate_atom(pack, bind(evidence["atom"], bindings))
            if evidence["role"] not in ("cause", "context", "scope"):
                raise ValueError("unknown evidence role")
            if type(evidence["hard_refute"]) is not bool:
                raise ValueError("invalid hard-refutation declaration")
            for name in ("support_points", "contradiction_points"):
                if type(evidence[name]) is not int or not 0 <= evidence[name] <= 1000:
                    raise ValueError("invalid evidence point contract")
    if not 1 <= count <= 128:
        raise ValueError("diagnostic candidate bound exceeded")
    multipliers = intent["diagnosis"]["rule_multipliers"]
    if not set(multipliers) <= known_evidence_ids:
        raise ValueError("unknown evidence-weight override")
    if any(type(v) is not int or not 0 <= v <= 1000 for v in multipliers.values()):
        raise ValueError("evidence multipliers must be bounded nonnegative integers")


def build_diagnostic_ast(pack, intent, snapshot):
    validate_diagnostics(pack, intent, snapshot)
    nodes = [{"kind": "read_symptom", "argument": intent["diagnostic_target"],
              "output_type": "EvidenceReceipt"}]
    evidence_index, cause_index = 0, 0
    for hypothesis, bindings in diagnostic_instances(pack, intent):
        indices = []
        for evidence in hypothesis["evidence"]:
            bound = {**evidence, "atom": bind(evidence["atom"], bindings)}
            nodes.append({"kind": "read_evidence", "argument": evidence_index,
                          "contract": bound, "cause_index": cause_index,
                          "multiplier": intent["diagnosis"]["rule_multipliers"].get(evidence["id"], 1),
                          "input_type": pack["predicates"][bound["atom"][0]],
                          "output_type": "EvidenceReceipt"})
            indices.append(evidence_index)
            evidence_index += 1
        nodes.append({"kind": "evaluate_cause", "argument": cause_index,
                      "hypothesis": {k: v for k, v in hypothesis.items() if k not in ("evidence", "parameters")},
                      "bindings": bindings, "evidence_indices": indices, "output_type": "CauseAssessment"})
        nodes[-1]["mechanism_prerequisites"] = mechanism_prerequisites(pack, hypothesis, bindings)
        cause_index += 1
    nodes.extend([{"kind": "rank_causes", "argument": 0, "output_type": "RankedCauses"},
                  {"kind": "complete_diagnosis", "argument": "DIAGNOSIS_EVALUATED",
                   "output_type": "DiagnosticEnvelope"}])
    return {"schema_version": "abem-diagnostic-ast-1", "kind": "DiagnosticSequence",
            "body": nodes, "return_type": "DiagnosticEnvelope", "effects": [],
            "model_hash": digest("diagnostic-model", pack["diagnostics"]),
            "weight_kind": pack["diagnostics"]["weight_kind"],
            "failure_control": "explicit_unknown_stale_conflict_and_assumption_results"}
