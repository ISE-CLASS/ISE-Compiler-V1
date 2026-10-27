"""
Command-line entry point.

    python -m syntax_analysis program.cpp              # print the AST or the errors
    python -m syntax_analysis program.cpp --positions  # also show line:col of each node

Exit code: 0 = parsed OK, 1 = lexical or syntax errors, 2 = bad usage / unreadable file.
"""
import argparse
import sys

from .ast_printer import format_ast
from .parser import parse_source


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m syntax_analysis",
                                 description="Parse a C++ subset source file and print its AST.")
    ap.add_argument("source_file")
    ap.add_argument("--positions", action="store_true", help="show line:col for each AST node")
    args = ap.parse_args(argv)

    try:
        with open(args.source_file, encoding="utf-8", errors="replace") as f:
            source = f.read()
    except OSError as e:
        print("Cannot read {}: {}".format(args.source_file, e), file=sys.stderr)
        return 2

    result = parse_source(source)

    if result.lexical_errors:
        print("Lexical errors (parsing skipped):")
        for e in result.lexical_errors:
            print("  " + e)
        return 1

    if result.syntax_errors:
        for e in result.syntax_errors:
            print(e)
        print("\n{} syntax error(s) found.".format(len(result.syntax_errors)))
        return 1

    print(format_ast(result.ast, args.positions, ascii_only=not _can_print_unicode()))
    print("\nParsed successfully.")
    return 0


def _can_print_unicode():
    try:
        "└──│├".encode(sys.stdout.encoding or "ascii")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


if __name__ == "__main__":
    sys.exit(main())
