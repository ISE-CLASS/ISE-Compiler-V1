"""
Pass 2: dead code elimination (purely structural, no variable tracking).

  * if (constant)            -> keep only the branch that runs
  * while (false)            -> removed
  * for (...; false; ...)    -> only the init part is kept
  * do { } while (false)     -> body once (when it has no break/continue)
  * code after return / break / continue in the same block -> removed
  * statements with no effect:  5;   x;   x = x;   x += 0;   x *= 1;
  * empty blocks and empty statements
"""
from syntax_analysis import ast_nodes as ast

from .unparse import unparse
from .utils import contains, is_pure, is_number, truthiness


class DeadCodeEliminator:
    def __init__(self, stats):
        self.stats = stats

    def run(self, program):
        for item in program.items:
            if isinstance(item, ast.FunctionDecl) and item.body is not None:
                item.body = self.as_block(self.stmt(item.body))
        return program

    # ---- helpers ----
    @staticmethod
    def as_block(stmts):
        """Turn the result list of stmt() back into one Block."""
        if len(stmts) == 1 and isinstance(stmts[0], ast.Block):
            return stmts[0]
        return ast.Block(stmts, 0, 0)

    @staticmethod
    def as_single(stmts, like):
        if not stmts:
            return ast.Block([], like.line, like.col)
        if len(stmts) == 1:
            return stmts[0]
        return ast.Block(stmts, like.line, like.col)

    def statements(self, stmts):
        out = []
        for s in stmts:
            if out and isinstance(out[-1], (ast.Return, ast.Break, ast.Continue)):
                self.stats.record("unreachable", s.line,
                                  "removed {} after {}".format(type(s).__name__,
                                                               type(out[-1]).__name__.lower()))
                continue
            for r in self.stmt(s):
                # a nested block without declarations is just a grouping: splice it in
                if isinstance(r, ast.Block) and not any(isinstance(x, ast.VarDecl) for x in r.statements):
                    out.extend(r.statements)
                else:
                    out.append(r)
        return out

    # ---- one statement -> list of statements (possibly empty) ----
    def stmt(self, s):
        if isinstance(s, ast.Block):
            s.statements = self.statements(s.statements)
            return [s]
        if isinstance(s, ast.EmptyStmt):
            return []
        if isinstance(s, ast.ExprStmt):
            return self.expr_stmt(s)
        if isinstance(s, ast.If):
            return self.if_stmt(s)
        if isinstance(s, ast.While):
            return self.while_stmt(s)
        if isinstance(s, ast.DoWhile):
            return self.do_while(s)
        if isinstance(s, ast.For):
            return self.for_stmt(s)
        if isinstance(s, ast.Switch):
            return self.switch_stmt(s)
        return [s]

    def expr_stmt(self, s):
        e = s.expr
        why = None
        if is_pure(e):
            why = "statement without effect"
        elif (isinstance(e, ast.Assign) and e.op == "=" and isinstance(e.target, ast.Identifier)
              and isinstance(e.value, ast.Identifier) and e.target.name == e.value.name
              and e.target.namespace == e.value.namespace):
            why = "self assignment"
        elif (isinstance(e, ast.Assign) and e.op in ("+=", "-=", "*=", "/=")
              and isinstance(e.target, ast.Identifier) and isinstance(e.value, ast.Literal)
              and e.value.kind == "int"
              and is_number(e.value, 0 if e.op in ("+=", "-=") else 1)):
            why = "no-op compound assignment"
        if why:
            self.stats.record("dead_expression", s.line, "removed '{};' ({})".format(unparse(e), why))
            return []
        return [s]

    def if_stmt(self, s):
        truth = truthiness(s.condition)
        if truth is not None:
            taken = s.then_branch if truth else s.else_branch
            self.stats.record("dead_branch", s.line,
                              "if ({}) -> {}".format(unparse(s.condition),
                                                     "then branch only" if truth else
                                                     ("else branch only" if taken is not None else "removed")))
            return self.stmt(taken) if taken is not None else []
        then = self.as_single(self.stmt(s.then_branch), s)
        other = None
        if s.else_branch is not None:
            other_list = self.stmt(s.else_branch)
            other = self.as_single(other_list, s) if other_list else None
        then_empty = isinstance(then, ast.Block) and not then.statements
        other_empty = other is None or (isinstance(other, ast.Block) and not other.statements)
        if then_empty and other_empty:
            self.stats.record("dead_branch", s.line, "if ({}) has empty branches".format(unparse(s.condition)))
            return [] if is_pure(s.condition) else [ast.ExprStmt(s.condition, s.line, s.col)]
        if then_empty:
            self.stats.record("dead_branch", s.line, "empty then-branch: condition inverted")
            s.condition = ast.Unary("!", s.condition, s.line, s.col)
            s.then_branch, s.else_branch = other, None
            return [s]
        s.then_branch = then
        s.else_branch = None if other_empty else other
        return [s]

    def while_stmt(self, s):
        if truthiness(s.condition) is False:
            self.stats.record("dead_loop", s.line, "while ({}) never runs: removed".format(unparse(s.condition)))
            return []
        s.body = self.as_single(self.stmt(s.body), s)
        return [s]

    def do_while(self, s):
        if truthiness(s.condition) is False and not contains(s.body, ast.Break, ast.Continue):
            self.stats.record("dead_loop", s.line, "do-while (false) runs once: loop removed")
            body = self.as_single(self.stmt(s.body), s)
            return [body if isinstance(body, ast.Block) else ast.Block([body], s.line, s.col)]
        s.body = self.as_single(self.stmt(s.body), s)
        return [s]

    def for_stmt(self, s):
        if s.condition is not None and truthiness(s.condition) is False:
            self.stats.record("dead_loop", s.line, "for loop never runs: only its init is kept")
            if s.init is None:
                return []
            if isinstance(s.init, ast.VarDecl):
                return [ast.Block([s.init], s.line, s.col)]
            return [] if is_pure(s.init) else [ast.ExprStmt(s.init, s.line, s.col)]
        if s.body is not None:
            s.body = self.as_single(self.stmt(s.body), s)
        return [s]

    def switch_stmt(self, s):
        for section in s.cases:
            if isinstance(section, ast.Case):
                section.body = self.statements(section.body)
            elif isinstance(section, ast.Default):
                section.body = self.statements(section.body)
        return [s]