"""
Optimization driver: runs the passes over and over until nothing changes.

    from optimization import optimize
    result = optimize(program)          # program: syntax_analysis Program
    result.ast                          # the optimized tree (input is untouched)
    result.stats                        # what was done
"""
from dataclasses import dataclass
from typing import Optional

from syntax_analysis import ast_nodes as ast

from .dead_code import DeadCodeEliminator
from .simplify import Simplifier
from .stats import Stats
from .unused import UnusedVariableRemover
from .utils import clone, node_count, same_tree

MAX_ROUNDS = 20


@dataclass
class OptimizationResult:
    ast: ast.Program
    stats: Stats
    rounds: int
    nodes_before: int
    nodes_after: int


def optimize(program, max_rounds=MAX_ROUNDS):
    """Optimize a Program AST. The given tree is not modified."""
    work = clone(program)
    stats = Stats()
    nodes_before = node_count(program)

    global_types = {}
    return_types = {}
    for item in work.items:
        if isinstance(item, ast.VarDecl):
            for d in item.declarators:
                global_types[d.name] = item.type_name
        elif isinstance(item, ast.FunctionDecl):
            return_types[item.name] = None if item.return_type == "void" else item.return_type

    rounds = 0
    while rounds < max_rounds:
        rounds += 1
        before = clone(work)
        Simplifier(stats, global_types, return_types).run(work)
        DeadCodeEliminator(stats).run(work)
        UnusedVariableRemover(stats).run(work)
        if same_tree(before, work):
            break
    return OptimizationResult(work, stats, rounds, nodes_before, node_count(work))