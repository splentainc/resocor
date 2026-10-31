"""In-process FEEL decision tables. No JVM, model, network or child process."""
from .feel import Expression, evaluate, evaluate_checked
from .model import DecisionModel
from .unary import UnaryTest
from .values import FeelError

__version__ = "0.1.0"
__all__ = ["DecisionModel", "Expression", "UnaryTest", "FeelError", "evaluate", "evaluate_checked"]
