"""Host ABI for the actual compiled diagnostic program; read-only evidence."""
import argparse
import json
from pathlib import Path

from .codegen import python_source
from .compiler import toolchain
from .diagnostic_checker import check_diagnostic_result, verify_diagnostic_ast
from .diagnostic_runtime import DiagnosticSession
from .model import digest


def main(generated_program):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--mode", choices=["replay", "simulate"], default="replay")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    def read(name):
        return json.loads((args.bundle / f"{name}.json").read_text())

    pack, intent, snapshot = read("domain-pack"), read("intent"), read("input-snapshot")
    tree, manifest, target = read("program.ast"), read("manifest"), read("language-manifest")
    expected = {"domain_hash": digest("domain", pack), "intent_hash": digest("intent", intent),
                "snapshot_hash": digest("snapshot", snapshot), "ast_hash": digest("ast", tree),
                "toolchain": toolchain()}
    if any(manifest.get(k) != v for k, v in expected.items()):
        raise ValueError("diagnostic dependency/snapshot substitution")
    if (target["ast_hash"] != expected["ast_hash"]
            or digest("python-source", python_source(tree).encode()) != target["source_hash"]
            or digest("python-bytecode", (args.bundle / "program.pyc").read_bytes()) != target["compiled_hash"]):
        raise ValueError("diagnostic language artifact mismatch")
    verify_diagnostic_ast(pack, intent, snapshot, tree)
    result = generated_program(DiagnosticSession(tree, pack, intent, snapshot))
    check_diagnostic_result(pack, intent, snapshot, tree, result)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit("Run the generated diagnostic program.pyc with --bundle.")
