"""Typed Plan AST -> Python AST -> source -> conventional CPython bytecode."""
import ast
import os
from pathlib import Path
import py_compile
import subprocess
import sys

from .compiler import lower
from .model import digest

METHODS = {1: "check", 2: "authorize", 3: "invoke", 4: "expect", 5: "assert_goal", 6: "complete"}


def program_ast(pack, plan):
    """Language-neutral AST, with contracts referenced from verified Plan IR."""
    operators = {o["id"]: o for o in pack["operators"]}
    nodes = []
    for opcode, arg in lower(plan):
        node = {"kind": METHODS[opcode], "argument": arg}
        if opcode < 5:
            operation = plan["nodes"][arg]
            contract = operators[operation["op"]]
            node.update(operator=operation["op"], capsule_hash=operation["capsule_hash"],
                        input_bindings=operation["bindings"], input_types=contract["parameters"],
                        output_types={a[0]: pack["predicates"][a[0]] for a in operation["expected"]},
                        effect=operation["kind"])
        nodes.append(node)
    return {"schema_version": "abem-ast-1", "kind": "Sequence", "body": nodes,
            "plan_hash": digest("plan", plan), "return_type": "DecisionEnvelope",
            "failure_control": "return_on_non_null_host_result"}


def literal(value):
    if isinstance(value, list):
        return ast.List(elts=[literal(v) for v in value], ctx=ast.Load())
    if isinstance(value, dict):
        return ast.Dict(keys=[literal(k) for k in value], values=[literal(v) for v in value.values()])
    return ast.Constant(value=value)


def python_source(tree):
    body = []
    for node in tree["body"]:
        call = ast.Call(func=ast.Attribute(value=ast.Name(id="session", ctx=ast.Load()),
                        attr=node["kind"], ctx=ast.Load()), args=[literal(node["argument"])], keywords=[])
        if node["kind"] in ("complete", "complete_diagnosis"):
            body.append(ast.Return(value=call))
        else:
            body.extend([ast.Assign(targets=[ast.Name(id="result", ctx=ast.Store())], value=call),
                         ast.If(test=ast.Compare(left=ast.Name(id="result", ctx=ast.Load()),
                                ops=[ast.IsNot()], comparators=[ast.Constant(value=None)]),
                                body=[ast.Return(value=ast.Name(id="result", ctx=ast.Load()))], orelse=[])])
    function = ast.FunctionDef(name="generated_program", args=ast.arguments(posonlyargs=[],
        args=[ast.arg(arg="session")], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=body, decorator_list=[], type_params=[])
    host = "diagnostic_host" if tree["kind"] == "DiagnosticSequence" else "generated_host"
    main_guard = ast.If(test=ast.Compare(left=ast.Name(id="__name__", ctx=ast.Load()),
        ops=[ast.Eq()], comparators=[ast.Constant(value="__main__")]), body=[
        ast.ImportFrom(module="abem." + host, names=[ast.alias(name="main")], level=0),
        ast.Expr(value=ast.Call(func=ast.Name(id="main", ctx=ast.Load()),
                               args=[ast.Name(id="generated_program", ctx=ast.Load())], keywords=[]))], orelse=[])
    module = ast.fix_missing_locations(ast.Module(body=[function, main_guard], type_ignores=[]))
    return "# Generated from typed ABEM AST; sequencing and branches are generated.\n" + ast.unparse(module) + "\n"


def build_python(root, pack, plan):
    tree = program_ast(pack, plan)
    return build_python_ast(root, tree, "abem.runtime.Session"), tree


def build_python_ast(root, tree, dependency):
    root = Path(root)
    source = python_source(tree)
    (root / "program.py").write_text(source)
    py_compile.compile(str(root / "program.py"), cfile=str(root / "program.pyc"),
                       dfile="<abem-generated-program>", doraise=True,
                       invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
    return {"target": "python", "ast_hash": digest("ast", tree),
            "source_hash": digest("python-source", source.encode()),
            "compiled_hash": digest("python-bytecode", (root / "program.pyc").read_bytes()),
            "compiler": sys.version, "abi": sys.implementation.cache_tag,
            "compiler_build_hash": digest("python-compiler", Path(py_compile.__file__).read_bytes()),
            "source_is_derived": True, "runtime_dependency": dependency}


def run_python(root, mode="replay", launcher=None):
    """Run the actual generated .pyc in another process; no exec/eval in planner."""
    root = Path(root).resolve()
    repository = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repository) + os.pathsep + env.get("PYTHONPATH", "")
    command = [sys.executable, str(root / "program.pyc")] if launcher is None else [sys.executable, "-m", launcher]
    completed = subprocess.run(command + ["--bundle", str(root),
                               "--mode", mode, "--output", str(root / "execution.python.json")],
                               env=env, capture_output=True, text=True, timeout=30, check=False)
    if completed.returncode:
        raise ValueError("generated Python failed: " + completed.stderr[-2000:])
    import json
    return json.loads((root / "execution.python.json").read_text())
