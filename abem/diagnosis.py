"""Diagnostic construction, compiled execution and evidence export."""
from copy import deepcopy
import json
from pathlib import Path

from .codegen import build_python_ast, run_python
from .compiler import toolchain
from .diagnostic_ast import build_diagnostic_ast
from .diagnostic_checker import check_diagnostic_result, verify_diagnostic_ast
from .diagnostic_runtime import evaluate_ast
from .model import digest


def diagnose(pack, intent, snapshot):
    tree = build_diagnostic_ast(pack, intent, snapshot)
    verify_diagnostic_ast(pack, intent, snapshot, tree)
    result = evaluate_ast(tree, pack, intent, snapshot)
    check_diagnostic_result(pack, intent, snapshot, tree, result)
    return result


def export_diagnosis(destination, pack, intent, snapshot):
    root = Path(destination)
    root.mkdir(parents=True, exist_ok=True)
    tree = build_diagnostic_ast(pack, intent, snapshot)
    verification = verify_diagnostic_ast(pack, intent, snapshot, tree)
    result = evaluate_ast(tree, pack, intent, snapshot)
    result_check = check_diagnostic_result(pack, intent, snapshot, tree, result)
    manifest = {"domain_hash": digest("domain", pack), "intent_hash": digest("intent", intent),
                "snapshot_hash": digest("snapshot", snapshot), "ast_hash": digest("ast", tree),
                "toolchain": toolchain(), "effects": [], "approval": "unsigned_fixture"}
    target = build_python_ast(root, tree, "abem.diagnostic_runtime.DiagnosticSession")
    rebuilt = build_python_ast(root, tree, "abem.diagnostic_runtime.DiagnosticSession")
    if target != rebuilt:
        raise AssertionError("diagnostic compiled rebuild diverged")
    for name, data in (("domain-pack", pack), ("intent", intent), ("input-snapshot", snapshot),
                       ("program.ast", tree), ("manifest", manifest), ("language-manifest", target),
                       ("verification", verification), ("result-check", result_check), ("results", result)):
        (root / f"{name}.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    actual = run_python(root)
    if actual != result:
        raise AssertionError("compiled diagnostic AST differs from reference evaluator")
    replayed = run_python(root)
    if actual != replayed:
        raise AssertionError("diagnostic recorded-snapshot replay diverged")
    summary = {"status": actual["status"], "ast_hash": manifest["ast_hash"],
               "compiled_hash": target["compiled_hash"], "compiled_result_equal": True,
               "rebuild_identical": True, "replay_identical": True,
               "checker": result_check, "result_hash": digest("diagnostic-result", actual)}
    (root / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return actual, summary


def diagnosis_history(pack, intent, initial, trace, initial_result=None):
    history = [{"checkpoint": "initial", "snapshot": deepcopy(initial)}]
    for event in trace:
        if event["event"] == "adapter" and "snapshot_after" in event:
            history.append({"checkpoint": event["id"], "operator": event["operator"],
                            "snapshot": deepcopy(event["snapshot_after"])})
    for index, checkpoint in enumerate(history):
        checkpoint["diagnosis"] = initial_result if index == 0 and initial_result is not None else diagnose(
            pack, intent, checkpoint["snapshot"])
    return history
