"""Lower the checked diagnostic sequence into pure single-purpose blocks."""
from copy import deepcopy

from .diagnostic_checker import verify_diagnostic_ast
from .model import digest, key


def seal_block(nodes, purpose, output_type):
    atoms = [n["argument"] if n["kind"] == "read_symptom" else n["contract"]["atom"]
             for n in nodes if n["kind"] in ("read_symptom", "read_evidence")]
    contract = {"purpose": purpose, "body": deepcopy(nodes), "effects": [],
                "read_set": sorted({key(a) for a in atoms}), "write_set": [],
                "input_type": "PinnedSnapshotRecords", "output_type": output_type,
                "depends_on": ["pinned_snapshot"], "semantic_version": "atomic-diagnostic-1"}
    return {"id": digest("atomic-block", contract), **contract}


def build_graph(tree):
    blocks = [seal_block(tree["body"][:1], "read_target_symptom", "SymptomReceipt")]
    pending = []
    for node in tree["body"][1:-2]:
        pending.append(node)
        if node["kind"] == "evaluate_cause":
            blocks.append(seal_block(pending, "assess:" + node["hypothesis"]["id"], "CauseAssessment"))
            pending = []
    if pending:
        raise ValueError("unconsumed diagnostic nodes")
    return {"schema_version": "abem-atomic-graph-1", "semantic_ast_hash": digest("ast", tree),
            "blocks": blocks, "join": {"kind": "canonical_join", "depends_on": [b["id"] for b in blocks],
            "body": deepcopy(tree["body"][-2:]), "output_type": "DiagnosticEnvelope"},
            "effects": [], "failure": "cancel_dependents_and_return_no_ranked_result"}


def check_graph(pack, intent, snapshot, tree, graph):
    verify_diagnostic_ast(pack, intent, snapshot, tree)
    if graph["schema_version"] != "abem-atomic-graph-1" or graph["effects"] != []:
        raise ValueError("atomic graph has undeclared effects/version")
    if graph["semantic_ast_hash"] != digest("ast", tree):
        raise ValueError("atomic graph semantic AST mismatch")
    flattened, seen, read_sets = [], set(), set()
    for index, block in enumerate(graph["blocks"]):
        contract = {k: v for k, v in block.items() if k != "id"}
        if block["id"] != digest("atomic-block", contract) or block["id"] in seen:
            raise ValueError("atomic block identity mismatch")
        seen.add(block["id"])
        if (block["effects"] or block["write_set"] or block["depends_on"] != ["pinned_snapshot"]
                or block["input_type"] != "PinnedSnapshotRecords" or block["semantic_version"] != "atomic-diagnostic-1"):
            raise ValueError("atomic block dependency/effect contract violation")
        nodes = block["body"]
        if index == 0:
            if len(nodes) != 1 or nodes[0]["kind"] != "read_symptom" or block["output_type"] != "SymptomReceipt":
                raise ValueError("invalid symptom block")
            if block["purpose"] != "read_target_symptom":
                raise ValueError("invalid symptom purpose")
        else:
            if (not nodes or nodes[-1]["kind"] != "evaluate_cause"
                    or any(n["kind"] != "read_evidence" for n in nodes[:-1])
                    or block["output_type"] != "CauseAssessment"):
                raise ValueError("block must assess exactly one cause")
            if nodes[-1]["evidence_indices"] != [n["argument"] for n in nodes[:-1]]:
                raise ValueError("cause block has unbound evidence")
            if block["purpose"] != "assess:" + nodes[-1]["hypothesis"]["id"]:
                raise ValueError("invalid cause purpose")
        atoms = [n["argument"] if n["kind"] == "read_symptom" else n["contract"]["atom"]
                 for n in nodes if n["kind"] != "evaluate_cause"]
        reads = sorted({key(a) for a in atoms})
        if reads != block["read_set"]:
            raise ValueError("atomic block has undeclared reads")
        read_sets.update(reads)
        flattened.extend(nodes)
    join = graph["join"]
    if (join["kind"] != "canonical_join" or join["depends_on"] != [b["id"] for b in graph["blocks"]]
            or join["body"] != tree["body"][-2:] or flattened != tree["body"][:-2]
            or join["output_type"] != "DiagnosticEnvelope"
            or graph["failure"] != "cancel_dependents_and_return_no_ranked_result"):
        raise ValueError("atomic graph coverage/join mismatch")
    return {"accepted": True, "blocks": len(seen), "independent_cause_blocks": len(seen) - 1,
            "unique_snapshot_queries": len(read_sets), "effects": [],
            "assurance": "CONTRACT_CHECKED", "graph_hash": digest("atomic-graph", graph)}
