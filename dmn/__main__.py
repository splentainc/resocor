"""Evaluate FEEL or review/run an authored JSON decision table."""
import argparse
import json

from . import DecisionModel, evaluate_checked
from .schema import load_json
from .values import wire


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", nargs="?")
    parser.add_argument("--feel", help="evaluate one FEEL expression")
    parser.add_argument("--inputs", default="{}", help="JSON input values")
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--analyze", action="store_true")
    args = parser.parse_args()
    if args.feel is not None:
        if args.model or args.describe or args.analyze:
            parser.error("--feel cannot be combined with model operations")
        result = evaluate_checked(args.feel, load_json(args.inputs))
        out = {"value": wire(result.value), "errors": result.errors}
    elif args.model:
        model = DecisionModel.load(args.model)
        out = model.describe() if args.describe else model.analysis if args.analyze else model.evaluate(load_json(args.inputs))
    else:
        parser.error("provide a model or --feel")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
