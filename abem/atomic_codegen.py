"""Compile independent block functions and sync/async orchestration to Python."""
import ast
from pathlib import Path
import py_compile
import runpy
import sys
from tempfile import TemporaryDirectory

from .compiler import toolchain
from .model import digest


def atomic_toolchain():
    root = Path(__file__).parent
    return {**toolchain(), **{name: digest("implementation", (root / name).read_bytes())
            for name in ("atomic_graph.py", "atomic_codegen.py", "atomic_runtime.py", "atomic_runner.py")}}


def source_for(graph):
    lines = ["# Generated atomic diagnostic blocks; only registered context operations are called."]
    names = []
    for index, block in enumerate(graph["blocks"]):
        name = f"block_{index}"
        names.append(name)
        identity = repr(block["id"])
        lines.extend([f"def {name}_sync(context):", f"    records = context.acquire_sync({identity})",
                      f"    return context.evaluate({identity}, records)",
                      f"async def {name}_async(context):", f"    records = await context.acquire_async({identity})",
                      f"    return context.evaluate({identity}, records)"])
    lines.extend(["def execute_sync(context):",
                  "    return context.finish([" + ", ".join(n + "_sync(context)" for n in names) + "])",
                  "async def execute_async(context):",
                  "    blocks = [" + ", ".join(n + "_async" for n in names) + "]",
                  "    return context.finish(await context.schedule(blocks))"])
    # Parsing this closed compiler template is mechanical AST lowering. No input
    # facts, descriptions, hypothesis labels or arbitrary code are interpolated.
    return ast.unparse(ast.parse("\n".join(lines))) + "\n"


def build_atomic(root, tree, graph):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    source = source_for(graph)
    (root / "blocks.py").write_text(source)
    py_compile.compile(str(root / "blocks.py"), cfile=str(root / "blocks.pyc"),
                       dfile="<abem-atomic-blocks>", doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
    return {"target": "python-atomic-1", "semantic_ast_hash": digest("ast", tree),
            "graph_hash": digest("atomic-graph", graph), "toolchain": atomic_toolchain(),
            "source_hash": digest("atomic-source", source.encode()),
            "binary_hash": digest("atomic-binary", (root / "blocks.pyc").read_bytes()),
            "python_abi": sys.implementation.cache_tag, "python_version": sys.version,
            "approval": "unsigned_fixture", "isolation": "explicit per-invocation state; no OS sandbox"}


def load_atomic(root, tree, graph, manifest):
    root = Path(root)
    if (manifest["semantic_ast_hash"] != digest("ast", tree)
            or manifest["graph_hash"] != digest("atomic-graph", graph)
            or manifest["toolchain"] != atomic_toolchain()
            or manifest["python_abi"] != sys.implementation.cache_tag
            or manifest["python_version"] != sys.version
            or manifest["source_hash"] != digest("atomic-source", source_for(graph).encode())
            or manifest["source_hash"] != digest("atomic-source", (root / "blocks.py").read_bytes())
            or manifest["binary_hash"] != digest("atomic-binary", (root / "blocks.pyc").read_bytes())):
        raise ValueError("atomic compiled artifact/dependency mismatch")
    # An unsigned manifest cannot authenticate replacement bytecode. Rebuild
    # the closed compiler template and compare exact bytes before loading.
    with TemporaryDirectory() as directory:
        expected = build_atomic(Path(directory), tree, graph)
    if expected != manifest:
        raise ValueError("atomic binary does not match reproducible compiler output")
    return runpy.run_path(str(root / "blocks.pyc"), run_name="abem_atomic_program")
