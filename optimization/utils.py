"""
Shared helpers for the optimization phase (Group 3).

Contents:
    - generic AST walking / cloning
    - numeric literal parsing and formatting (C++ int / double rules)
    - purity check (does an expression have side effects?)
    - assigned_vars(): which variables can a piece of code change?
"""
import copy
from dataclasses import fields

from syntax_analysis import ast_nodes as ast

INT_MIN = -(2 ** 31)
INT_MAX = 2 ** 31 - 1


# ---------------------------------------------------------------
# Generic tree helpers
# ---------------------------------------------------------------
def children(node):
    """Yield every direct child node of `node` (in field order)."""
    for f in fields(node):
        value = getattr(node, f.name)
        if isinstance(value, ast.Node):
            yield value
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, ast.Node):
                    yield item


def walk(node):
    """Yield `node` and every node below it (depth first)."""
    yield node
    for child in children(node):
        yield from walk(child)


def clone(node):
    """Deep copy of a node (the optimizer never mutates the input AST)."""
    return copy.deepcopy(node)


def node_count(node):
    return sum(1 for _ in walk(node))


def same_tree(a, b):
    """Structural equality of two nodes, ignoring line/col."""
    if type(a) is not type(b):
        return False
    for f in fields(a):
        if f.name in ("line", "col"):
            continue
        va, vb = getattr(a, f.name), getattr(b, f.name)
        if isinstance(va, ast.Node):
            if not same_tree(va, vb):
                return False
        elif isinstance(va, list):
            if len(va) != len(vb):
                return False
            for x, y in zip(va, vb):
                if isinstance(x, ast.Node):
                    if not same_tree(x, y):
                        return False
                elif x != y:
                    return False
        elif va != vb:
            return False
    return True


# ---------------------------------------------------------------
# Numeric literals
# ---------------------------------------------------------------
def parse_int_text(text):
    """Value of a C++ int literal, or None if we should not touch it
    (has a u/l/z suffix, does not fit in a 32-bit int, malformed)."""
    t = text.replace("'", "")
    neg = False
    if t.startswith("-"):
        neg, t = True, t[1:]
    low = t.lower()
    try:
        if low.startswith("0x"):
            v = int(low[2:], 16)
        elif low.startswith("0b"):
            v = int(low[2:], 2)
        elif len(low) > 1 and low.startswith("0") and low.isdigit():
            v = int(low, 8)
        else:
            v = int(low, 10)
    except ValueError:
        return None
    v = -v if neg else v
    return v if INT_MIN <= v <= INT_MAX else None


def parse_float_text(text):
    """Value of a C++ floating literal, or None (f/l suffix, hex float...)."""
    t = text.replace("'", "")
    if t and t[-1] in "fFlL":
        return None            # 'float' precision differs from double: leave it
    if t.lower().startswith(("0x", "-0x")):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def literal_value(lit):
    """(kind, python value) for a foldable literal, else None.
    kind is 'int', 'float' or 'bool'. Strings and chars are never folded."""
    if not isinstance(lit, ast.Literal):
        return None
    if lit.kind == "bool":
        return ("bool", lit.value == "true")
    if lit.kind == "int":
        v = parse_int_text(lit.value)
        return None if v is None else ("int", v)
    if lit.kind == "float":
        v = parse_float_text(lit.value)
        return None if v is None else ("float", v)
    return None


def format_float(v):
    text = repr(float(v))
    if "e" not in text and "." not in text:
        text += ".0"
    return text


def make_literal(kind, value, like):
    """Build a Literal node at the position of node `like`."""
    if kind == "bool":
        text = "true" if value else "false"
    elif kind == "int":
        text = str(value)
    else:
        text = format_float(value)
    return ast.Literal(kind, text, like.line, like.col)


def is_const(node):
    return literal_value(node) is not None


def is_number(node, number):
    """True if node is a numeric literal equal to `number` (int or float)."""
    lv = literal_value(node)
    return lv is not None and lv[0] in ("int", "float") and lv[1] == number


def truthiness(node):
    lv = literal_value(node)
    return None if lv is None else bool(lv[1])


# ---------------------------------------------------------------
# Purity and writes
# ---------------------------------------------------------------
def is_pure(expr):
    """True if evaluating `expr` has no side effects (no call, no assignment,
    no ++/--, no stream << >> which would print or read)."""
    if isinstance(expr, (ast.Literal, ast.Identifier)):
        return True
    if isinstance(expr, ast.Unary):
        return expr.op in ("-", "+", "!", "~") and is_pure(expr.operand)
    if isinstance(expr, ast.AddressOf):
        return is_pure(expr.operand)
    if isinstance(expr, ast.Deref):
        return is_pure(expr.operand)
    if isinstance(expr, ast.Binary):
        if expr.op in ("<<", ">>"):
            return False
        return is_pure(expr.left) and is_pure(expr.right)
    if isinstance(expr, ast.Index):
        return is_pure(expr.array) and is_pure(expr.index)
    if isinstance(expr, ast.Ternary):
        return is_pure(expr.condition) and is_pure(expr.when_true) and is_pure(expr.when_false)
    return False


def base_name(target):
    """Name of the variable written through an assignment target."""
    while isinstance(target, (ast.Index, ast.Deref)):
        target = target.array if isinstance(target, ast.Index) else target.operand
    return target.name if isinstance(target, ast.Identifier) else None


def assigned_vars(node):
    """Names that executing `node` may change: assignment targets, ++/--,
    declarations (they shadow), and anything on the right of `>>` (cin >> x)."""
    names = set()
    for n in walk(node):
        if isinstance(n, ast.Assign):
            b = base_name(n.target)
            if b:
                names.add(b)
        elif isinstance(n, (ast.Unary, ast.Postfix)) and n.op in ("++", "--"):
            b = base_name(n.operand)
            if b:
                names.add(b)
        elif isinstance(n, ast.Declarator):
            names.add(n.name)
        elif isinstance(n, ast.Binary) and n.op == ">>":
            for m in walk(n.right):
                if isinstance(m, ast.Identifier):
                    names.add(m.name)
    return names


def contains(node, *types):
    return any(isinstance(n, types) for n in walk(node))