"""Render a caller-supplied JSON bundle from a file or standard input."""
import argparse
import json
from pathlib import Path
import sys

from . import LEVELS, NLG


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="full JSON bundle file; '-' reads stdin")
    parser.add_argument("--detail", choices=LEVELS, default="crisp")
    args = parser.parse_args()
    try:
        source = sys.stdin.read() if args.input == "-" else Path(args.input).read_text()
        bundle = json.loads(source)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(NLG().render(bundle, detail=args.detail), indent=2))


if __name__ == "__main__":
    main()
