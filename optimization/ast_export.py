"""
AST exports (Group 3 - Abstract Syntax Tree):

    to_dict(node)   nested dict, one entry per node field
    to_json(node)   the same as JSON text
    to_dot(node)    Graphviz DOT text; render with:  dot -Tpng tree.dot -o tree.png
    ast_stats(node) node counts per type, total nodes, tree depth
"""
import json
from collections import Counter
from dataclasses import fields

from syntax_analysis import ast_nodes as ast

from .utils import children


def to_dict(node):
    d = {"node": type(node).__name__}
    for f in fields(node):
        value = getattr(node, f.name)
        if isinstance(value, ast.Node):
            d[f.name] = to_dict(value)
        elif isinstance(value, list):
            d[f.name] = [to_dict(v) if isinstance(v, ast.Node) else v for v in value]
        elif value is not None and value is not False:
            d[f.name] = value
    return d


def to_json(node, indent=2):
    return json.dumps(to_dict(node), indent=indent)


def _escape(text):
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _label(node):
    parts = [type(node).__name__]
    for f in fields(node):
        value = getattr(node, f.name)
        if f.name in ("line", "col") or isinstance(value, (ast.Node, list)) or value in (None, False):
            continue
        parts.append("{}={}".format(f.name, value))
    return "\\n".join(_escape(p) for p in parts)


def to_dot(node, title="AST"):
    lines = ['digraph AST {', '  label="{}";'.format(_escape(title)),
             '  node [shape=box, fontname="Consolas", fontsize=10];']
    counter = [0]

    def visit(n, parent=None, edge=""):
        my_id = "n{}".format(counter[0])
        counter[0] += 1
        shape = "ellipse" if isinstance(n, ast.Expr) else "box"
        lines.append('  {} [label="{}", shape={}];'.format(my_id, _label(n), shape))
        if parent is not None:
            lines.append('  {} -> {} [label="{}", fontsize=8];'.format(parent, my_id, edge))
        for f in fields(n):
            value = getattr(n, f.name)
            if isinstance(value, ast.Node):
                visit(value, my_id, f.name)
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, ast.Node):
                        visit(item, my_id, "{}[{}]".format(f.name, i))

    visit(node)
    lines.append("}")
    return "\n".join(lines) + "\n"


def depth(node):
    kids = list(children(node))
    return 1 + (max(depth(k) for k in kids) if kids else 0)


def ast_stats(node):
    counts = Counter()

    def visit(n):
        counts[type(n).__name__] += 1
        for k in children(n):
            visit(k)

    visit(node)
    return {"total_nodes": sum(counts.values()), "depth": depth(node), "by_type": dict(counts)}