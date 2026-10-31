"""Import the existing script-based engine without changing its parser modules."""
from functools import lru_cache
from importlib import import_module
from pathlib import Path
import sys


@lru_cache(maxsize=1)
def execution_module():
    # Existing engine scripts use sibling imports. Restrict the path change to
    # their import boundary; the reusable NLG package never needs this shim.
    directory = str(Path(__file__).resolve().parent)
    sys.path.insert(0, directory)
    try:
        return import_module("execute_tree")
    finally:
        sys.path.remove(directory)
