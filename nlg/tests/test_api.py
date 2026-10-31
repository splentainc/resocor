"""Public API/CLI and detached-package checks without any POC fixtures."""
import ast
from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import nlg

PACKAGE = Path(nlg.__file__).resolve().parent
ROOT = PACKAGE.parent


def example():
    return {
        "domain": {"name": "standalone_example", "provenance": "explicit synthetic fixture",
                   "entities": {"sample": "Example"},
                   "metrics": {"count": {"noun": {"head": "count"}, "kind": "number", "unit": "count"}},
                   "units": {"count": {"dimension": {"count": 1}}}},
        "facts": [{"id": "count-1", "entity": "sample", "metric": "count", "value": "8", "source": "synthetic example"}],
        "input": {"version": 2, "statement": {"node": "Relation", "op": "eq",
                  "left": {"node": "Property", "entity": "sample", "metric": "count"},
                  "right": {"node": "Quantity", "value": "8", "unit": "count"}}, "result": True}}


class PublicAPITest(unittest.TestCase):
    def test_public_class_and_function_default_to_crisp(self):
        self.assertEqual(nlg.NLG().render(example())["answer"], "The count of Example was 8.")
        self.assertEqual(nlg.render(example())["detail"], "crisp")
        self.assertEqual(nlg.LEVELS, ("crisp", "detailed", "audit"))

    def test_all_levels_work_without_network_or_child_processes(self):
        with ExitStack() as stack:
            for target in ("socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex", "subprocess.Popen"):
                stack.enter_context(patch(target, side_effect=AssertionError("offline runtime")))
            renderer = nlg.NLG()
            for level in nlg.LEVELS:
                data = example()
                original = deepcopy(data)
                out = renderer.render(data, detail=level)
                self.assertEqual(out["outcome"], "answered", out)
                self.assertTrue(out["answer"].startswith("The count of Example was 8."))
                self.assertEqual(original, data)

    def test_invalid_meanings_and_detail_settings_refuse(self):
        self.assertEqual(nlg.render({}, detail="audit")["outcome"], "abstained")
        self.assertEqual(nlg.render(example(), detail="invented")["outcome"], "abstained")
        data = example()
        data["input"]["result"] = False
        self.assertIsNone(nlg.render(data)["answer"])

    def test_json_cli_reads_stdin_and_defaults_to_crisp(self):
        command = [sys.executable, "-m", "nlg", "--input", "-"]
        result = subprocess.run(command, cwd=ROOT, input=json.dumps(example()), capture_output=True, text=True, check=True)
        out = json.loads(result.stdout)
        self.assertEqual(out["detail"], "crisp")
        self.assertEqual(out["answer"], "The count of Example was 8.")

    def test_json_cli_reads_file_and_explicit_audit(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "input.json"
            source.write_text(json.dumps(example()))
            result = subprocess.run([sys.executable, "-m", "nlg", "--input", str(source), "--detail", "audit"],
                                    cwd=ROOT, capture_output=True, text=True, check=True)
            out = json.loads(result.stdout)
            self.assertEqual(out["detail"], "audit")
            self.assertIn("count-1", out["answer"])

    def test_cli_malformed_json_has_a_clear_error(self):
        result = subprocess.run([sys.executable, "-m", "nlg", "--input", "-"], cwd=ROOT,
                                input="{", capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)

    def test_package_imports_have_no_application_or_model_dependencies(self):
        forbidden = {"poc", "engine", "openai", "anthropic", "transformers", "torch", "requests", "httpx"}
        for file in PACKAGE.glob("*.py"):
            tree = ast.parse(file.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [x.name.split(".")[0] for x in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    modules = [(node.module or "").split(".")[0]]
                else:
                    continue
                self.assertFalse(set(modules) & forbidden, file.name)

    def test_copied_package_runs_in_an_isolated_interpreter_without_repo(self):
        with tempfile.TemporaryDirectory() as folder:
            copied = Path(folder) / "nlg"
            shutil.copytree(PACKAGE, copied, ignore=shutil.ignore_patterns("__pycache__"))
            code = (
                "import sys,json; from unittest.mock import patch; "
                "sys.path.insert(0,sys.argv[1]); import nlg; "
                "assert nlg.__file__.startswith(sys.argv[1]); "
                "assert not any(x=='poc' or x.startswith('poc.') or x=='engine' for x in sys.modules); "
                "bundle=json.loads(sys.stdin.read()); "
                "guard=patch('socket.socket.connect',side_effect=AssertionError('offline')); guard.start(); "
                "print(json.dumps(nlg.NLG().render(bundle,detail='audit')))"
            )
            result = subprocess.run([sys.executable, "-I", "-c", code, folder], cwd=folder,
                                    input=json.dumps(example()), capture_output=True, text=True, check=True)
            out = json.loads(result.stdout)
            self.assertEqual(out["outcome"], "answered", out)
            self.assertIn("synthetic example", out["answer"])


if __name__ == "__main__":
    unittest.main()
