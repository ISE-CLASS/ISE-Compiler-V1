"""
Syntax analysis phase of the C++ subset compiler.

Typical use by the next phase:

    from syntax_analysis import parse_source

    result = parse_source(source_text)
    if result.ok:
        program = result.ast          # syntax_analysis.ast_nodes.Program
    else:
        for e in result.lexical_errors: print(e)
        for e in result.syntax_errors: print(e)
"""
from .ast_printer import format_ast, print_ast
from .errors import ParseError
from .parser import ParseResult, Parser, parse_source

__all__ = ["parse_source", "ParseResult", "Parser", "ParseError", "format_ast", "print_ast"]
