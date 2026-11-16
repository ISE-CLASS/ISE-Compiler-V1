"""Command line entry point: ``python -m compiler main.cpp``."""
import argparse
import json
import sys
from pathlib import Path

from .pipeline import compile_source


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m compiler",
        description="Compile the supported C++ subset to x86-64 assembly.")
    parser.add_argument("file", help="C++ source file")
    parser.add_argument("--stage", default="compile",
                        choices=["tokenize", "parse", "optimize", "semantic",
                                 "ir", "assembly", "compile", "run"],
                        help="stop after this stage (default: compile and run)")
    parser.add_argument("--no-optimize", action="store_true",
                        help="disable the optimizer")
    parser.add_argument("--stdin", default="", help="text fed to the program")
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--asm", metavar="PATH", help="write assembly to PATH")
    parser.add_argument("--json", action="store_true", help="print the full JSON result")
    args = parser.parse_args(argv)

    path = Path(args.file)
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        print("error: cannot read {}: {}".format(path, exc), file=sys.stderr)
        return 2

    result = compile_source(
        source,
        filename=path.name,
        stage=args.stage,
        optimize=not args.no_optimize,
        stdin=args.stdin,
        timeout=args.timeout,
    )
    if args.asm and result.get("assembly"):
        Path(args.asm).write_text(result["assembly"], encoding="utf-8")
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for diagnostic in result.get("diagnostics", []):
            print(diagnostic)
        if result.get("output"):
            print(result["output"])
        if result.get("assembly") and args.stage in ("assembly", "compile", "run"):
            print("\n--- assembly ---")
            print(result["assembly"])
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
