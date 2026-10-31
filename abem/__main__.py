"""Run ABEM against caller-supplied domain/intent/snapshot JSON, without fixtures."""
import argparse
import asyncio
import json
from pathlib import Path

from . import AtomicRunner
from .atomic_runner import write


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True,
                        help="directory containing domain-pack.json, intent.json and snapshot.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--mode", choices=["sync", "async"], default="sync")
    parser.add_argument("--parallel-reads", type=int, default=4)
    args = parser.parse_args()
    data = tuple(json.loads((args.inputs / f"{name}.json").read_text())
                 for name in ("domain-pack", "intent", "snapshot"))
    args.output.mkdir(parents=True, exist_ok=True)
    runner = AtomicRunner(args.cache_dir or args.output / "programs")
    output = (runner.run_sync(*data) if args.mode == "sync" else
              asyncio.run(runner.run_async(*data, parallel_reads=args.parallel_reads)))
    write(args.output / "result.json", output["result"])
    write(args.output / "execution.json", output["execution"])
    print(f"{output['result']['status']}: {args.output.resolve() / 'result.json'}")


if __name__ == "__main__":
    main()
