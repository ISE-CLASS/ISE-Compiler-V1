"""
AST pretty-printer: renders a tree like

    Program
    └── FunctionDecl return_type='int' name='main'
        └── body: Block
            └── Return
                └── value: Literal kind='int' value='0'

Works for any node in ast_nodes.py: simple fields (str, bool, int) are shown
on the node's line, child nodes and lists of nodes are drawn below it.
"""
from dataclasses import fields

from .ast_nodes import Node

POSITION_FIELDS = ("line", "col")
FLAT_LISTS = ("items", "statements", "elements", "cases", "body")

# Box-drawing characters and their plain-ASCII replacements, for consoles
# that cannot print Unicode (e.g. the default Windows cp1252 console).
ASCII_TREE = str.maketrans({"└": "`", "├": "|", "│": "|", "─": "-"})


def format_ast(node, show_positions=False, ascii_only=False):
    lines = []
    _format(node, "", "", "", lines, show_positions)
    text = "\n".join(lines)
    return text.translate(ASCII_TREE) if ascii_only else text


def print_ast(node, show_positions=False, ascii_only=False):
    print(format_ast(node, show_positions, ascii_only))


def _label(node, show_positions):
    parts = [type(node).__name__]
    for f in fields(node):
        value = getattr(node, f.name)
        if f.name in POSITION_FIELDS or f.name in ("mangled_name",):
            continue
        if isinstance(value, (Node, list)) or value is None:
            continue
        if value is False:
            continue
        parts.append("{}={!r}".format(f.name, value))
    if show_positions:
        parts.append("@{}:{}".format(node.line, node.col))
    return " ".join(parts)


def _children(node):
    """(name, value) pairs for child nodes and non-empty lists of nodes."""
    result = []
    for f in fields(node):
        value = getattr(node, f.name)
        if isinstance(value, Node) or (isinstance(value, list) and value):
            result.append((f.name, value))
    return result


def _format(node, name, prefix, child_prefix, lines, show_positions):
    head = name + ": " if name else ""
    lines.append(prefix + head + _label(node, show_positions))
    children = _children(node)
    # Container lists (Program.items, Block.statements, InitList.elements, Switch.cases,
    # Case/Default.body lists) show their items directly, without an extra level.
    if len(children) == 1 and children[0][0] in FLAT_LISTS and isinstance(children[0][1], list):
        entries = [("", item) for item in children[0][1]]
    else:
        entries = children
    for i, (child_name, value) in enumerate(entries):
        last = i == len(entries) - 1
        branch = "└── " if last else "├── "
        extend = "    " if last else "│   "
        if isinstance(value, list):
            lines.append(child_prefix + branch + child_name)
            for j, item in enumerate(value):
                item_last = j == len(value) - 1
                _format(item, "", child_prefix + extend + ("└── " if item_last else "├── "),
                        child_prefix + extend + ("    " if item_last else "│   "),
                        lines, show_positions)
        else:
            _format(value, child_name, child_prefix + branch, child_prefix + extend,
                    lines, show_positions)
