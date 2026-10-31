"""Small host ABI used by generated Python programs; fixture/replay modes only."""
import argparse
import json
from pathlib import Path

from .codegen import program_ast, python_source
from .model import digest
from .runtime import Session


def main(generated_program, adapters=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--mode", choices=["replay", "simulate"], default="replay")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    def read(name):
        return json.loads((args.bundle / f"{name}.json").read_text())

    pack, intent, snapshot = read("domain-pack"), read("intent"), read("input-snapshot")
    manifest, target = read("manifest"), read("language-manifest")
    binary = (args.bundle / "program.abp").read_bytes()
    plan = read("plan.ir")
    tree = program_ast(pack, plan)
    if (digest("ast", tree) != target["ast_hash"]
            or digest("python-source", python_source(tree).encode()) != target["source_hash"]
            or digest("python-bytecode", (args.bundle / "program.pyc").read_bytes()) != target["compiled_hash"]):
        raise ValueError("generated-language artifact mismatch")
    replay = read("replay-inputs") if args.mode == "replay" else None
    world = {} if replay is not None else read("input-world")
    session = Session(binary, manifest, pack, intent, snapshot, world, replay=replay, adapters=adapters)
    result = generated_program(session)
    if result is None or replay:
        raise ValueError("incomplete generated program or unconsumed replay")
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit("Run the generated program.py or program.pyc with --bundle.")
