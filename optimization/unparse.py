"""
AST -> C++ source text.

Used to show the optimized program in readable form. Parentheses are
re-inserted from operator precedence, so the printed code means exactly
what the tree means. Comments are not part of the AST, so they are lost.
"""
from syntax_analysis import ast_nodes as ast

INDENT = "    "

# Higher number = binds tighter.
BINARY_PREC = {
    "||": 1,
    "&&": 2,
    "|": 3,
    "^": 4,
    "&": 5,
    "==": 6, "!=": 6,
    "<": 7, ">": 7, "<=": 7, ">=": 7,
    "<<": 8, ">>": 8,
    "+": 9, "-": 9,
    "*": 10, "/": 10, "%": 10,
}
PREC_TERNARY = 0
PREC_ASSIGN = -1
PREC_UNARY = 11
PREC_POSTFIX = 12
PREC_PRIMARY = 13


class Unparser:
    def __init__(self, use_std_prefix=False):
        # If the program has no "using namespace std;", 'string' must be std::string.
        self.use_std_prefix = use_std_prefix

    # ----- program / declarations -----
    def program(self, prog):
        has_using = any(isinstance(i, ast.UsingDirective) for i in prog.items)
        self.use_std_prefix = not has_using
        out = []
        prev = None
        for item in prog.items:
            kind = self._group(item)
            if prev is not None and (kind != prev or kind == "func"):
                out.append("")
            out.extend(self.top_level(item))
            prev = kind
        return "\n".join(out) + "\n"

    @staticmethod
    def _group(item):
        if isinstance(item, (ast.Preprocessor, ast.UsingDirective)):
            return "head"
        if isinstance(item, ast.FunctionDecl):
            return "func"
        return "var"

    def top_level(self, item):
        if isinstance(item, ast.Preprocessor):
            return [item.text]
        if isinstance(item, ast.UsingDirective):
            return ["using namespace {};".format(item.namespace)]
        if isinstance(item, ast.FunctionDecl):
            return self.function(item)
        return self.stmt(item, 0)

    def type_text(self, name):
        if name == "string" and self.use_std_prefix:
            return "std::string"
        return name

    def param(self, p):
        text = ("const " if getattr(p, "is_const", False) else "") + self.type_text(p.type_name)
        stars = "*" * getattr(p, "pointer_depth", 0)
        if stars:
            text += stars
        if getattr(p, "is_reference", False):
            text += "&"
        if p.name:
            text += " " + p.name
        elif stars or getattr(p, "is_reference", False):
            pass
        if p.is_array:
            text += "[]"
        return text

    def function(self, fn):
        head = "{} {}({})".format(self.type_text(fn.return_type), fn.name,
                                  ", ".join(self.param(p) for p in fn.params))
        if fn.body is None:
            return [head + ";"]
        return self.block_lines(fn.body, 0, head + " ")

    def declarator(self, d):
        text = ("*" * getattr(d, "pointer_depth", 0)) + d.name
        if d.is_array:
            text += "[{}]".format(self.expr(d.array_size) if d.array_size is not None else "")
        if d.init is not None:
            text += " = " + self.init(d.init)
        return text

    def init(self, node):
        if isinstance(node, ast.InitList):
            return "{" + ", ".join(self.expr(e) for e in node.elements) + "}"
        return self.expr(node)

    def vardecl_text(self, vd):
        prefix = "const " if getattr(vd, "is_const", False) else ""
        return "{}{} {}".format(prefix, self.type_text(vd.type_name),
                                ", ".join(self.declarator(d) for d in vd.declarators))

    # ----- statements -----
    def block_lines(self, block, level, prefix=""):
        pad = INDENT * level
        lines = [pad + prefix + "{"]
        for s in block.statements:
            lines.extend(self.stmt(s, level + 1))
        lines.append(pad + "}")
        return lines

    def body(self, node, level, prefix, force_block=False):
        """Lines for `prefix` + a statement used as a loop/if body."""
        pad = INDENT * level
        if isinstance(node, ast.Block):
            return self.block_lines(node, level, prefix)
        if force_block:
            return [pad + prefix + "{"] + self.stmt(node, level + 1) + [pad + "}"]
        return [pad + prefix.rstrip()] + self.stmt(node, level + 1)

    def stmt(self, s, level):
        pad = INDENT * level
        if isinstance(s, ast.Block):
            return self.block_lines(s, level)
        if isinstance(s, ast.VarDecl):
            return [pad + self.vardecl_text(s) + ";"]
        if isinstance(s, ast.ExprStmt):
            return [pad + self.expr(s.expr) + ";"]
        if isinstance(s, ast.EmptyStmt):
            return [pad + ";"]
        if isinstance(s, ast.Return):
            return [pad + ("return " + self.expr(s.value) + ";" if s.value is not None else "return;")]
        if isinstance(s, ast.Break):
            return [pad + "break;"]
        if isinstance(s, ast.Continue):
            return [pad + "continue;"]
        if isinstance(s, ast.If):
            return self.if_lines(s, level, "if ")
        if isinstance(s, ast.While):
            return self.body(s.body, level, "while ({}) ".format(self.expr(s.condition)))
        if isinstance(s, ast.DoWhile):
            tail = "while ({});".format(self.expr(s.condition))
            lines = self.body(s.body, level, "do ", force_block=True)
            lines[-1] += " " + tail
            return lines
        if isinstance(s, ast.For):
            return self.body(s.body if s.body is not None else ast.EmptyStmt(),
                             level, "for ({}) ".format(self.for_header(s)))
        if isinstance(s, ast.Switch):
            lines = [pad + "switch (" + self.expr(s.expr) + ") {"]
            for section in s.cases:
                if isinstance(section, ast.Case):
                    lines.append(INDENT * (level + 1) + "case " + self.expr(section.value) + ":")
                elif isinstance(section, ast.Default):
                    lines.append(INDENT * (level + 1) + "default:")
                else:
                    continue
                for stmt in section.body:
                    lines.extend(self.stmt(stmt, level + 2))
            lines.append(pad + "}")
            return lines
        if isinstance(s, (ast.Preprocessor, ast.UsingDirective, ast.FunctionDecl)):
            return ["{}{}".format(pad, line) for line in self.top_level(s)]
        raise TypeError("cannot print statement " + type(s).__name__)

    def if_lines(self, s, level, keyword):
        pad = INDENT * level
        has_else = s.else_branch is not None
        lines = self.body(s.then_branch, level, "{}({}) ".format(keyword, self.expr(s.condition)),
                          force_block=has_else)
        if has_else:
            if isinstance(s.else_branch, ast.If):
                nested = self.if_lines(s.else_branch, level, "else if ")
                lines[-1] += " " + nested[0].strip()
                lines.extend(nested[1:])
            else:
                else_lines = self.body(s.else_branch, level, "else ")
                lines[-1] += " " + else_lines[0].strip()
                lines.extend(else_lines[1:])
        return lines

    def for_header(self, f):
        if f.init is None:
            init = ""
        elif isinstance(f.init, ast.VarDecl):
            init = self.vardecl_text(f.init)
        else:
            init = self.expr(f.init)
        cond = self.expr(f.condition) if f.condition is not None else ""
        upd = self.expr(f.update) if f.update is not None else ""
        header = init + ";"
        if cond:
            header += " " + cond
        header += ";"
        if upd:
            header += " " + upd
        return header

    # ----- expressions -----
    def expr(self, e, parent_prec=0, side=None):
        text, prec = self._expr(e)
        if prec < parent_prec or (side == "right" and prec == parent_prec):
            return "(" + text + ")"
        return text

    def _expr(self, e):
        if isinstance(e, ast.Literal):
            neg = e.value.startswith("-")
            return e.value, (PREC_UNARY if neg else PREC_PRIMARY)
        if isinstance(e, ast.Identifier):
            return (e.namespace + "::" + e.name if e.namespace else e.name), PREC_PRIMARY
        if isinstance(e, ast.Binary):
            p = BINARY_PREC[e.op]
            return "{} {} {}".format(self.expr(e.left, p), e.op, self.expr(e.right, p, "right")), p
        if isinstance(e, ast.Ternary):
            return "{} ? {} : {}".format(
                self.expr(e.condition, 1),
                self.expr(e.when_true, 1),
                self.expr(e.when_false, PREC_TERNARY)), PREC_TERNARY
        if isinstance(e, ast.Assign):
            return "{} {} {}".format(self.expr(e.target, PREC_UNARY),
                                     e.op, self.expr(e.value, PREC_TERNARY)), PREC_ASSIGN
        if isinstance(e, ast.Unary):
            inner = self.expr(e.operand, PREC_UNARY)
            if inner.startswith(e.op[0]):
                inner = " " + inner
            return e.op + inner, PREC_UNARY
        if isinstance(e, ast.AddressOf):
            inner = self.expr(e.operand, PREC_UNARY)
            return "&" + inner, PREC_UNARY
        if isinstance(e, ast.Deref):
            inner = self.expr(e.operand, PREC_UNARY)
            return "*" + inner, PREC_UNARY
        if isinstance(e, ast.Postfix):
            return self.expr(e.operand, PREC_POSTFIX) + e.op, PREC_POSTFIX
        if isinstance(e, ast.Call):
            return "{}({})".format(self.expr(e.callee, PREC_POSTFIX),
                                   ", ".join(self.expr(a, 1) for a in e.args)), PREC_POSTFIX
        if isinstance(e, ast.Index):
            return "{}[{}]".format(self.expr(e.array, PREC_POSTFIX), self.expr(e.index)), PREC_POSTFIX
        raise TypeError("cannot print expression " + type(e).__name__)


def unparse(node):
    """C++ source text for a Program (or a single statement / expression)."""
    u = Unparser()
    if isinstance(node, ast.Program):
        return u.program(node)
    if isinstance(node, ast.Expr):
        return u.expr(node)
    return "\n".join(u.stmt(node, 0)) + "\n"