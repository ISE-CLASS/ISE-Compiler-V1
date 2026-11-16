"""Semantic analysis for the supported C++ subset.

Responsibilities:
  * build scoped symbol tables (globals, functions, parameters, blocks)
  * resolve identifiers and calls
  * check declaration, assignment, operator and return types
  * check ``break``/``continue`` placement and ``main`` signature
  * annotate every expression with ``_type`` and every identifier/declarator
    with ``_symbol`` so IR and code generation do not have to re-resolve names

Only run this phase when parsing succeeded; a partial AST is not safe to use.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from syntax_analysis import ast_nodes as ast
from . import constexpr
from .diagnostics import Diagnostic, error, warning

NUMERIC = {"int", "float", "double", "char", "bool"}
SCALAR = NUMERIC
PRINTABLE = NUMERIC | {"string"}
INPUTTABLE = NUMERIC | {"string"}
BUILTINS = {"cout": "ostream", "cin": "istream", "endl": "endl"}


def strip_top_ref(type_name):
    if not type_name:
        return type_name
    if type_name.endswith("&"):
        return type_name[:-1]
    return type_name


def is_reference_type(type_name):
    return bool(type_name) and type_name.endswith("&")


def pointer_depth_of(type_name):
    t = strip_top_ref(type_name or "")
    # arrays are not pointer-depth types here
    if "[" in t:
        return 0
    depth = 0
    while t.endswith("*"):
        depth += 1
        t = t[:-1]
    return depth


def pointee_type(type_name):
    t = strip_top_ref(type_name or "")
    if t.endswith("*"):
        return t[:-1]
    return None


def base_type(type_name):
    if not type_name:
        return type_name
    t = strip_top_ref(type_name)
    if "[" in t:
        return t.split("[", 1)[0].replace("*", "")
    return t.replace("*", "")


def is_array_type(type_name):
    t = strip_top_ref(type_name or "")
    return bool(t) and "[" in t and not t.rstrip().endswith("*")


def make_array_type(element, size=None):
    return "{}[{}]".format(element, "" if size is None else size)


def make_pointer_type(pointee, depth=1):
    return pointee + ("*" * depth)


def element_or_pointee_base(type_name):
    t = strip_top_ref(type_name or "")
    if is_array_type(t):
        return t[:t.find("[")]
    if t.endswith("*"):
        return t[:-1]
    return t


def _array_size_from_type(type_name):
    t = strip_top_ref(type_name or "")
    if "[" not in t:
        return None
    body = t[t.find("[") + 1:t.rfind("]")]
    if body == "":
        return None
    try:
        return int(body)
    except ValueError:
        return None


def is_pointer_type(type_name):
    t = strip_top_ref(type_name or "")
    return "*" in t and not is_array_type(t)


def is_integral_type(type_name):
    return base_type(strip_top_ref(type_name)) in ("int", "char", "bool")


def decay_param_type(type_name):
    # Arrays decay to element pointers in parameter signatures/calls (subset behavior).
    t = strip_top_ref(type_name or "")
    if is_array_type(t):
        elem = t[:t.find("[")]
        return elem + "*"
    return strip_top_ref(t)


def type_size_bytes(type_name):
    t = strip_top_ref(type_name or "")
    if is_pointer_type(t) or t == "string":
        return 8
    b = base_type(t)
    if b in ("char", "bool"):
        return 1
    if b in ("int", "float", "double"):
        return 8
    return 8


def mangle_type(type_name):
    t = decay_param_type(type_name)
    if not t:
        return "v"
    if t.endswith("*"):
        inner = mangle_type(t[:-1])
        return "P" + inner
    if t in ("int", "float", "double", "char", "bool", "void", "string"):
        return {"int": "i", "float": "f", "double": "d", "char": "c", "bool": "b", "void": "v", "string": "s"}[t]
    # fallback
    return t.replace("[", "A").replace("]", "").replace("*", "P").replace("&", "R")


def compatible(dst, src):
    """Can a value of type ``src`` be used where ``dst`` is expected?"""
    dst0 = strip_top_ref(dst)
    src0 = strip_top_ref(src)
    if dst0 == src0:
        return True
    if not dst0 or not src0:
        return False
    # pointer compatibility (simple subset): same pointer type, or array-to-pointer decay
    if is_pointer_type(dst0) or is_pointer_type(src0):
        if is_array_type(src0) and is_pointer_type(dst0):
            return dst0 == decay_param_type(src0)
        if dst0 == src0:
            return True
        return False
    if is_array_type(dst0) or is_array_type(src0):
        # allow array decay source to pointer destination
        if is_array_type(src0) and is_pointer_type(dst0):
            return dst0 == decay_param_type(src0)
        if not (is_array_type(dst0) and is_array_type(src0)):
            return False
        if base_type(dst0) != base_type(src0):
            return False
        dst_size = _array_size_from_type(dst0)
        src_size = _array_size_from_type(src0)
        if dst_size is None or src_size is None:
            return True
        return src_size <= dst_size
    if base_type(dst0) in NUMERIC and base_type(src0) in NUMERIC:
        return True
    return False


@dataclass
class Symbol:
    name: str
    kind: str                 # variable, parameter, function
    type_name: str
    is_array: bool = False
    array_size: Optional[int] = None
    is_const: bool = False
    pointer_depth: int = 0
    is_reference: bool = False
    line: int = 0
    col: int = 0
    uid: int = 0
    scope_id: int = 0
    is_global: bool = False

    @property
    def declared_type(self):
        t = self.type_name + ("*" * getattr(self, "pointer_depth", 0))
        if self.is_array:
            t = make_array_type(t, self.array_size)
        if getattr(self, "is_reference", False):
            t += "&"
        return t

    @property
    def full_type(self):
        return self.declared_type

    @property
    def value_type(self):
        if self.is_reference:
            return self.type_name + ("*" * getattr(self, "pointer_depth", 0))
        return self.declared_type

    def to_dict(self):
        return {
            "name": self.name,
            "kind": self.kind,
            "type": self.full_type,
            "is_array": self.is_array,
            "array_size": self.array_size,
            "line": self.line,
            "col": self.col,
            "uid": self.uid,
            "scope": self.scope_id,
        }


@dataclass
class ParamInfo:
    name: Optional[str]
    type_name: str
    is_array: bool = False
    is_const: bool = False
    pointer_depth: int = 0
    is_reference: bool = False
    line: int = 0
    col: int = 0

    @property
    def full_type(self):
        t = self.type_name + ("*" * getattr(self, "pointer_depth", 0))
        if self.is_array:
            t = make_array_type(t)
        if getattr(self, "is_reference", False):
            t += "&"
        return t

    @property
    def signature_type(self):
        return decay_param_type(self.full_type)

    def to_dict(self):
        return {
            "name": self.name,
            "type": self.full_type,
            "is_array": self.is_array,
            "line": self.line,
            "col": self.col,
        }


@dataclass
class FunctionInfo:
    name: str
    return_type: str
    params: List[ParamInfo] = field(default_factory=list)
    line: int = 0
    col: int = 0
    defined: bool = False
    node: object = None
    mangled_name: Optional[str] = None

    def signature(self):
        return (self.return_type, tuple(decay_param_type(p.full_type) for p in self.params))

    def param_signature(self):
        return tuple(decay_param_type(p.full_type) for p in self.params)

    def to_dict(self):
        return {
            "name": self.name,
            "return_type": self.return_type,
            "params": [p.to_dict() for p in self.params],
            "line": self.line,
            "col": self.col,
            "defined": self.defined,
            "mangled_name": self.mangled_name,
        }


@dataclass
class SemanticResult:
    errors: List[Diagnostic] = field(default_factory=list)
    warnings: List[Diagnostic] = field(default_factory=list)
    symbol_table: dict = field(default_factory=dict)
    functions: Dict[str, FunctionInfo] = field(default_factory=dict)
    globals: Dict[str, Symbol] = field(default_factory=dict)

    @property
    def ok(self):
        return not self.errors


class SemanticAnalyzer:
    def __init__(self):
        self.errors = []
        self.warnings = []
        self.scopes = []
        self.scope_records = []
        self.functions = {}        # mangled_name -> FunctionInfo
        self.functions_by_name = {}  # name -> [FunctionInfo]
        self.globals = {}
        self.using_std = False
        self.current_function = None
        self.loop_depth = 0
        self.switch_depth = 0
        self.return_seen = False
        self._uid = 0
        self._scope_uid = 0
        self._push_scope("global", "<global>")
        self.input_validation_used = False

    def _mangle_function(self, name, params):
        if name == "main":
            return "main"
        sig = [mangle_type(decay_param_type(p.full_type)) for p in params]
        return "{}__{}".format(name, "_".join(sig) if sig else "v")

    # ------------------------------------------------------------------
    # Scope and symbol helpers
    # ------------------------------------------------------------------
    def _push_scope(self, kind, name):
        self._scope_uid += 1
        record = {
            "id": self._scope_uid,
            "parent": self.scope_records[-1]["id"] if self.scope_records else None,
            "kind": kind,
            "name": name,
            "symbols": [],
        }
        self.scope_records.append(record)
        self.scopes.append({})
        return record["id"]

    def _pop_scope(self):
        if self.scopes:
            self.scopes.pop()

    @property
    def _scope(self):
        return self.scopes[-1]

    @property
    def _scope_id(self):
        return self.scope_records[-1]["id"]

    def _declare(self, symbol):
        if symbol.name in self._scope:
            previous = self._scope[symbol.name]
            self.errors.append(error(
                "semantic",
                "redeclaration of '{}' (previously declared at line {}, column {})".format(
                    symbol.name, previous.line, previous.col),
                symbol.line, symbol.col,
            ))
            return previous
        self._uid += 1
        symbol.uid = self._uid
        symbol.scope_id = self._scope_id
        symbol.is_global = self.scope_records[-1]["kind"] == "global"
        self._scope[symbol.name] = symbol
        self.scope_records[-1]["symbols"].append(symbol.to_dict())
        return symbol

    def _lookup(self, name):
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def analyze(self, program):
        if not isinstance(program, ast.Program):
            self.errors.append(error("semantic", "no program to analyse", 1, 1))
            return self._result()

        # Pass 1: collect using directives, function signatures and globals.
        for item in program.items:
            if isinstance(item, ast.UsingDirective):
                if item.namespace == "std":
                    self.using_std = True
            elif isinstance(item, ast.FunctionDecl):
                self._collect_function(item)
            elif isinstance(item, ast.VarDecl):
                self._collect_global_var(item)

        # Pass 2: check global initialisers and function bodies.
        for item in program.items:
            if isinstance(item, ast.VarDecl):
                self._analyze_global_init(item)
            elif isinstance(item, ast.FunctionDecl) and item.body is not None:
                self._analyze_function(item)

        self._check_main()
        return self._result()

    def _result(self):
        return SemanticResult(
            errors=self.errors,
            warnings=self.warnings,
            symbol_table={
                "globals": list(self.globals.values()) and [
                    s.to_dict() for s in self.globals.values()
                ] or [],
                "functions": [f.to_dict() for f in self.functions.values()],
                "scopes": self.scope_records,
            },
            functions=self.functions,
            globals=self.globals,
        )

    # ------------------------------------------------------------------
    # Functions
    # ------------------------------------------------------------------
    def _collect_function(self, node):
        return_type = node.return_type
        params = self._param_infos(node)
        mangled = self._mangle_function(node.name, params)
        node.mangled_name = mangled
        overloads = self.functions_by_name.setdefault(node.name, [])
        existing = self.functions.get(mangled)
        if existing:
            # same signature overload (mangled key) => declaration/redefinition rules
            if existing.return_type != return_type:
                self.errors.append(error(
                    "semantic",
                    "conflicting declaration of function '{}'".format(node.name),
                    node.line, node.col,
                ))
                return
            if node.body is not None:
                if existing.defined:
                    self.errors.append(error(
                        "semantic",
                        "redefinition of function '{}'".format(node.name),
                        node.line, node.col,
                    ))
                    return
                existing.defined = True
                existing.node = node
                existing.line = node.line
                existing.col = node.col
            node._function_info = existing
            return

        # check conflicting return type for same param signature among overloads? (invalid overload in C++)
        for ov in overloads:
            if ov.param_signature() == tuple(decay_param_type(p.full_type) for p in params):
                if ov.return_type != return_type:
                    self.errors.append(error(
                        "semantic",
                        "conflicting declaration of function '{}'".format(node.name),
                        node.line, node.col,
                    ))
                    return

        info = FunctionInfo(
            name=node.name,
            return_type=return_type,
            params=params,
            line=node.line,
            col=node.col,
            defined=node.body is not None,
            node=node,
            mangled_name=mangled,
        )
        self.functions[mangled] = info
        overloads.append(info)
        node._function_info = info

    def _param_infos(self, node):
        params = []
        raw = list(node.params)
        if len(raw) == 1 and raw[0].type_name == "void" and not raw[0].is_array and getattr(raw[0], "pointer_depth", 0) == 0 and not getattr(raw[0], "is_reference", False):
            raw = []
        for param in raw:
            if param.type_name == "void" and param.is_array:
                self.errors.append(error(
                    "semantic", "array of void is not allowed",
                    param.line, param.col,
                ))
            params.append(ParamInfo(
                name=param.name,
                type_name=param.type_name,
                is_array=param.is_array,
                is_const=getattr(param, "is_const", False),
                pointer_depth=getattr(param, "pointer_depth", 0),
                is_reference=getattr(param, "is_reference", False),
                line=param.line,
                col=param.col,
            ))
        return params

    def _analyze_function(self, node):
        info = getattr(node, "_function_info", None) or self.functions.get(getattr(node, "mangled_name", None) or node.name)
        if not info or info.node is not node:
            return
        self.current_function = info
        self.return_seen = False
        self.loop_depth = 0
        self.switch_depth = 0
        self._push_scope("function", node.name)
        raw_params = [p for p in node.params if not (p.type_name == "void" and not p.is_array and getattr(p, "pointer_depth", 0) == 0 and not getattr(p, "is_reference", False))]
        for raw_param, info_param in zip(raw_params, info.params):
            if not info_param.name:
                continue
            symbol = self._declare(Symbol(
                name=info_param.name,
                kind="parameter",
                type_name=info_param.type_name,
                is_array=info_param.is_array,
                array_size=None,
                is_const=info_param.is_const,
                pointer_depth=getattr(info_param, "pointer_depth", 0),
                is_reference=getattr(info_param, "is_reference", False),
                line=info_param.line,
                col=info_param.col,
            ))
            info_param._symbol = symbol
            raw_param._symbol = symbol
        self._analyze_stmt(node.body)
        if info.return_type != "void" and not self.return_seen and node.name != "main":
            self.warnings.append(warning(
                "semantic",
                "function '{}' may reach the end without returning a value".format(node.name),
                node.line, node.col,
            ))
        self._pop_scope()
        self.current_function = None

    def _check_main(self):
        mains = self.functions_by_name.get("main", [])
        if not mains:
            self.errors.append(error("semantic", "program has no 'main' function", 1, 1))
            return
        if len(mains) != 1:
            self.errors.append(error("semantic", "'main' must be unique in this subset", mains[0].line, mains[0].col))
        main = mains[0]
        if not main.defined:
            self.errors.append(error("semantic", "'main' is declared but never defined", main.line, main.col))
        if main.return_type != "int":
            self.errors.append(error("semantic", "'main' must return int", main.line, main.col))
        if main.params:
            self.errors.append(error(
                "semantic",
                "this subset requires 'main' to take no parameters",
                main.line, main.col,
            ))

    # ------------------------------------------------------------------
    # Global variables
    # ------------------------------------------------------------------
    def _collect_global_var(self, node):
        base = node.type_name
        if base == "void":
            self.errors.append(error("semantic", "variable '{}' cannot have type void".format(
                node.declarators[0].name if node.declarators else "?"), node.line, node.col))
            return
        for decl in node.declarators:
            size = self._array_size(decl, base)
            is_const = getattr(node, "is_const", False) or getattr(decl, "is_const", False)
            symbol = Symbol(
                name=decl.name,
                kind="variable",
                type_name=base,
                is_array=decl.is_array,
                array_size=size,
                is_const=is_const,
                pointer_depth=getattr(decl, "pointer_depth", 0),
                line=decl.line,
                col=decl.col,
            )
            stored = self._declare(symbol)
            if stored is symbol:
                self.globals[decl.name] = symbol
            decl._symbol = stored

    def _analyze_global_init(self, node):
        for decl in node.declarators:
            symbol = getattr(decl, "_symbol", None)
            if symbol is None:
                continue
            if decl.init is not None:
                self._check_initializer(decl, symbol)
            elif symbol.is_const:
                self.errors.append(error(
                    "semantic",
                    "const variable '{}' must be initialised".format(decl.name),
                    decl.line, decl.col,
                ))
            elif symbol.is_array and symbol.array_size is None:
                self.errors.append(error(
                    "semantic",
                    "array '{}' needs a size or an initialiser".format(decl.name),
                    decl.line, decl.col,
                ))

    # ------------------------------------------------------------------
    # Statements
    # ------------------------------------------------------------------
    def _analyze_stmt(self, node):
        if node is None:
            return
        if isinstance(node, ast.Block):
            self._push_scope("block", "<block>")
            for stmt in node.statements:
                self._analyze_stmt(stmt)
            self._pop_scope()
        elif isinstance(node, ast.VarDecl):
            self._analyze_local_var(node)
        elif isinstance(node, ast.ExprStmt):
            self._analyze_expr(node.expr)
        elif isinstance(node, ast.EmptyStmt):
            pass
        elif isinstance(node, ast.If):
            self._require_condition(node.condition, "if")
            self._push_scope("branch", "if")
            self._analyze_stmt(node.then_branch)
            self._pop_scope()
            if node.else_branch is not None:
                self._push_scope("branch", "else")
                self._analyze_stmt(node.else_branch)
                self._pop_scope()
        elif isinstance(node, ast.While):
            self._require_condition(node.condition, "while")
            self.loop_depth += 1
            self._analyze_stmt(node.body)
            self.loop_depth -= 1
        elif isinstance(node, ast.DoWhile):
            self.loop_depth += 1
            self._analyze_stmt(node.body)
            self.loop_depth -= 1
            self._require_condition(node.condition, "do-while")
        elif isinstance(node, ast.For):
            self._push_scope("loop", "for")
            if node.init is not None:
                if isinstance(node.init, ast.VarDecl):
                    self._analyze_local_var(node.init)
                else:
                    self._analyze_expr(node.init)
            if node.condition is not None:
                self._require_condition(node.condition, "for")
            if node.update is not None:
                self._analyze_expr(node.update)
            self.loop_depth += 1
            self._analyze_stmt(node.body)
            self.loop_depth -= 1
            self._pop_scope()
        elif isinstance(node, ast.Return):
            info = self.current_function
            return_type = info.return_type if info else "void"
            if node.value is not None:
                value_type = self._analyze_expr(node.value)
                if return_type == "void":
                    self.errors.append(error(
                        "semantic", "void function cannot return a value",
                        node.line, node.col,
                    ))
                elif not compatible(return_type, value_type):
                    self.errors.append(error(
                        "semantic",
                        "return type mismatch: expected '{}', got '{}'".format(return_type, value_type),
                        node.line, node.col,
                    ))
            else:
                if return_type != "void":
                    self.errors.append(error(
                        "semantic",
                        "non-void function '{}' must return a value".format(
                            info.name if info else "?"),
                        node.line, node.col,
                    ))
            self.return_seen = True
        elif isinstance(node, ast.Break):
            if self.loop_depth == 0 and self.switch_depth == 0:
                self.errors.append(error(
                    "semantic", "'break' is only allowed inside a loop or switch",
                    node.line, node.col,
                ))
        elif isinstance(node, ast.Continue):
            if self.loop_depth == 0:
                self.errors.append(error(
                    "semantic", "'continue' is only allowed inside a loop",
                    node.line, node.col,
                ))
        elif isinstance(node, ast.Switch):
            cond_type = self._analyze_expr(node.expr)
            if cond_type is not None and base_type(cond_type) not in ("int", "char", "bool"):
                self.errors.append(error(
                    "semantic", "switch condition must be an integer value",
                    node.expr.line, node.expr.col,
                ))
            self.switch_depth += 1
            self._push_scope("block", "switch")
            seen_cases = set()
            default_count = 0
            for section in node.cases:
                if isinstance(section, ast.Case):
                    value = constexpr.const_int(section.value)
                    if value is None:
                        self.errors.append(error(
                            "semantic", "case label must be a constant integer",
                            section.value.line, section.value.col,
                        ))
                    elif value in seen_cases:
                        self.errors.append(error(
                            "semantic", "duplicate case label '{}'".format(value),
                            section.value.line, section.value.col,
                        ))
                    else:
                        seen_cases.add(value)
                    self._analyze_expr(section.value)
                    for stmt in section.body:
                        self._analyze_stmt(stmt)
                elif isinstance(section, ast.Default):
                    default_count += 1
                    if default_count > 1:
                        self.errors.append(error(
                            "semantic", "multiple default labels in switch",
                            section.line, section.col,
                        ))
                    for stmt in section.body:
                        self._analyze_stmt(stmt)
            self._pop_scope()
            self.switch_depth -= 1
        elif isinstance(node, (ast.Case, ast.Default)):
            self.errors.append(error(
                "semantic", "case/default label outside of switch",
                node.line, node.col,
            ))
        elif isinstance(node, (ast.Preprocessor, ast.UsingDirective)):
            pass
        else:
            self.errors.append(error(
                "semantic", "unsupported statement '{}'".format(type(node).__name__),
                getattr(node, "line", 0), getattr(node, "col", 0),
            ))

    def _require_condition(self, expr, owner):
        typ = self._analyze_expr(expr)
        if base_type(typ) not in SCALAR:
            self.errors.append(error(
                "semantic",
                "{} condition must be a scalar value, got '{}'".format(owner, typ),
                getattr(expr, "line", 0), getattr(expr, "col", 0),
            ))

    def _analyze_local_var(self, node):
        base = node.type_name
        if base == "void":
            self.errors.append(error("semantic", "variable cannot have type void", node.line, node.col))
            return
        for decl in node.declarators:
            size = self._array_size(decl, base)
            is_const = getattr(node, "is_const", False) or getattr(decl, "is_const", False)
            symbol = Symbol(
                name=decl.name,
                kind="variable",
                type_name=base,
                is_array=decl.is_array,
                array_size=size,
                is_const=is_const,
                pointer_depth=getattr(decl, "pointer_depth", 0),
                line=decl.line,
                col=decl.col,
            )
            stored = self._declare(symbol)
            decl._symbol = stored
            if decl.init is not None:
                self._check_initializer(decl, stored)
            elif stored.is_const:
                self.errors.append(error(
                    "semantic",
                    "const variable '{}' must be initialised".format(decl.name),
                    decl.line, decl.col,
                ))
            elif stored.is_array and stored.array_size is None:
                self.errors.append(error(
                    "semantic",
                    "array '{}' needs a size or an initialiser".format(decl.name),
                    decl.line, decl.col,
                ))

    # ------------------------------------------------------------------
    # Initialisers
    # ------------------------------------------------------------------
    def _array_size(self, decl, base):
        if not decl.is_array:
            return None
        if decl.array_size is not None:
            size = constexpr.const_int(decl.array_size)
            if size is None:
                self.errors.append(error(
                    "semantic", "array size must be a constant integer",
                    decl.line, decl.col,
                ))
                return None
            if size <= 0:
                self.errors.append(error(
                    "semantic", "array size must be greater than zero",
                    decl.line, decl.col,
                ))
                return None
            return size
        if isinstance(decl.init, ast.InitList):
            return len(decl.init.elements)
        if base == "char" and isinstance(decl.init, ast.Literal) and decl.init.kind == "string":
            text = constexpr.parse_string_literal(decl.init.value)
            if text is not None:
                return len(text) + 1
        return None

    def _check_initializer(self, decl, symbol):
        declared = symbol.declared_type
        elem_base = symbol.type_name + ("*" * getattr(symbol, "pointer_depth", 0))
        if symbol.is_array:
            if isinstance(decl.init, ast.InitList):
                if symbol.array_size is not None and len(decl.init.elements) > symbol.array_size:
                    self.errors.append(error(
                        "semantic",
                        "too many initialisers for array '{}'".format(decl.name),
                        decl.line, decl.col,
                    ))
                for element in decl.init.elements:
                    typ = self._analyze_expr(element)
                    if not compatible(elem_base, typ):
                        self.errors.append(error(
                            "semantic",
                            "array element type mismatch: expected '{}', got '{}'".format(elem_base, typ),
                            element.line, element.col,
                        ))
                decl.init._type = make_array_type(elem_base, symbol.array_size)
            elif symbol.type_name == "char" and symbol.pointer_depth == 0 and isinstance(decl.init, ast.Literal) and decl.init.kind == "string":
                self._analyze_expr(decl.init)
            else:
                self.errors.append(error(
                    "semantic",
                    "array '{}' must be initialised with a brace-enclosed list".format(decl.name),
                    decl.line, decl.col,
                ))
            return
        typ = self._analyze_expr(decl.init)
        if typ is None:
            return
        target = symbol.value_type if getattr(symbol, "is_reference", False) else declared
        if not compatible(target, typ):
            self.errors.append(error(
                "semantic",
                "initialiser type mismatch: expected '{}', got '{}'".format(target, typ),
                decl.init.line, decl.init.col,
            ))
        if decl.init is not None:
            decl.init._type = typ

    # ------------------------------------------------------------------
    # Expressions
    # ------------------------------------------------------------------
    def _analyze_expr(self, node):
        if node is None:
            return None
        typ = None
        if isinstance(node, ast.Literal):
            typ = node.kind
            node._type = typ
            if typ == "string":
                node._const = constexpr.parse_string_literal(node.value)
            else:
                ct, cv = constexpr.const_value(node)
                if ct is not None:
                    node._const = cv
        elif isinstance(node, ast.Identifier):
            typ = self._identifier_type(node)
        elif isinstance(node, ast.Assign):
            typ = self._analyze_assign(node)
        elif isinstance(node, ast.Binary):
            typ = self._analyze_binary(node)
        elif isinstance(node, ast.Unary):
            typ = self._analyze_unary(node)
        elif isinstance(node, ast.AddressOf):
            if not self._is_lvalue(node.operand):
                self.errors.append(error(
                    "semantic", "cannot take address of a non-lvalue", node.line, node.col,
                ))
                typ = None
            else:
                inner = self._analyze_expr(node.operand)
                if inner is None:
                    typ = None
                else:
                    # &array gives pointer to element in decay cases? keep simple: &T -> T*
                    if is_array_type(inner):
                        typ = strip_top_ref(inner) + "*"
                    elif is_reference_type(inner):
                        typ = strip_top_ref(inner) + "*"
                    else:
                        typ = strip_top_ref(inner) + "*"
        elif isinstance(node, ast.Deref):
            inner = self._analyze_expr(node.operand)
            if inner is None:
                typ = None
            elif not is_pointer_type(inner):
                self.errors.append(error(
                    "semantic", "cannot dereference non-pointer type '{}'".format(inner),
                    node.line, node.col,
                ))
                typ = None
            else:
                typ = pointee_type(inner)
        elif isinstance(node, ast.Postfix):
            target_type = self._lvalue_type(node.operand, "postfix '{}'".format(node.op))
            if self._is_const_lvalue(node.operand):
                self.errors.append(error(
                    "semantic", "cannot modify a const value", node.line, node.col,
                ))
            if target_type is not None and base_type(strip_top_ref(target_type)) not in NUMERIC and not is_pointer_type(target_type):
                self.errors.append(error(
                    "semantic",
                    "'{}' needs a numeric or pointer operand".format(node.op),
                    node.line, node.col,
                ))
            typ = target_type
        elif isinstance(node, ast.Call):
            typ = self._analyze_call(node)
        elif isinstance(node, ast.Index):
            typ = self._analyze_index(node)
        elif isinstance(node, ast.Ternary):
            cond_type = self._analyze_expr(node.condition)
            if cond_type is not None and base_type(cond_type) not in SCALAR:
                self.errors.append(error(
                    "semantic", "ternary condition must be a scalar value",
                    node.line, node.col,
                ))
            true_type = self._analyze_expr(node.when_true)
            false_type = self._analyze_expr(node.when_false)
            typ = None
            if true_type is not None and false_type is not None:
                if compatible(true_type, false_type) and compatible(false_type, true_type):
                    typ = true_type if base_type(true_type) == base_type(false_type) else true_type
                elif base_type(true_type) in NUMERIC and base_type(false_type) in NUMERIC:
                    if "double" in (base_type(true_type), base_type(false_type)):
                        typ = "double"
                    elif "float" in (base_type(true_type), base_type(false_type)):
                        typ = "float"
                    else:
                        typ = "int"
                elif compatible(true_type, false_type):
                    typ = true_type
                elif compatible(false_type, true_type):
                    typ = false_type
                else:
                    self.errors.append(error(
                        "semantic",
                        "ternary branches must have compatible types, got '{}' and '{}'".format(
                            true_type, false_type),
                        node.line, node.col,
                    ))
            node._type = typ
            return typ
        elif isinstance(node, ast.InitList):
            self.errors.append(error(
                "semantic", "initialiser list is only allowed in a declaration",
                node.line, node.col,
            ))
            typ = None
        else:
            self.errors.append(error(
                "semantic", "unsupported expression '{}'".format(type(node).__name__),
                getattr(node, "line", 0), getattr(node, "col", 0),
            ))
        if typ is not None:
            node._type = typ
        return typ

    def _identifier_type(self, node):
        if node.namespace is None:
            symbol = self._lookup(node.name)
            if symbol is not None:
                node._symbol = symbol
                return symbol.value_type if hasattr(symbol, "value_type") else symbol.full_type
            if self.using_std and node.name in BUILTINS:
                node._builtin = BUILTINS[node.name]
                return BUILTINS[node.name]
            if node.name in self.functions:
                self.errors.append(error(
                    "semantic",
                    "function '{}' used as a value (function pointers are not supported)".format(node.name),
                    node.line, node.col,
                ))
                return None
            self.errors.append(error(
                "semantic", "undeclared identifier '{}'".format(node.name),
                node.line, node.col,
            ))
            return None
        if node.namespace == "std":
            if node.name in BUILTINS:
                node._builtin = BUILTINS[node.name]
                return BUILTINS[node.name]
            self.errors.append(error(
                "semantic", "unknown standard name 'std::{}'".format(node.name),
                node.line, node.col,
            ))
            return None
        self.errors.append(error(
            "semantic", "only the 'std' namespace is supported",
            node.line, node.col,
        ))
        return None

    def _is_lvalue(self, node):
        return isinstance(node, (ast.Identifier, ast.Index, ast.Deref))

    def _is_const_lvalue(self, node):
        if isinstance(node, ast.Identifier):
            symbol = getattr(node, "_symbol", None) or self._lookup(node.name)
            return bool(symbol and getattr(symbol, "is_const", False))
        if isinstance(node, ast.Index):
            return self._is_const_lvalue(node.array)
        if isinstance(node, ast.Deref):
            # conservative: unknown pointee constness -> mutable for now
            return False
        return False

    def _analyze_assign(self, node):
        target_type = self._lvalue_type(node.target, "assignment")
        value_type = self._analyze_expr(node.value)
        if target_type is None or value_type is None:
            return None
        if self._is_const_lvalue(node.target):
            self.errors.append(error(
                "semantic", "cannot assign to a const value", node.line, node.col,
            ))
            return target_type
        if node.op == "=":
            if is_array_type(target_type):
                self.errors.append(error(
                    "semantic", "cannot assign to an array", node.line, node.col,
                ))
                return target_type
            if not compatible(target_type, value_type):
                self.errors.append(error(
                    "semantic",
                    "assignment type mismatch: cannot assign '{}' to '{}'".format(value_type, target_type),
                    node.line, node.col,
                ))
            return target_type
        if node.op in ("<<=", ">>=", "&=", "|=", "^="):
            if base_type(target_type) not in ("int", "char", "bool") or base_type(value_type) not in ("int", "char", "bool"):
                self.errors.append(error(
                    "semantic",
                    "compound assignment '{}' requires integer operands".format(node.op),
                    node.line, node.col,
                ))
            return target_type
        if base_type(target_type) not in NUMERIC or base_type(value_type) not in NUMERIC:
            self.errors.append(error(
                "semantic",
                "compound assignment '{}' requires numeric operands".format(node.op),
                node.line, node.col,
            ))
        return target_type

    def _analyze_binary(self, node):
        left_type = self._analyze_expr(node.left)
        if left_type is None:
            self._analyze_expr(node.right)
            return None
        if node.op in ("<<", ">>") and base_type(left_type) in ("ostream", "istream"):
            return self._analyze_stream(node, left_type)
        right_type = self._analyze_expr(node.right)
        if right_type is None:
            return None
        lt, rt = strip_top_ref(left_type), strip_top_ref(right_type)
        lb, rb = base_type(lt), base_type(rt)

        if node.op in ("<<", ">>", "&", "|", "^"):
            if lb not in ("int", "char", "bool") or rb not in ("int", "char", "bool"):
                self.errors.append(error(
                    "semantic", "'{}' requires integer operands".format(node.op),
                    node.line, node.col,
                ))
            return "int"
        if node.op in ("+", "-", "*", "/", "%"):
            if is_pointer_type(lt) or is_pointer_type(rt):
                if node.op == "+":
                    if is_pointer_type(lt) and is_integral_type(rt):
                        return lt
                    if is_pointer_type(rt) and is_integral_type(lt):
                        return rt
                elif node.op == "-":
                    if is_pointer_type(lt) and is_integral_type(rt):
                        return lt
                    if is_pointer_type(lt) and is_pointer_type(rt) and lt == rt:
                        return "int"
                self.errors.append(error(
                    "semantic", "invalid pointer arithmetic with '{}'".format(node.op),
                    node.line, node.col,
                ))
                return lt if is_pointer_type(lt) else "int"
            if lb not in NUMERIC or rb not in NUMERIC:
                self.errors.append(error(
                    "semantic", "'{}' requires numeric operands, got '{}' and '{}'".format(
                        node.op, left_type, right_type),
                    node.line, node.col,
                ))
                return "int"
            if node.op == "%" and (lb not in ("int", "char", "bool") or rb not in ("int", "char", "bool")):
                self.errors.append(error(
                    "semantic", "'%' requires integer operands", node.line, node.col,
                ))
            if lb in ("float", "double") or rb in ("float", "double"):
                return "double"
            return "int"
        if node.op in ("<", ">", "<=", ">=", "==", "!="):
            if is_pointer_type(lt) or is_pointer_type(rt):
                if lt != rt:
                    self.errors.append(error(
                        "semantic", "pointer comparison requires compatible pointer types", node.line, node.col,
                    ))
                return "bool"
            if lb not in NUMERIC or rb not in NUMERIC:
                self.errors.append(error(
                    "semantic", "comparison requires numeric operands", node.line, node.col,
                ))
            return "bool"
        if node.op in ("&&", "||"):
            if lb not in SCALAR and not is_pointer_type(lt):
                self.errors.append(error(
                    "semantic", "logical operator requires scalar operands", node.line, node.col,
                ))
            if rb not in SCALAR and not is_pointer_type(rt):
                self.errors.append(error(
                    "semantic", "logical operator requires scalar operands", node.line, node.col,
                ))
            return "bool"
        self.errors.append(error(
            "semantic", "unsupported binary operator '{}'".format(node.op),
            node.line, node.col,
        ))
        return "int"

    def _analyze_stream(self, node, left_type):
        left_is_output = base_type(left_type) == "ostream"
        op = node.op
        if left_is_output and op != "<<":
            self.errors.append(error("semantic", "cout only supports '<<'", node.line, node.col))
            return left_type
        if not left_is_output and op != ">>":
            self.errors.append(error("semantic", "cin only supports '>>'", node.line, node.col))
            return left_type
        if left_is_output:
            right_type = self._analyze_expr(node.right)
            if right_type is None:
                return left_type
            if base_type(right_type) not in PRINTABLE and base_type(right_type) != "endl":
                self.errors.append(error(
                    "semantic", "cannot print a value of type '{}'".format(right_type),
                    node.line, node.col,
                ))
            return "ostream"
        target_type = self._lvalue_type(node.right, "input")
        if target_type is None:
            return left_type
        if self._is_const_lvalue(node.right):
            self.errors.append(error(
                "semantic", "cannot read into a const value", node.line, node.col,
            ))
        if base_type(target_type) not in INPUTTABLE:
            self.errors.append(error(
                "semantic", "cannot read into a value of type '{}'".format(target_type),
                node.line, node.col,
            ))
        return "istream"

    def _analyze_unary(self, node):
        if node.op in ("++", "--"):
            target_type = self._lvalue_type(node.operand, "unary '{}'".format(node.op))
            if self._is_const_lvalue(node.operand):
                self.errors.append(error(
                    "semantic", "cannot modify a const value", node.line, node.col,
                ))
            if target_type is not None and base_type(strip_top_ref(target_type)) not in NUMERIC and not is_pointer_type(target_type):
                self.errors.append(error(
                    "semantic", "'{}' needs a numeric or pointer operand".format(node.op),
                    node.line, node.col,
                ))
            return target_type
        typ = self._analyze_expr(node.operand)
        if typ is None:
            return None
        t = strip_top_ref(typ)
        if node.op == "!":
            if base_type(t) not in SCALAR and not is_pointer_type(t):
                self.errors.append(error(
                    "semantic", "'!' requires a scalar operand", node.line, node.col,
                ))
            return "bool"
        if node.op in ("+", "-"):
            if base_type(t) not in NUMERIC:
                self.errors.append(error(
                    "semantic", "'{}' requires a numeric operand".format(node.op),
                    node.line, node.col,
                ))
                return "int"
            return t
        if node.op == "~":
            if base_type(t) not in ("int", "char", "bool"):
                self.errors.append(error(
                    "semantic", "'~' requires an integer operand", node.line, node.col,
                ))
                return "int"
            return "int"
        self.errors.append(error(
            "semantic", "unsupported unary operator '{}'".format(node.op),
            node.line, node.col,
        ))
        return typ

    def _analyze_call(self, node):
        if not isinstance(node.callee, ast.Identifier):
            self.errors.append(error(
                "semantic", "only named functions can be called", node.line, node.col,
            ))
            for arg in node.args:
                self._analyze_expr(arg)
            return None
        name = node.callee.name
        overloads = self.functions_by_name.get(name, [])
        if not overloads:
            self.errors.append(error(
                "semantic", "call to undeclared function '{}'".format(name),
                node.callee.line, node.callee.col,
            ))
            for arg in node.args:
                self._analyze_expr(arg)
            return None
        arg_types = [self._analyze_expr(arg) for arg in node.args]

        def rank_param(param, arg, atyp):
            if atyp is None:
                return 3
            expected = param.full_type
            if param.is_reference:
                if not self._is_lvalue(arg):
                    return None
                et = strip_top_ref(expected)
                at = strip_top_ref(atyp)
                if et == at:
                    return 3
                if compatible(et, at):
                    return 2
                return None
            else:
                et = decay_param_type(expected)
                at = strip_top_ref(atyp)
                if et == at:
                    return 3
                if is_array_type(strip_top_ref(expected)) and is_pointer_type(at) and decay_param_type(expected) == at:
                    return 3
                if compatible(expected, atyp) or compatible(et, atyp):
                    eb = base_type(strip_top_ref(expected))
                    ab = base_type(at)
                    if et == at or eb == ab:
                        return 2
                    if eb in NUMERIC and ab in NUMERIC:
                        # prefer non-narrowing/wider conversions for floating args
                        if ab in ("float", "double") and eb in ("double", "float"):
                            return 2 if eb == "double" else 1
                        if ab in ("float", "double") and eb in ("int", "char", "bool"):
                            return 1
                        if ab in ("int", "char", "bool") and eb in ("float", "double"):
                            return 2
                    return 1
                return None

        scored = []
        for info in overloads:
            if len(info.params) != len(node.args):
                continue
            ranks = []
            ok = True
            for param, arg, atyp in zip(info.params, node.args, arg_types):
                r = rank_param(param, arg, atyp)
                if r is None:
                    ok = False
                    break
                ranks.append(r)
            if ok:
                scored.append((sum(ranks), ranks, info))

        if not scored:
            self.errors.append(error(
                "semantic",
                "no matching overload for call to '{}' with {} argument(s)".format(name, len(node.args)),
                node.line, node.col,
            ))
            return None
        best_score = max(s[0] for s in scored)
        best = [s for s in scored if s[0] == best_score]
        if len(best) > 1:
            filtered = []
            for cand in best:
                dominated = False
                for other in best:
                    if cand is other:
                        continue
                    if all(o >= c for o, c in zip(other[1], cand[1])) and any(o > c for o, c in zip(other[1], cand[1])):
                        dominated = True
                        break
                if not dominated:
                    filtered.append(cand)
            best = filtered
        if len(best) != 1:
            self.errors.append(error(
                "semantic",
                "call to '{}' is ambiguous".format(name),
                node.line, node.col,
            ))
            return None
        info = best[0][2]
        node._function = info
        node.mangled_name = info.mangled_name
        for index, (arg, param, atyp) in enumerate(zip(node.args, info.params, arg_types)):
            if param.is_reference and not self._is_lvalue(arg):
                self.errors.append(error(
                    "semantic",
                    "argument {} of '{}' must be an lvalue for reference parameter".format(index + 1, name),
                    arg.line, arg.col,
                ))
            if param.is_reference and self._is_const_lvalue(arg) and not param.is_const:
                self.errors.append(error(
                    "semantic",
                    "cannot bind non-const reference to const lvalue in argument {} of '{}'".format(index + 1, name),
                    arg.line, arg.col,
                ))
        return info.return_type

    def _analyze_index(self, node):
        base = self._analyze_expr(node.array)
        index_type = self._analyze_expr(node.index)
        if base is None or index_type is None:
            return None
        b = strip_top_ref(base)
        if is_array_type(b):
            elem = b[:b.find("[")]
            if base_type(index_type) not in ("int", "char", "bool"):
                self.errors.append(error(
                    "semantic", "array index must be an integer", node.line, node.col,
                ))
            return elem
        if is_pointer_type(b):
            if base_type(index_type) not in ("int", "char", "bool"):
                self.errors.append(error(
                    "semantic", "pointer index must be an integer", node.line, node.col,
                ))
            return pointee_type(b)
        self.errors.append(error(
            "semantic", "subscripted value is not an array or pointer", node.line, node.col,
        ))
        return None

    def _lvalue_type(self, node, context):
        if isinstance(node, ast.Identifier):
            if node.namespace is not None:
                self.errors.append(error(
                    "semantic", "{} target must be a variable".format(context),
                    node.line, node.col,
                ))
                return None
            symbol = self._lookup(node.name)
            if symbol is None:
                self.errors.append(error(
                    "semantic", "{} target '{}' is not declared".format(context, node.name),
                    node.line, node.col,
                ))
                return None
            node._symbol = symbol
            # reference params/vars act as lvalues of referenced type
            if symbol.is_reference:
                return symbol.type_name + ("*" * symbol.pointer_depth)
            return symbol.full_type
        if isinstance(node, ast.Index):
            return self._analyze_index(node)
        if isinstance(node, ast.Deref):
            t = self._analyze_expr(node.operand)
            if t is None:
                return None
            if not is_pointer_type(t):
                self.errors.append(error(
                    "semantic", "cannot dereference non-pointer type '{}'".format(t),
                    getattr(node, "line", 0), getattr(node, "col", 0),
                ))
                return None
            return pointee_type(t)
        typ = self._analyze_expr(node)
        self.errors.append(error(
            "semantic", "{} target must be a variable or array element".format(context),
            getattr(node, "line", 0), getattr(node, "col", 0),
        ))
        return typ


def analyze(program):
    """Analyse a parsed program and return a :class:`SemanticResult`."""
    return SemanticAnalyzer().analyze(program)
