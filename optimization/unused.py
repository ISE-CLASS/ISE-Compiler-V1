"""
Pass 3: remove local variables that are never read.

A local scalar is removed when, inside its function,
  * it is declared exactly once and is not a parameter or a global name,
  * every use of it is a statement that only writes to it
    (`x = <pure>;`, `x += <pure>;`, `x++;`) or its own declaration,
  * its initializer is pure (no calls, no cout/cin).
Arrays are never removed. Variables read by `cin >> x` count as used.
The driver runs this pass repeatedly, so chains of unused variables
disappear one after another.
"""
from collections import Counter

from syntax_analysis import ast_nodes as ast

from .utils import is_pure, walk


def _is_write_stmt(s):
    """If `s` is a removable write statement, return the variable name."""
    if not isinstance(s, ast.ExprStmt):
        return None
    e = s.expr
    if isinstance(e, ast.Assign) and isinstance(e.target, ast.Identifier) and is_pure(e.value):
        return e.target.name
    if isinstance(e, ast.Postfix) and e.op in ("++", "--") and isinstance(e.operand, ast.Identifier):
        return e.operand.name
    if isinstance(e, ast.Unary) and e.op in ("++", "--") and isinstance(e.operand, ast.Identifier):
        return e.operand.name
    return None


def _occurrences(node, name):
    return sum(1 for n in walk(node) if isinstance(n, ast.Identifier)
               and n.namespace is None and n.name == name)


class UnusedVariableRemover:
    def __init__(self, stats):
        self.stats = stats

    def run(self, program):
        global_names = set()
        for item in program.items:
            if isinstance(item, ast.VarDecl):
                global_names.update(d.name for d in item.declarators)
        for item in program.items:
            if isinstance(item, ast.FunctionDecl) and item.body is not None:
                self.function(item, global_names)
        return program

    def function(self, fn, global_names):
        declared = Counter(p.name for p in fn.params if p.name)
        for n in walk(fn.body):
            if isinstance(n, ast.VarDecl):
                for d in n.declarators:
                    declared[d.name] += 1

        # candidate declarators that sit directly in a statement list
        candidates = {}
        for n in walk(fn.body):
            if isinstance(n, ast.Block):
                for st in n.statements:
                    if isinstance(st, ast.VarDecl):
                        for d in st.declarators:
                            if (declared[d.name] == 1 and d.name not in global_names and not d.is_array
                                    and (d.init is None or is_pure(d.init))):
                                candidates[d.name] = d

        dead = set()
        for name in candidates:
            total = _occurrences(fn.body, name)
            removable = 0
            for n in walk(fn.body):
                if isinstance(n, ast.ExprStmt) and _is_write_stmt(n) == name:
                    removable += _occurrences(n, name)
            if total == removable:
                dead.add(name)
        if not dead:
            return
        fn.body = self.rewrite(fn.body, dead)

    def rewrite_list(self, stmts, dead):
        out = []
        for s in stmts:
            r = self.rewrite(s, dead)
            if r is not None:
                out.append(r)
        return out

    def rewrite(self, s, dead):
        """Return the statement without dead variables, or None if nothing is left."""
        if isinstance(s, ast.Block):
            s.statements = self.rewrite_list(s.statements, dead)
            return s
        if isinstance(s, ast.VarDecl):
            keep = []
            for d in s.declarators:
                if d.name in dead:
                    self.stats.record("unused_variable", d.line or s.line,
                                      "removed unused variable '{}'".format(d.name))
                else:
                    keep.append(d)
            s.declarators = keep
            return s if keep else None
        if isinstance(s, ast.ExprStmt):
            name = _is_write_stmt(s)
            return None if name in dead else s
        if isinstance(s, ast.If):
            s.then_branch = self.rewrite(s.then_branch, dead) or ast.EmptyStmt(s.line, s.col)
            if s.else_branch is not None:
                s.else_branch = self.rewrite(s.else_branch, dead) or ast.EmptyStmt(s.line, s.col)
            return s
        if isinstance(s, (ast.While, ast.DoWhile)):
            s.body = self.rewrite(s.body, dead) or ast.EmptyStmt(s.line, s.col)
            return s
        if isinstance(s, ast.For):
            if s.body is not None:
                s.body = self.rewrite(s.body, dead) or ast.EmptyStmt(s.line, s.col)
            return s
        return s