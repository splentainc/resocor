"""Standalone standard-library operation and CLI authoring/review controls."""
from pathlib import Path
import json
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]


class CliTest(unittest.TestCase):
    def call(self, *args):
        result = subprocess.run([sys.executable, "-S", "-m", "dmn", *args], cwd=ROOT,
                                capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_standalone_feel_works_without_site_packages(self):
        result = self.call("--feel", "0.1 + 0.2")
        self.assertEqual(result, {"value": "0.3", "errors": []})

    def test_model_execution_and_review(self):
        path = "dmn/examples/revenue_review.json"
        result = self.call(path, "--inputs", '{"revenue":"3000"}')
        self.assertEqual(result["decision"], {"band": "large_review"})
        self.assertFalse(result["escalated"])
        described = self.call(path, "--describe")
        self.assertEqual(described["requirements"], [{"input": "revenue", "decision": "revenue_review"}])
        self.assertTrue(self.call(path, "--analyze")["complete"])


if __name__ == "__main__":
    unittest.main()
