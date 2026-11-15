"""
Command-line entry point (Group 3: AST + optimization).

    python -m optimization program.cpp                 # report + optimized C++ code
    python -m optimization program.cpp --verbose       # also list every change
    python -m optimization program.cpp --ast           # AST before and after
    python -m optimization program.cpp -o out.cpp      # write optimized code to a file
    python -m optimization program.cpp --dot tree      # tree_before.dot / tree_after.dot (Graphviz)
    python -m optimization program.cpp --json tree     # tree_before.json / tree_after.json

Exit code: 0 = ok, 1 = lexical or syntax errors, 2 = bad usage / unreadable file.
"""
import argparse
import sys

from syntax_analysis import format_ast

from . import optimize_source
from .ast_export import ast_stats, to_dot, to_json


def _can_print_unicode():
    try:
        "\u2514\u2500\u2500\u2502\u251c".encode(sys.stdout.encoding or "ascii")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("  wrote " + path)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m optimization",
                                 description="Parse a C++ subset file, build its AST and optimize it.")
    ap.add_argument("source_file")
    ap.add_argument("--ast", action="store_true", help="print the AST before and after optimization")
    ap.add_argument("-v", "--verbose", action="store_true", help="list every single change")
    ap.add_argument("-o", "--output", metavar="FILE", help="write the optimized C++ code to FILE")
    ap.add_argument("--dot", metavar="PREFIX", help="export both ASTs as Graphviz files PREFIX_before.dot / PREFIX_after.dot")
    ap.add_argument("--json", metavar="PREFIX", help="export both ASTs as JSON PREFIX_before.json / PREFIX_after.json")
    args = ap.parse_args(argv)

    try:
        with open(args.source_file, encoding="utf-8", errors="replace") as f:
            source = f.read()
    except OSError as e:
        print("Cannot read {}: {}".format(args.source_file, e), file=sys.stderr)
        return 2

    out = optimize_source(source)
    if out.lexical_errors:
        print("Lexical errors (optimization skipped):")
        for e in out.lexical_errors:
            print("  " + e)
        return 1
    if out.syntax_errors:
        for e in out.syntax_errors:
            print(e)
        print("\n{} syntax error(s) found. Optimization skipped.".format(len(out.syntax_errors)))
        return 1

    ascii_only = not _can_print_unicode()
    if args.ast:
        print("=== AST before optimization ===")
        print(format_ast(out.original, False, ascii_only))
        print("\n=== AST after optimization ===")
        print(format_ast(out.optimized, False, ascii_only))
        print()

    before, after = ast_stats(out.original), ast_stats(out.optimized)
    print("=== Optimization report ===")
    print("  AST nodes: {} -> {}   (depth {} -> {})   passes: {}".format(
        before["total_nodes"], after["total_nodes"], before["depth"], after["depth"], out.rounds))
    print("  Changes made: {}".format(out.stats.total))
    for line in out.stats.summary_lines():
        print(line)
    if args.verbose and out.stats.events:
        print("\n=== Every change ===")
        for line in out.stats.event_lines():
            print(line)

    print("\n=== Optimized code ===")
    print(out.code, end="")

    if args.output or args.dot or args.json:
        print()
    if args.output:
        _write(args.output, out.code)
    if args.dot:
        _write(args.dot + "_before.dot", to_dot(out.original, "AST before optimization"))
        _write(args.dot + "_after.dot", to_dot(out.optimized, "AST after optimization"))
        print("  render with:  dot -Tpng {0}_before.dot -o {0}_before.png".format(args.dot))
    if args.json:
        _write(args.json + "_before.json", to_json(out.original))
        _write(args.json + "_after.json", to_json(out.optimized))
    return 0


if __name__ == "__main__":
    sys.exit(main())