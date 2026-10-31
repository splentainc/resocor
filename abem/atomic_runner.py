"""Resident compiler/cache and invocation API for generated atomic diagnostics."""
import asyncio
from collections import OrderedDict
from copy import deepcopy
import json
from pathlib import Path
import sys
from time import perf_counter_ns

from .atomic_codegen import atomic_toolchain, build_atomic, load_atomic
from .atomic_graph import build_graph, check_graph
from .atomic_runtime import AtomicContext
from .diagnostic_ast import build_diagnostic_ast
from .diagnostic_checker import check_diagnostic_result
from .model import digest, validate_inputs


def write(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


class AtomicRunner:
    def __init__(self, cache_dir, max_entries=32):
        if type(max_entries) is not int or not 1 <= max_entries <= 256:
            raise ValueError("cache entry bound must be 1..256")
        self.root, self.max_entries = Path(cache_dir).resolve(), max_entries
        self.root.mkdir(parents=True, exist_ok=True)
        self.pins = atomic_toolchain()
        self.cache = OrderedDict()
        self.build_count = 0

    def prepare(self, pack, intent, snapshot):
        if atomic_toolchain() != self.pins:
            raise ValueError("atomic implementation changed; restart resident runner")
        cache_key = digest("atomic-cache", {"pack": pack, "intent": intent, "pins": self.pins,
                                            "python": sys.version, "abi": sys.implementation.cache_tag})
        if cache_key in self.cache:
            program = self.cache.pop(cache_key)
            validate_inputs(program["pack"], program["intent"], snapshot)
            self.cache[cache_key] = program
            return program, True
        pack, intent = deepcopy(pack), deepcopy(intent)
        validate_inputs(pack, intent, snapshot)
        tree = build_diagnostic_ast(pack, intent, snapshot)
        graph = build_graph(tree)
        verification = check_graph(pack, intent, snapshot, tree, graph)
        root = self.root / cache_key
        if (root / "manifest.json").exists():
            manifest = json.loads((root / "manifest.json").read_text())
        else:
            manifest = build_atomic(root, tree, graph)
            write(root / "semantic.ast.json", tree)
            write(root / "graph.json", graph)
            write(root / "verification.json", verification)
            write(root / "manifest.json", manifest)
            self.build_count += 1
        functions = load_atomic(root, tree, graph, manifest)
        program = {"tree": tree, "graph": graph, "manifest": manifest, "functions": functions,
                   "pack": pack, "intent": intent, "root": str(root), "cache_key": cache_key}
        self.cache[cache_key] = program
        while len(self.cache) > self.max_entries:
            self.cache.popitem(last=False)
        return program, False

    def complete(self, program, snapshot, result, start, prepared, cache_hit, mode, context):
        body_done = perf_counter_ns()
        check_diagnostic_result(program["pack"], program["intent"], snapshot, program["tree"], result)
        end = perf_counter_ns()
        return {"result": result, "execution": {"mode": mode, "cache_hit": cache_hit,
                "cache_key": program["cache_key"], "graph_hash": program["manifest"]["graph_hash"],
                "program_directory": program["root"], "prepare_ns": prepared - start,
                "execute_ns": body_done - prepared, "result_check_ns": end - body_done,
                "total_ns": end - start, "adapter_calls": context.calls,
                "unique_snapshot_queries": len(context.allowed_reads), "joined_all_blocks": True}}

    def run_sync(self, pack, intent, snapshot):
        start = perf_counter_ns()
        snapshot = deepcopy(snapshot)
        program, hit = self.prepare(pack, intent, snapshot)
        context = AtomicContext(program["tree"], program["graph"], program["pack"], program["intent"], snapshot)
        prepared = perf_counter_ns()
        result = program["functions"]["execute_sync"](context)
        return self.complete(program, snapshot, result, start, prepared, hit, "resident-sync", context)

    async def run_async(self, pack, intent, snapshot, *, source=None, parallel_reads=4,
                        concurrent=True, timeout_s=5):
        if not isinstance(timeout_s, (int, float)) or not 0 < timeout_s <= 60:
            raise ValueError("observation timeout must be in (0, 60]")
        start = perf_counter_ns()
        snapshot = deepcopy(snapshot)
        program, hit = self.prepare(pack, intent, snapshot)
        context = AtomicContext(program["tree"], program["graph"], program["pack"], program["intent"], snapshot,
                                source=source, parallel_reads=parallel_reads, concurrent=concurrent)
        prepared = perf_counter_ns()
        try:
            async with asyncio.timeout(timeout_s):
                result = await program["functions"]["execute_async"](context)
            mode = "resident-async" if concurrent else "resident-async-sequential"
            return self.complete(program, snapshot, result, start, prepared, hit, mode, context)
        finally:
            await context.close()
