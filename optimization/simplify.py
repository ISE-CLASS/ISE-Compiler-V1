"""
Pass 1: constant propagation + constant folding + algebraic simplification
+ strength reduction. All four work on expressions, so they share one walk.

Soundness rules (the optimizer must never change what the program prints):
  * Only LOCAL scalar variables of type int / bool / double are propagated.
    Globals, arrays, char, string and float are left alone.
  * A variable's known value is forgotten when a loop or branch may change it,
    when it is changed by ++/--, or when it is read by `cin >> x`.
  * Integer folding follows C++: division truncates toward zero, results must
    fit in 32 bits (otherwise we leave the expression untouched), division
    by zero is never folded.
  * Identities such as x + 0 -> x are only applied when the type of x is
    known to be int/float/double (char and bool would change type).
"""
import math

from syntax_analysis import ast_nodes as ast

from .unparse import unparse
from .utils import (INT_MAX, INT_MIN, assigned_vars, base_name, is_number,
                    is_pure, literal_value, make_literal, walk)

NUMERIC = ("int", "float", "double")


class Entry:
    """What we know about one local variable."""
    __slots__ = ("type", "value")

    def __init__(self, type_name, value):
        self.type = type_name
        self.value = value          # a Literal node, or None when unknown


def convert(lit, type_name):
    """Literal as stored in a variable of `type_name`, or None if unsure."""
    lv = literal_value(lit)
    if lv is None:
        return None
    kind, v = lv
    if type_name == "int":
        if kind == "float":
            if not math.isfinite(v) or not (INT_MIN <= int(v) <= INT_MAX):
                return None
            v = int(v)
        return make_literal("int", int(v), lit)
    if type_name == "bool":
        return make_literal("bool", bool(v), lit)
    if type_name == "double":
        return make_literal("float", float(v), lit)
    return None


def _trunc_div(a, b):
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def compute(op, a, b):
    """Evaluate `a op b` for two (kind, value) constants -> (kind, value) | None."""
    ka, va = a
    kb, vb = b
    is_float = "float" in (ka, kb)
    if op == "&&":
        return ("bool", bool(va) and bool(vb))
    if op == "||":
        return ("bool", bool(va) or bool(vb))
    if op in ("==", "!=", "<", ">", "<=", ">="):
        r = {"==": va == vb, "!=": va != vb, "<": va < vb,
             ">": va > vb, "<=": va <= vb, ">=": va >= vb}[op]
        return ("bool", r)
    if op in ("+", "-", "*"):
        r = {"+": va + vb, "-": va - vb, "*": va * vb}[op]
        if is_float:
            return ("float", float(r)) if math.isfinite(r) else None
        r = int(r)
        return ("int", r) if INT_MIN <= r <= INT_MAX else None
    if op == "/":
        if vb == 0:
            return None
        if is_float:
            r = va / vb
            return ("float", r) if math.isfinite(r) else None
        r = _trunc_div(int(va), int(vb))
        return ("int", r) if INT_MIN <= r <= INT_MAX else None
    if op == "%":
        if is_float or vb == 0:
            return None
        va, vb = int(va), int(vb)
        return ("int", va - vb * _trunc_div(va, vb))
    if op in ("<<", ">>", "&", "|", "^"):
        if ka != "int" or kb != "int":
            return None
        if op in ("<<", ">>"):
            if va < 0 or not (0 <= vb < 32):
                return None
            r = (va << vb) if op == "<<" else (va >> vb)
        elif op == "&":
            r = va & vb
        elif op == "|":
            r = va | vb
        else:
            r = va ^ vb
        return ("int", r) if INT_MIN <= r <= INT_MAX else None
    return None


