"""
AST node definitions: the interface between syntax analysis and semantic analysis.

Every node is a dataclass with `line` and `col` fields (1-based) pointing at
the token that best identifies it:
    - statements and declarations: their first token
    - Binary / Assign: the operator token
    - Call / Index / Postfix: the '(' / '[' / operator token

Type names are plain strings: "int", "float", "double", "char", "bool",
"void", "string" ("std::string" is normalized to "string").

Literal values are kept as the exact source text (e.g. "0x1F", "1'000",
"'\\n'", "\"hi\""), so the semantic group decides how to convert them.
"""
from dataclasses import dataclass, field
from typing import List, Optional


class Node:
    """Base class for all AST nodes (used for isinstance checks)."""


class Expr(Node):
    """Base class for expression nodes."""


class Stmt(Node):
    """Base class for statement nodes."""


# ---------------------------------------------------------------
# Program and top-level items
# ---------------------------------------------------------------
@dataclass
class Program(Node):
    items: List[Node] = field(default_factory=list)   # Preprocessor, UsingDirective, FunctionDecl, VarDecl
    line: int = 1
    col: int = 1


@dataclass
class Preprocessor(Stmt):
    text: str                     # whole directive, e.g. "#include <iostream>"
    line: int = 0
    col: int = 0


@dataclass
class UsingDirective(Stmt):
    namespace: str                # always "std" in this subset
    line: int = 0
    col: int = 0


@dataclass
class Param(Node):
    type_name: str
    name: Optional[str]           # None when unnamed, e.g. "int f(int);"
    is_array: bool = False        # int arr[] or int arr[10]
    is_const: bool = False
    pointer_depth: int = 0
    is_reference: bool = False
    line: int = 0
    col: int = 0


@dataclass
class FunctionDecl(Node):
    return_type: str
    name: str
    params: List[Param] = field(default_factory=list)
    body: Optional["Block"] = None   # None for a declaration (prototype) like "int f(int);"
    mangled_name: Optional[str] = None
    line: int = 0
    col: int = 0


# ---------------------------------------------------------------
# Declarations
# ---------------------------------------------------------------
@dataclass
class Declarator(Node):
    name: str
    is_array: bool = False
    array_size: Optional[Expr] = None   # None for "int a[] = {...}" or non-arrays
    init: Optional[Node] = None         # Expr or InitList
    is_const: bool = False
    pointer_depth: int = 0
    line: int = 0
    col: int = 0


@dataclass
class VarDecl(Stmt):
    type_name: str
    declarators: List[Declarator] = field(default_factory=list)   # int a = 1, b;  -> 2 declarators
    is_const: bool = False
    line: int = 0
    col: int = 0


@dataclass
class InitList(Node):
    elements: List[Expr] = field(default_factory=list)            # {1, 2, 3}
    line: int = 0
    col: int = 0


# ---------------------------------------------------------------
# Statements
# ---------------------------------------------------------------
@dataclass
class Block(Stmt):
    statements: List[Node] = field(default_factory=list)
    line: int = 0
    col: int = 0


@dataclass
class ExprStmt(Stmt):
    expr: Expr
    line: int = 0
    col: int = 0


@dataclass
class EmptyStmt(Stmt):
    line: int = 0
    col: int = 0


@dataclass
class If(Stmt):
    condition: Expr
    then_branch: Stmt
    else_branch: Optional[Stmt] = None
    line: int = 0
    col: int = 0


@dataclass
class While(Stmt):
    condition: Expr
    body: Stmt
    line: int = 0
    col: int = 0


@dataclass
class DoWhile(Stmt):
    body: Stmt
    condition: Expr
    line: int = 0
    col: int = 0


@dataclass
class For(Stmt):
    init: Optional[Node] = None         # VarDecl, Expr, or None
    condition: Optional[Expr] = None
    update: Optional[Expr] = None
    body: Optional[Stmt] = None
    line: int = 0
    col: int = 0


@dataclass
class Return(Stmt):
    value: Optional[Expr] = None
    line: int = 0
    col: int = 0


@dataclass
class Break(Stmt):
    line: int = 0
    col: int = 0


@dataclass
class Continue(Stmt):
    line: int = 0
    col: int = 0


@dataclass
class Case(Stmt):
    value: Expr
    body: List[Stmt] = field(default_factory=list)
    line: int = 0
    col: int = 0


@dataclass
class Default(Stmt):
    body: List[Stmt] = field(default_factory=list)
    line: int = 0
    col: int = 0


@dataclass
class Switch(Stmt):
    expr: Expr
    cases: List[Node] = field(default_factory=list)   # Case / Default in source order
    line: int = 0
    col: int = 0


# ---------------------------------------------------------------
# Expressions
# ---------------------------------------------------------------
@dataclass
class Assign(Expr):
    op: str                       # "=", "+=", "-=", "*=", "/=", "%="
    target: Expr                  # Identifier or Index
    value: Expr
    line: int = 0
    col: int = 0


@dataclass
class Binary(Expr):
    op: str                       # "||" "&&" "==" "!=" "<" ">" "<=" ">=" "<<" ">>" "+" "-" "*" "/" "%"
    left: Expr
    right: Expr
    line: int = 0
    col: int = 0


@dataclass
class Ternary(Expr):
    condition: Expr
    when_true: Expr
    when_false: Expr
    line: int = 0
    col: int = 0


@dataclass
class Unary(Expr):
    op: str                       # prefix: "-", "+", "!", "~", "++", "--"
    operand: Expr
    line: int = 0
    col: int = 0


@dataclass
class AddressOf(Expr):
    operand: Expr
    line: int = 0
    col: int = 0


@dataclass
class Deref(Expr):
    operand: Expr
    line: int = 0
    col: int = 0


@dataclass
class Postfix(Expr):
    op: str                       # "++" or "--"
    operand: Expr
    line: int = 0
    col: int = 0


@dataclass
class Call(Expr):
    callee: Expr                  # usually an Identifier
    args: List[Expr] = field(default_factory=list)
    mangled_name: Optional[str] = None
    line: int = 0
    col: int = 0


@dataclass
class Index(Expr):
    array: Expr
    index: Expr
    line: int = 0
    col: int = 0


@dataclass
class Identifier(Expr):
    name: str                     # "x", "cout"
    namespace: Optional[str] = None   # "std" for std::cout, otherwise None
    line: int = 0
    col: int = 0


@dataclass
class Literal(Expr):
    kind: str                     # "int", "float", "string", "char", "bool"
    value: str                    # exact source text
    line: int = 0
    col: int = 0
