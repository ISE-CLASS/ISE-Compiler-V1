"""Constant evaluation for the supported C++ subset.

Used for array sizes, global initialisers and for keeping the optimiser and
code generator consistent.  Only side-effect-free literal arithmetic is
evaluated; anything else returns ``None``.
"""
import math
import re

from syntax_analysis import ast_nodes as ast

_INT_SUFFIX = re.compile(r"(?i)(u|l|ll|z|ul|ull|lu|llu)+$")


def parse_int_literal(text):
    """Parse a C++ integer literal into a Python int, or return None."""
    if not isinstance(text, str):
        return None
    value = text.replace("'", "").strip()
    value = _INT_SUFFIX.sub("", value)
    try:
        if value.lower().startswith("0x"):
            return int(value, 16)
        if value.lower().startswith("0b"):
            return int(value, 2)
        if value.lower().startswith("0o"):
            return int(value, 8)
        if len(value) > 1 and value.startswith("0") and value.isdigit():
            return int(value, 8)
        return int(value, 10)
    except ValueError:
        return None


def parse_char_literal(text):
    """Return the integer value of a character literal, or None."""
    if not isinstance(text, str) or len(text) < 3:
        return None
    body = text
    if body[0] in "LuU" and len(body) > 1 and body[1] == "'":
        body = body[1:]
    if body.startswith("u8'"):
        body = body[2:]
    if not body.startswith("'") or not body.endswith("'"):
        return None
    inner = body[1:-1]
    if not inner:
        return None
    if inner.startswith("\\"):
        escapes = {
            "\\n": 10, "\\t": 9, "\\r": 13, "\\0": 0, "\\a": 7,
            "\\b": 8, "\\f": 12, "\\v": 11, "\\\\": 92, "\\'": 39,
            '\\"': 34,
        }
        if inner in escapes:
            return escapes[inner]
        if inner.startswith("\\x"):
            try:
                return int(inner[2:], 16)
            except ValueError:
                return None
        if inner.startswith("\\") and inner[1:].isdigit():
            try:
                return int(inner[1:], 8)
            except ValueError:
                return None
        return ord(inner[1]) if len(inner) > 1 else None
    return ord(inner[0])


def parse_float_literal(text):
    """Parse a C++ floating literal into a Python float, or None."""
    if not isinstance(text, str):
        return None
    value = text.replace("'", "").strip().lower()
    if value.endswith(("f", "l")):
        value = value[:-1]
    try:
        if value.startswith("0x"):
            return float.fromhex(value)
        return float(value)
    except ValueError:
        return None


def parse_string_literal(text):
    """Decode a C++ string literal into Python text (without quotes)."""
    if not isinstance(text, str):
        return None
    body = text
    for prefix in ("u8", "u", "U", "L"):
        if body.startswith(prefix + '"'):
            body = body[len(prefix):]
            break
    if not body.startswith('"') or not body.endswith('"'):
        return None
    inner = body[1:-1]
    out = []
    i = 0
    escapes = {
        "n": "\n", "t": "\t", "r": "\r", "0": "\0", "a": "\a",
        "b": "\b", "f": "\f", "v": "\v", "\\": "\\", "'": "'",
        '"': '"', "?": "?",
    }
    while i < len(inner):
        ch = inner[i]
        if ch != "\\" or i + 1 >= len(inner):
            out.append(ch)
            i += 1
            continue
        nxt = inner[i + 1]
        if nxt in escapes:
            out.append(escapes[nxt])
            i += 2
        elif nxt == "x":
            j = i + 2
            while j < len(inner) and inner[j] in "0123456789abcdefABCDEF":
                j += 1
            try:
                out.append(chr(int(inner[i + 2:j], 16)))
            except ValueError:
                out.append("?")
            i = j
        elif nxt.isdigit():
            j = i + 1
            while j < len(inner) and inner[j].isdigit() and j < i + 4:
                j += 1
            try:
                out.append(chr(int(inner[i + 1:j], 8)))
            except ValueError:
                out.append("?")
            i = j
        else:
            out.append(nxt)
            i += 2
    return "".join(out)