class Simplifier:
    def __init__(self, stats, global_types, return_types):
        self.stats = stats
        self.global_types = global_types      # name -> type
        self.return_types = return_types      # function name -> type
        self.types = {}                       # per function: name -> type or None

    # ------------------------------------------------------------
    # entry point
    # ------------------------------------------------------------
    def run(self, program):
        # global initializers: fold only (globals are never propagated)
        for item in program.items:
            if isinstance(item, ast.VarDecl):
                self.var_decl(item, {}, track=False)
        for item in program.items:
            if isinstance(item, ast.FunctionDecl) and item.body is not None:
                self.function(item)
        return program

    def function(self, fn):
        self.types = dict(self.global_types)
        for p in fn.params:
            if p.name:
                self._declare_type(p.name, p.type_name)
        self._collect_local_types(fn.body)
        env = {}
        for p in fn.params:
            if p.name:
                env[p.name] = Entry(p.type_name, None)
        self.stmt(fn.body, env)

    def _declare_type(self, name, type_name):
        if name in self.types and self.types[name] != type_name:
            self.types[name] = None           # declared twice with different types
        else:
            self.types[name] = type_name

    def _collect_local_types(self, node):
        for n in walk(node):
            if isinstance(n, ast.VarDecl):
                for d in n.declarators:
                    self._declare_type(d.name, n.type_name)

    # ------------------------------------------------------------
    # statements
    # ------------------------------------------------------------
    def stmt(self, s, env):
        if isinstance(s, ast.Block):
            inner = dict(env)
            s.statements = [self.stmt(x, inner) for x in s.statements]
            self._kill(env, assigned_vars(s))
        elif isinstance(s, ast.VarDecl):
            self.var_decl(s, env)
        elif isinstance(s, ast.ExprStmt):
            s.expr = self.expr(s.expr, env)
        elif isinstance(s, ast.Return):
            if s.value is not None:
                s.value = self.expr(s.value, env)
        elif isinstance(s, ast.If):
            s.condition = self.expr(s.condition, env)
            s.then_branch = self.stmt(s.then_branch, dict(env))
            if s.else_branch is not None:
                s.else_branch = self.stmt(s.else_branch, dict(env))
            self._kill(env, assigned_vars(s))
        elif isinstance(s, ast.While):
            self._kill(env, assigned_vars(s))
            s.condition = self.expr(s.condition, env)
            s.body = self.stmt(s.body, dict(env))
        elif isinstance(s, ast.DoWhile):
            self._kill(env, assigned_vars(s))
            s.body = self.stmt(s.body, dict(env))
            s.condition = self.expr(s.condition, env)
        elif isinstance(s, ast.For):
            inner = dict(env)
            if isinstance(s.init, ast.VarDecl):
                self.var_decl(s.init, inner)
            elif s.init is not None:
                s.init = self.expr(s.init, inner)
            killed = assigned_vars(s)
            self._kill(inner, killed)
            self._kill(env, killed)
            if s.condition is not None:
                s.condition = self.expr(s.condition, inner)
            if s.update is not None:
                s.update = self.expr(s.update, inner)
            if s.body is not None:
                s.body = self.stmt(s.body, dict(inner))
        elif isinstance(s, ast.Switch):
            s.expr = self.expr(s.expr, env)
            # Conservatively forget values assigned in any case/default body.
            self._kill(env, assigned_vars(s))
            for section in s.cases:
                if isinstance(section, ast.Case):
                    section.value = self.expr(section.value, dict(env))
                if isinstance(section, (ast.Case, ast.Default)):
                    inner = dict(env)
                    section.body = [self.stmt(x, inner) for x in section.body]
        return s

    def var_decl(self, vd, env, track=True):
        for d in vd.declarators:
            if d.array_size is not None:
                d.array_size = self.expr(d.array_size, env)
            if isinstance(d.init, ast.InitList):
                d.init.elements = [self.expr(e, env) for e in d.init.elements]
            elif d.init is not None:
                d.init = self.expr(d.init, env)
            if track:
                value = None
                if not d.is_array and isinstance(d.init, ast.Literal):
                    value = convert(d.init, vd.type_name)
                env[d.name] = Entry(vd.type_name, value)

    @staticmethod
    def _kill(env, names):
        for n in names:
            if n in env:
                env[n] = Entry(env[n].type, None)

    # ------------------------------------------------------------
    # types (best effort; None = unknown)
    # ------------------------------------------------------------
    def type_of(self, e):
        if isinstance(e, ast.Literal):
            if e.kind == "float":
                return "float" if e.value[-1] in "fF" else "double"
            return {"int": "int", "bool": "bool", "char": "char", "string": "string"}.get(e.kind)
        if isinstance(e, ast.Identifier):
            return None if e.namespace else self.types.get(e.name)
        if isinstance(e, ast.Index):
            return self.type_of(e.array)
        if isinstance(e, ast.Call):
            if isinstance(e.callee, ast.Identifier):
                return self.return_types.get(e.callee.name)
            return None
        if isinstance(e, ast.Ternary):
            t = self.type_of(e.when_true)
            f = self.type_of(e.when_false)
            if t is not None and f is not None and t == f:
                return t
            if t in NUMERIC and f in NUMERIC:
                if "double" in (t, f):
                    return "double"
                if "float" in (t, f):
                    return "float"
                return "int"
            return t or f
        if isinstance(e, ast.Assign):
            return self.type_of(e.target)
        if isinstance(e, (ast.Postfix,)) or (isinstance(e, ast.Unary) and e.op in ("++", "--")):
            return self.type_of(e.operand)
        if isinstance(e, ast.Unary):
            if e.op == "!":
                return "bool"
            t = self.type_of(e.operand)
            return "int" if t in ("bool", "char") else (t if t in NUMERIC else None)
        if isinstance(e, ast.AddressOf):
            t = self.type_of(e.operand)
            return (t + "*") if t else None
        if isinstance(e, ast.Deref):
            t = self.type_of(e.operand)
            if t and t.endswith("*"):
                return t[:-1]
            return None
        if isinstance(e, ast.Binary):
            if e.op in ("==", "!=", "<", ">", "<=", ">=", "&&", "||"):
                return "bool"
            if e.op in ("<<", ">>"):
                return "int" if self.type_of(e.left) == "int" else None
            lt, rt = self.type_of(e.left), self.type_of(e.right)
            if lt is None or rt is None:
                return None
            ok = NUMERIC + ("bool", "char")
            if lt not in ok or rt not in ok:
                return None
            if "double" in (lt, rt):
                return "double"
            if "float" in (lt, rt):
                return "float"
            return "int"
        return None

    # ------------------------------------------------------------
    # expressions
    # ------------------------------------------------------------
    def lvalue(self, e, env):
        """Optimize inside an assignment target without replacing the variable."""
        if isinstance(e, ast.Index):
            e.array = self.lvalue(e.array, env) if not isinstance(e.array, ast.Identifier) else e.array
            e.index = self.expr(e.index, env)
            return e
        if isinstance(e, ast.Deref):
            e.operand = self.expr(e.operand, env)
            return e
        if isinstance(e, ast.Identifier):
            return e
        return self.expr(e, env)

    def expr(self, e, env):
        if isinstance(e, ast.Literal):
            return e
        if isinstance(e, ast.Identifier):
            return self._propagate(e, env)
        if isinstance(e, ast.Unary):
            if e.op in ("++", "--"):
                e.operand = self.lvalue(e.operand, env)
                self._kill(env, {base_name(e.operand)})
                return e
            e.operand = self.expr(e.operand, env)
            return self._simplify_unary(e)
        if isinstance(e, ast.Postfix):
            e.operand = self.lvalue(e.operand, env)
            self._kill(env, {base_name(e.operand)})
            return e
        if isinstance(e, ast.Binary):
            return self._binary(e, env)
        if isinstance(e, ast.Assign):
            return self._assign(e, env)
        if isinstance(e, ast.Call):
            e.args = [self.expr(a, env) for a in e.args]
            return e
        if isinstance(e, ast.Index):
            e.array = self.expr(e.array, env)
            e.index = self.expr(e.index, env)
            return e
        if isinstance(e, ast.AddressOf):
            e.operand = self.lvalue(e.operand, env)
            return e
        if isinstance(e, ast.Deref):
            e.operand = self.expr(e.operand, env)
            return e
        if isinstance(e, ast.Ternary):
            e.condition = self.expr(e.condition, env)
            true_env = dict(env)
            false_env = dict(env)
            e.when_true = self.expr(e.when_true, true_env)
            e.when_false = self.expr(e.when_false, false_env)
            self._kill(env, assigned_vars(e.when_true) | assigned_vars(e.when_false))
            lv = literal_value(e.condition)
            if lv is not None:
                chosen = e.when_true if lv[1] else e.when_false
                self.stats.record("constant_folding", e.line,
                                  "{} -> ternary branch".format("?:"))
                return chosen
            return e
        return e

    def _propagate(self, ident, env):
        if ident.namespace is None:
            entry = env.get(ident.name)
            if entry is not None and entry.value is not None:
                lit = ast.Literal(entry.value.kind, entry.value.value, ident.line, ident.col)
                self.stats.record("constant_propagation", ident.line,
                                  "{} -> {}".format(ident.name, lit.value))
                return lit
        return ident

    def _assign(self, e, env):
        e.target = self.lvalue(e.target, env)
        e.value = self.expr(e.value, env)
        if isinstance(e.target, ast.Identifier):
            entry = env.get(e.target.name)
            if entry is not None:
                new = None
                if isinstance(e.value, ast.Literal):
                    if e.op == "=":
                        new = convert(e.value, entry.type)
                    elif entry.value is not None:
                        a, b = literal_value(entry.value), literal_value(e.value)
                        if a and b:
                            r = compute(e.op[:-1], a, b)
                            if r:
                                new = convert(make_literal(r[0], r[1], e), entry.type)
                env[e.target.name] = Entry(entry.type, new)
        return e

    # ----- unary -----
    def _simplify_unary(self, e):
        lv = literal_value(e.operand)
        before = None
        result = None
        if lv is not None:
            kind, v = lv
            if e.op == "-":
                r = ("float", -v) if kind == "float" else ("int", -int(v))
                if r[0] == "int" and not (INT_MIN <= r[1] <= INT_MAX):
                    r = None
                result = r
            elif e.op == "+":
                result = ("float", v) if kind == "float" else ("int", int(v))
            elif e.op == "!":
                result = ("bool", not v)
            elif e.op == "~":
                if kind == "int":
                    r = ~int(v)
                    if INT_MIN <= r <= INT_MAX:
                        result = ("int", r)
            if result is not None:
                before = unparse(e)
                node = make_literal(result[0], result[1], e)
                self.stats.record("constant_folding", e.line, "{} -> {}".format(before, node.value))
                return node
        if e.op == "-" and isinstance(e.operand, ast.Unary) and e.operand.op == "-":
            if self.type_of(e.operand.operand) in NUMERIC:
                before = unparse(e)
                self.stats.record("algebraic", e.line, "{} -> {}".format(before, unparse(e.operand.operand)))
                return e.operand.operand
        return e

    # ----- binary -----
    def _binary(self, e, env):
        if e.op in ("&&", "||"):
            e.left = self.expr(e.left, env)
            right_env = dict(env)
            e.right = self.expr(e.right, right_env)
            self._kill(env, assigned_vars(e.right))     # right side runs only sometimes
            return self._simplify_logic(e)
        if e.op == ">>":
            e.left = self.expr(e.left, env)
            e.right = self.lvalue(e.right, env)         # cin >> x writes x
            self._kill(env, {m.name for m in walk(e.right) if isinstance(m, ast.Identifier)})
            return self._fold_binary(e)
        e.left = self.expr(e.left, env)
        e.right = self.expr(e.right, env)
        return self._fold_binary(e)

    def _fold_binary(self, e):
        a, b = literal_value(e.left), literal_value(e.right)
        if a is not None and b is not None:
            r = compute(e.op, a, b)
            if r is not None:
                before = unparse(e)
                node = make_literal(r[0], r[1], e)
                self.stats.record("constant_folding", e.line, "{} -> {}".format(before, node.value))
                return node
            return e
        return self._algebra(e)

    def _simplify_logic(self, e):
        lt = literal_value(e.left)
        rt = literal_value(e.right)
        if lt is not None and rt is not None:
            return self._fold_binary(e)
        before = unparse(e)

        def done(node):
            self.stats.record("algebraic", e.line, "{} -> {}".format(before, unparse(node)))
            return node

        if e.op == "&&":
            if lt is not None:
                if not lt[1]:
                    return done(make_literal("bool", False, e))       # false && x
                if self.type_of(e.right) == "bool":
                    return done(e.right)                              # true && x
            elif rt is not None:
                if rt[1] and self.type_of(e.left) == "bool":
                    return done(e.left)                               # x && true
                if not rt[1] and is_pure(e.left):
                    return done(make_literal("bool", False, e))       # x && false
        else:
            if lt is not None:
                if lt[1]:
                    return done(make_literal("bool", True, e))        # true || x
                if self.type_of(e.right) == "bool":
                    return done(e.right)                              # false || x
            elif rt is not None:
                if not rt[1] and self.type_of(e.left) == "bool":
                    return done(e.left)                               # x || false
                if rt[1] and is_pure(e.left):
                    return done(make_literal("bool", True, e))        # x || true
        return e

    # ----- algebra and strength reduction -----
    def _identity_ok(self, other, lit):
        """x op lit keeps the type of x (so replacing it by x is safe)."""
        t = self.type_of(other)
        if lit.kind == "int":
            return t in NUMERIC
        return t == "double"

    def _algebra(self, e):
        op, L, R = e.op, e.left, e.right
        before = unparse(e)

        def alg(node):
            self.stats.record("algebraic", e.line, "{} -> {}".format(before, unparse(node)))
            return node

        def strength(node):
            self.stats.record("strength_reduction", e.line, "{} -> {}".format(before, unparse(node)))
            return node

        def num(node, v):
            return isinstance(node, ast.Literal) and is_number(node, v)

        if op == "+":
            if num(R, 0) and self._identity_ok(L, R):
                return alg(L)
            if num(L, 0) and self._identity_ok(R, L):
                return alg(R)
        elif op == "-":
            if num(R, 0) and self._identity_ok(L, R):
                return alg(L)
            if (isinstance(L, ast.Identifier) and isinstance(R, ast.Identifier)
                    and L.name == R.name and L.namespace == R.namespace
                    and self.type_of(L) == "int"):
                return alg(make_literal("int", 0, e))
        elif op == "*":
            for lit, other in ((R, L), (L, R)):
                if not isinstance(lit, ast.Literal):
                    continue
                if num(lit, 1) and self._identity_ok(other, lit):
                    return alg(other)
                if num(lit, 0) and lit.kind == "int" and self.type_of(other) == "int" and is_pure(other):
                    return alg(make_literal("int", 0, e))
                lv = literal_value(lit)
                if lv and lv[0] == "int" and lv[1] >= 2 and (lv[1] & (lv[1] - 1)) == 0 \
                        and isinstance(other, ast.Identifier):
                    k = lv[1].bit_length() - 1
                    t = self.type_of(other)
                    if k == 1 and t in NUMERIC:
                        return strength(ast.Binary("+", other, ast.Identifier(other.name, other.namespace,
                                                                             other.line, other.col),
                                                   e.line, e.col))
                    if t == "int":
                        return strength(ast.Binary("<<", other, make_literal("int", k, e), e.line, e.col))
        elif op == "/":
            if num(R, 1) and self._identity_ok(L, R):
                return alg(L)
        return e