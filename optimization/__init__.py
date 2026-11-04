"""
Group 3: Abstract Syntax Tree tools and code optimization.

Pipeline position:   lexer -> parser (AST) -> OPTIMIZER -> code generation

    from optimization import optimize_source

    out = optimize_source(open("main.cpp").read())
    if out.ok:
        optimized_program = out.optimized     # syntax_analysis Program
        print(out.code)                       # optimized C++ source
"""
from dataclasses import dataclass, field
from typing import List, Optional

from syntax_analysis import parse_source

from .ast_export import ast_stats, to_dict, to_dot, to_json
from .optimizer import OptimizationResult, optimize
from .stats import Stats
from .unparse import unparse

__all__ = ["optimize", "optimize_source", "OptimizationResult", "Stats",
           "unparse", "to_dict", "to_json", "to_dot", "ast_stats"]


@dataclass
class PipelineResult:
    original: object = None                  # AST before optimization
    optimized: object = None                 # AST after optimization
    code: str = ""                           # optimized C++ source
    stats: Optional[Stats] = None
    rounds: int = 0
    lexical_errors: List[str] = field(default_factory=list)
    syntax_errors: List[object] = field(default_factory=list)

    @property
    def ok(self):
        return not self.lexical_errors and not self.syntax_errors


def optimize_source(source):
    """Parse C++ source text and optimize it (only if it parsed without errors)."""
    parsed = parse_source(source)
    result = PipelineResult(original=parsed.ast, lexical_errors=parsed.lexical_errors,
                            syntax_errors=parsed.syntax_errors)
    if not parsed.ok:
        return result
    opt = optimize(parsed.ast)
    result.optimized = opt.ast
    result.code = unparse(opt.ast)
    result.stats = opt.stats
    result.rounds = opt.rounds
    return result