def const_value(node):
    """Return ``(type_name, python_value)`` for a constant expression."""
    if isinstance(node, ast.Literal):
        if node.kind == "int":
            vo = parse_int_literal(node.value)
            return ("int", vo) if vo is not None else (None, None)
        if node.kind == "bool":
            return ("bool", node.value == "true")
        if node.kind == "char":
            vo = parse_char_literal(node.value)
            return ("char", vo) if vo is not None else (None, None)
        if node.kind == "float":
            vo = parse_float_literal(node.value)
            return ("float", vo) if vo is not None else (None, None)
        return (None, None)
    if isinstance(node, ast.Unary):
        if node.op in ("-", "+"):
            typ, value = const_value(node.operand)
            if typ in ("int", "float", "char", "bool"):
                if node.op == "-":
                    return (typ, -value)
                return (typ, +value)
            return (None, None)
        if node.op == "!":
            typ, value = const_value(node.operand)
            if typ is None:
                return (None, None)
            return ("bool", not bool(value))
        if node.op == "~":
            typ, value = const_value(node.operand)
            if typ in ("int", "char", "bool"):
                return ("int", ~int(value))
            return (None, None)
        return (None, None)
    if isinstance(node, ast.Binary):
        lt, lv = const_value(node.left)
        rt, rv = const_value(node.right)
        if lt is None or rt is None:
            return (None, None)
        if lt == "float" or rt == "float":
            if node.op in ("+", "-", "*", "/"):
                try:
                    result = {
                        "+": lv + rv, "-": lv - rv,
                        "*": lv * rv, "/": lv / rv,
                    }[node.op]
                except (ZeroDivisionError, OverflowError):
                    return (None, None)
                return ("float", result)
            return (None, None)
        if node.op in ("+", "-", "*", "/", "%", "<<", ">>", "&", "|", "^"):
            try:
                if node.op == "+":
                    return ("int", int(lv) + int(rv))
                if node.op == "-":
                    return ("int", int(lv) - int(rv))
                if node.op == "*":
                    return ("int", int(lv) * int(rv))
                if node.op == "/":
                    if int(rv) == 0:
                        return (None, None)
                    return ("int", int(math.trunc(int(lv) / int(rv))))
                if node.op == "%":
                    if int(rv) == 0:
                        return (None, None)
                    return ("int", int(lv) - int(math.trunc(int(lv) / int(rv))) * int(rv))
                if node.op == "<<":
                    if int(lv) < 0 or not (0 <= int(rv) < 64):
                        return (None, None)
                    return ("int", int(lv) << int(rv))
                if node.op == ">>":
                    if int(lv) < 0 or not (0 <= int(rv) < 64):
                        return (None, None)
                    return ("int", int(lv) >> int(rv))
                if node.op == "&":
                    return ("int", int(lv) & int(rv))
                if node.op == "|":
                    return ("int", int(lv) | int(rv))
                if node.op == "^":
                    return ("int", int(lv) ^ int(rv))
            except (ValueError, OverflowError):
                return (None, None)
        if node.op in ("<", ">", "<=", ">=", "==", "!="):
            ops = {
                "<": lv < rv, ">": lv > rv, "<=": lv <= rv,
                ">=": lv >= rv, "==": lv == rv, "!=": lv != rv,
            }
            return ("bool", ops[node.op])
        if node.op in ("&&", "||"):
            return ("bool", (bool(lv) and bool(rv)) if node.op == "&&" else (bool(lv) or bool(rv)))
    if isinstance(node, ast.Ternary):
        ct, cv = const_value(node.condition)
        if ct is None:
            return (None, None)
        branch = node.when_true if bool(cv) else node.when_false
        return const_value(branch)
    return (None, None)


def const_int(node):
    typ, value = const_value(node)
    if typ in ("int", "char", "bool"):
        return int(value)
    return None
