"""Mechanical lowering to the checked ABEM bytecode format."""
from pathlib import Path
import json

from .model import VERSION, canonical, digest
from .verifier import verify
from .adapters import validate_bindings

MAGIC = b"ABEMPOC1\n"
OPCODES = {1: "CHECK", 2: "AUTHORIZE", 3: "INVOKE", 4: "EXPECT", 5: "ASSERT", 6: "RETURN"}


def toolchain():
    root = Path(__file__).parent
    return {name: digest("implementation", (root / name).read_bytes()) for name in
            ("__init__.py", "adapters.py", "model.py", "planner.py", "verifier.py", "compiler.py", "runtime.py",
             "codegen.py", "generated_host.py", "ranking.py", "diagnostic_ast.py",
             "diagnostic_runtime.py", "diagnostic_checker.py", "diagnostic_host.py")}


def lower(plan):
    instructions = []
    for i, node in enumerate(plan["nodes"]):
        instructions.extend([[1, i], [2, i], [3, i], [4, i]])
    instructions.extend([[5, plan["goal"]], [6, "GOAL_CONFIRMED"]])
    return instructions


def compile_program(pack, intent, snapshot, plan, adapters=None):
    verification = verify(pack, intent, snapshot, plan)
    adapter_hashes = validate_bindings(pack, adapters)
    capsule_registry = {digest("capsule-contract", op): op for op in pack["operators"]}
    pins = {"schema_version": VERSION, "intent_hash": digest("intent", intent),
            "snapshot_hash": digest("snapshot", snapshot), "domain_hash": digest("domain", pack),
            "plan_hash": digest("plan", plan), "registry_hash": digest("registry", capsule_registry),
            "decision_hashes": [digest("decision", d) for d in pack["decisions"]],
            "toolchain": toolchain(), "adapter_hashes": adapter_hashes,
            "verification_hash": digest("verification", verification)}
    payload = {"pins": pins, "plan": plan, "code": lower(plan)}
    binary = MAGIC + canonical(payload)
    manifest = {**pins, "binary_hash": digest("binary", binary),
                "approval": "unsigned_fixture_policy_only", "target": "ABEMPOC1"}
    return binary, manifest, verification, capsule_registry


def load(binary, manifest, pack, intent, snapshot, adapters=None):
    if len(binary) > 4_000_000 or not binary.startswith(MAGIC):
        raise ValueError("invalid/oversized POC binary")
    if digest("binary", binary) != manifest["binary_hash"]:
        raise ValueError("binary hash mismatch")
    payload = json.loads(binary[len(MAGIC):])
    if set(payload) != {"pins", "plan", "code"}:
        raise ValueError("invalid binary envelope")
    expected, rebuilt_manifest, _, _ = compile_program(pack, intent, snapshot, payload["plan"], adapters)
    if binary != expected or manifest != rebuilt_manifest:
        raise ValueError("artifact, dependency, verification or toolchain mismatch")
    return payload


def disassemble(binary):
    payload = json.loads(binary[len(MAGIC):])
    return "\n".join(f"{i:04} {OPCODES[op]} {json.dumps(arg, sort_keys=True)}"
                     for i, (op, arg) in enumerate(payload["code"])) + "\n"


def project(plan):
    lines = ["# Generated procedural projection of conditional Plan IR", "# Constructed from declared primitive operations"]
    for n in plan["nodes"]:
        args = ", ".join(f"{k}={v}" for k, v in sorted(n["bindings"].items()))
        lines.extend([f"require {n['requires']}", f"authorize {n['capability']} else REQUIRES_AUTHORIZATION",
                      f"{n['kind']} {n['op']}({args})",
                      f"expect {n['expected']} else REPLAN; unknown -> NEEDS_OBSERVATION; failed -> EXECUTION_FAILED"])
    lines.extend([f"assert observed {plan['goal']}", "return GOAL_CONFIRMED (under the declared model)"])
    return "\n".join(lines) + "\n"
