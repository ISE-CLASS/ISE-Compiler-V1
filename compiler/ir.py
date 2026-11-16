"""Typed three-address intermediate representation.

The IR is intentionally small: one instruction object with an opcode, a
destination and operands.  It is produced from the semantically annotated AST
and consumed by :mod:`compiler.codegen`.
"""
from dataclasses import dataclass, field
from typing import List, Optional

from syntax_analysis import ast_nodes as ast
from . import constexpr
from .semantic import base_type, is_array_type, is_pointer_type, type_size_bytes, strip_top_ref, pointee_type


@dataclass
class IRValue:
    kind: str                 # var, temp, const, builtin
    text: str
    type: str = ""
    value: object = None
    symbol: object = None

    def to_dict(self):
        return {
            "kind": self.kind,
            "text": self.text,
            "type": self.type,
        }


@dataclass
class IRInstr:
    op: str
    args: List[IRValue] = field(default_factory=list)
    dest: Optional[IRValue] = None

    def text(self):
        a = [v.text for v in self.args]
        d = self.dest.text if self.dest else ""
        if self.op == "copy":
            return "{} = {}".format(d, a[0])
        if self.op == "bin":
            return "{} = {} {} {}".format(d, a[0], a[1], a[2])
        if self.op == "un":
            return "{} = {} {}".format(d, a[0], a[1])
        if self.op == "load":
            return "{} = {}[{}]".format(d, a[0], a[1])
        if self.op == "store":
            return "{}[{}] = {}".format(a[0], a[1], a[2])
        if self.op == "pload":
            return "{} = *{}".format(d, a[0])
        if self.op == "pstore":
            return "*{} = {}".format(a[0], a[1])
        if self.op == "addr":
            return "{} = &{}".format(d, a[0])
        if self.op == "addr_index":
            return "{} = &{}[{}]".format(d, a[0], a[1])
        if self.op == "param":
            return "param {}".format(a[0])
        if self.op == "call":
            return "{} = call {}({})".format(d, a[0], a[1]) if d else "call {}({})".format(a[0], a[1])
        if self.op == "ret":
            return "return {}".format(a[0]) if a else "return"
        if self.op == "goto":
            return "goto {}".format(a[0])
        if self.op == "label":
            return "{}:".format(a[0])
        if self.op == "if":
            return "if {} goto {}".format(a[0], a[1])
        if self.op == "ifnot":
            return "ifnot {} goto {}".format(a[0], a[1])
        if self.op == "print":
            return "print {}".format(a[0])
        if self.op == "print_endline":
            return "print endl"
        if self.op == "read":
            return "read {}".format(a[0])
        if self.op == "read_ptr":
            return "read *{}".format(a[0])
        if self.op == "read_index":
            return "read {}[{}]".format(a[0], a[1])
        if self.op == "init_string":
            return "{} = {}".format(d, a[0])
        return "{} {}".format(self.op, ", ".join(a))

    def to_dict(self):
        return {
            "op": self.op,
            "dest": self.dest.to_dict() if self.dest else None,
            "args": [v.to_dict() for v in self.args],
            "text": self.text(),
        }


@dataclass
class IRFunction:
    name: str
    return_type: str
    params: List[IRValue] = field(default_factory=list)
    instructions: List[IRInstr] = field(default_factory=list)
    mangled_name: str = ""

    def to_dict(self):
        return {
            "name": self.name,
            "mangled_name": self.mangled_name or self.name,
            "return_type": self.return_type,
            "params": [p.to_dict() for p in self.params],
            "instructions": [i.to_dict() for i in self.instructions],
        }


@dataclass
class IRProgram:
    functions: List[IRFunction] = field(default_factory=list)
    globals: List[dict] = field(default_factory=list)

    def to_dict(self):
        return {
            "functions": [f.to_dict() for f in self.functions],
            "globals": self.globals,
        }

    def text(self):
        lines = []
        for global_info in self.globals:
            lines.append("global {} {}".format(
                global_info.get("type", ""), global_info.get("name", "")))
        for fn in self.functions:
            lines.append("")
            fname = fn.mangled_name or fn.name
            lines.append("function {} {}(".format(fn.return_type, fname) + ", ".join(
                "{} {}".format(p.type, p.text) for p in fn.params) + ") {")
            for instr in fn.instructions:
                lines.append("    " + instr.text())
            lines.append("}")
        return "\n".join(lines).strip()


def _var_name(symbol):
    prefix = "g" if getattr(symbol, "is_global", False) else "v"
    return "{}_{}_{}".format(prefix, symbol.uid, symbol.name)


class IRBuilder:
    def __init__(self):
        self.program = IRProgram()
        self.func = None
        self._temp = 0
        self._label = 0
        self.loops = []
        self.break_targets = []

    def lower(self, program):
        for item in program.items:
            if isinstance(item, ast.VarDecl):
                for decl in item.declarators:
                    symbol = getattr(decl, "_symbol", None)
                    if symbol:
                        self.program.globals.append({
                            "name": symbol.name,
                            "type": symbol.full_type,
                            "has_initialiser": decl.init is not None,
                            "line": decl.line,
                            "col": decl.col,
                        })
        for item in program.items:
            if isinstance(item, ast.FunctionDecl) and item.body is not None:
                self._function(item)
        return self.program

    def _function(self, node):
        info = getattr(node, "_function_info", None)
        params = []
        for raw in node.params:
            if raw.type_name == "void" and not raw.is_array and getattr(raw, "pointer_depth", 0) == 0 and not getattr(raw, "is_reference", False):
                continue
            symbol = getattr(raw, "_symbol", None)
            if symbol is not None:
                params.append(self._var(symbol))
        fn = IRFunction(
            name=node.name,
            return_type=info.return_type if info else node.return_type,
            params=params,
            mangled_name=(getattr(info, "mangled_name", None) or getattr(node, "mangled_name", None) or node.name),
        )
        self.func = fn
        self._temp = 0
        self._label = 0
        self.loops = []
        self.break_targets = []
        self._stmt(node.body)
        if not fn.instructions or fn.instructions[-1].op != "ret":
            if fn.return_type == "void" or fn.name == "main":
                fn.instructions.append(IRInstr("ret"))
        self.program.functions.append(fn)
        self.func = None

    def _emit(self, op, args=None, dest=None):
        instr = IRInstr(op, list(args or []), dest)
        self.func.instructions.append(instr)
        return instr

    def _temp_value(self, type_name):
        self._temp += 1
        return IRValue("temp", "t{}".format(self._temp), type_name or "int")

    def _label_value(self):
        self._label += 1
        return IRValue("label", "L{}".format(self._label), "label")

    def _var(self, symbol):
        t = symbol.full_type
        if getattr(symbol, "is_reference", False):
            # reference stored as pointer value
            t = (symbol.type_name + ("*" * getattr(symbol, "pointer_depth", 0))) + "*"
        return IRValue("var", _var_name(symbol), t, symbol=symbol)

    def _const(self, type_name, value, text=None):
        if text is None:
            if type_name == "string":
                text = '"{}"'.format(str(value).replace('"', '\\"'))
            elif type_name == "bool":
                text = "1" if value else "0"
            else:
                text = str(value)
        return IRValue("const", text, type_name, value=value)

    def _op(self, text):
        return IRValue("const", text, "op", value=text)

    def _expr(self, node):
        if isinstance(node, ast.Literal):
            if node.kind == "string":
                text = constexpr.parse_string_literal(node.value)
                return self._const("string", text, node.value)
            if node.kind == "float":
                value = constexpr.parse_float_literal(node.value)
                return self._const("float", value if value is not None else 0.0, node.value)
            if node.kind == "char":
                value = constexpr.parse_char_literal(node.value)
                return self._const("char", value if value is not None else 0, node.value)
            if node.kind == "bool":
                return self._const("bool", 1 if node.value == "true" else 0, node.value)
            value = constexpr.parse_int_literal(node.value)
            return self._const("int", value if value is not None else 0)
        if isinstance(node, ast.Identifier):
            if getattr(node, "_builtin", None):
                return IRValue("builtin", node.name, node._builtin, value=node._builtin)
            symbol = getattr(node, "_symbol", None)
            if symbol is not None:
                if getattr(symbol, "is_reference", False):
                    ptr = self._temp_value(symbol.type_name + ("*" * getattr(symbol, "pointer_depth", 0)) + "*")
                    self._emit("copy", [self._var(symbol)], ptr)
                    ref_type = symbol.type_name + ("*" * getattr(symbol, "pointer_depth", 0))
                    if symbol.is_array or base_type(ref_type) == "string":
                        return ptr
                    dest = self._temp_value(ref_type)
                    self._emit("pload", [ptr], dest)
                    return dest
                return self._var(symbol)
            return self._const("int", 0)
        if isinstance(node, ast.Assign):
            return self._assign(node)
        if isinstance(node, ast.Binary):
            return self._binary(node)
        if isinstance(node, ast.Unary):
            return self._unary(node)
        if isinstance(node, ast.Postfix):
            return self._postfix(node)
        if isinstance(node, ast.Call):
            return self._call(node)
        if isinstance(node, ast.Index):
            return self._index_rvalue(node)
        if isinstance(node, ast.AddressOf):
            return self._address_of(node.operand)
        if isinstance(node, ast.Deref):
            ptr = self._expr(node.operand)
            dest = self._temp_value(getattr(node, "_type", None) or pointee_type(ptr.type) or "int")
            self._emit("pload", [ptr], dest)
            return dest
        if isinstance(node, ast.Ternary):
            cond = self._expr(node.condition)
            result = self._temp_value(getattr(node, "_type", "int") or "int")
            false_label = self._label_value()
            end_label = self._label_value()
            self._emit("ifnot", [cond, false_label])
            true_val = self._expr(node.when_true)
            self._emit("copy", [true_val], result)
            self._emit("goto", [end_label])
            self._emit("label", [false_label])
            false_val = self._expr(node.when_false)
            self._emit("copy", [false_val], result)
            self._emit("label", [end_label])
            return result
        return self._const("int", 0)

    def _index_rvalue(self, node):
        base = self._expr(node.array)
        index = self._expr(node.index)
        btype = strip_top_ref(getattr(node.array, "_type", "") or base.type or "")
        if is_pointer_type(btype):
            elem_t = pointee_type(btype) or base_type(getattr(node, "_type", "int"))
            addr = self._temp_value(btype)
            scale = type_size_bytes(elem_t) or 8
            scaled = self._temp_value("int")
            self._emit("bin", [index, self._op("*"), self._const("int", scale)], scaled)
            self._emit("bin", [base, self._op("+ptr"), scaled], addr)
            dest = self._temp_value(getattr(node, "_type", elem_t))
            self._emit("pload", [addr], dest)
            return dest
        dest = self._temp_value(base_type(getattr(node, "_type", "int")))
        self._emit("load", [base, index], dest)
        return dest

    def _address_of(self, node):
        if isinstance(node, ast.Identifier):
            symbol = getattr(node, "_symbol", None)
            if symbol is not None and getattr(symbol, "is_reference", False):
                v = self._var(symbol)
                dest = self._temp_value(symbol.type_name + ("*" * getattr(symbol, "pointer_depth", 0)) + "*")
                self._emit("copy", [v], dest)
                return dest
            base = self._var(symbol) if symbol is not None else self._expr(node)
            dest = self._temp_value((getattr(node, "_type", "int") or base.type) + "*")
            self._emit("addr", [base], dest)
            return dest
        if isinstance(node, ast.Index):
            arr = self._expr(node.array)
            idx = self._expr(node.index)
            arr_t = strip_top_ref(getattr(node.array, "_type", "") or arr.type or "")
            if is_pointer_type(arr_t):
                elem_t = pointee_type(arr_t) or "int"
                scaled = self._temp_value("int")
                self._emit("bin", [idx, self._op("*"), self._const("int", type_size_bytes(elem_t) or 8)], scaled)
                dest = self._temp_value(arr_t)
                self._emit("bin", [arr, self._op("+ptr"), scaled], dest)
                return dest
            elem_t = base_type(getattr(node, "_type", None) or arr_t)
            dest = self._temp_value((elem_t or "int") + "*")
            self._emit("addr_index", [arr, idx], dest)
            return dest
        if isinstance(node, ast.Deref):
            return self._expr(node.operand)
        val = self._expr(node)
        dest = self._temp_value((val.type or "int") + "*")
        self._emit("addr", [val], dest)
        return dest

    def _assign(self, node):
        value = self._expr(node.value)
        if node.op == "=":
            self._store_lvalue(node.target, value)
            return value
        current = self._load_lvalue(node.target)
        dest = self._temp_value(base_type(getattr(node, "_type", None) or current.type or "int"))
        self._emit("bin", [current, self._op(node.op[:-1]), value], dest)
        self._store_lvalue(node.target, dest)
        return dest

    def _binary(self, node):
        left_type = getattr(node.left, "_type", "")
        if node.op in ("<<", ">>") and base_type(left_type) in ("ostream", "istream"):
            return self._stream(node)
        if node.op in ("&&", "||"):
            return self._short_circuit(node)
        left = self._expr(node.left)
        right = self._expr(node.right)
        dest_type = getattr(node, "_type", "int") or "int"
        op = node.op
        lt = strip_top_ref(left_type or left.type or "")
        rt = strip_top_ref(getattr(node.right, "_type", "") or right.type or "")
        if is_pointer_type(lt) and op in ("+", "-") and not is_pointer_type(rt):
            scaled = self._temp_value("int")
            self._emit("bin", [right, self._op("*"), self._const("int", type_size_bytes(pointee_type(lt) or "int") or 8)], scaled)
            dest = self._temp_value(dest_type)
            self._emit("bin", [left, self._op(op + "ptr"), scaled], dest)
            return dest
        if is_pointer_type(rt) and op == "+" and not is_pointer_type(lt):
            scaled = self._temp_value("int")
            self._emit("bin", [left, self._op("*"), self._const("int", type_size_bytes(pointee_type(rt) or "int") or 8)], scaled)
            dest = self._temp_value(dest_type)
            self._emit("bin", [right, self._op("+ptr"), scaled], dest)
            return dest
        if is_pointer_type(lt) and is_pointer_type(rt) and op == "-":
            raw = self._temp_value("int")
            self._emit("bin", [left, self._op("-ptr"), right], raw)
            dest = self._temp_value("int")
            size = type_size_bytes(pointee_type(lt) or "int") or 1
            self._emit("bin", [raw, self._op("/"), self._const("int", size)], dest)
            return dest
        dest = self._temp_value(dest_type)
        self._emit("bin", [left, self._op(op), right], dest)
        return dest

    def _stream(self, node):
        operands = self._flatten_stream(node, node.op)
        is_output = base_type(node.left._type) == "ostream"
        for operand in operands:
            if is_output:
                if isinstance(operand, ast.Identifier) and operand.name == "endl":
                    self._emit("print_endline")
                else:
                    value = self._expr(operand)
                    self._emit("print", [value])
            else:
                op, args = self._read_target(operand)
                self._emit(op, args)
        return self._const("int", 0, "stream")

    def _flatten_stream(self, node, op):
        if isinstance(node, ast.Binary) and node.op == op and base_type(
                getattr(node.left, "_type", "")) in ("ostream", "istream"):
            return self._flatten_stream(node.left, op) + [node.right]
        if isinstance(node, ast.Identifier) and getattr(node, "_builtin", None) in ("ostream", "istream"):
            return []
        return [node]

    def _short_circuit(self, node):
        result = self._temp_value("bool")
        left = self._expr(node.left)
        end_label = self._label_value()
        if node.op == "&&":
            right_label = self._label_value()
            false_label = self._label_value()
            self._emit("if", [left, right_label])
            self._emit("copy", [self._const("bool", 0)], result)
            self._emit("goto", [end_label])
            self._emit("label", [right_label])
            right = self._expr(node.right)
            self._emit("if", [right, true_label := self._label_value()])
            self._emit("copy", [self._const("bool", 0)], result)
            self._emit("goto", [end_label])
            self._emit("label", [true_label])
            self._emit("copy", [self._const("bool", 1)], result)
            self._emit("label", [end_label])
        else:
            true_label = self._label_value()
            right_label = self._label_value()
            self._emit("if", [left, true_label])
            self._emit("goto", [right_label])
            self._emit("label", [true_label])
            self._emit("copy", [self._const("bool", 1)], result)
            self._emit("goto", [end_label])
            self._emit("label", [right_label])
            right = self._expr(node.right)
            self._emit("if", [right, set_true := self._label_value()])
            self._emit("copy", [self._const("bool", 0)], result)
            self._emit("goto", [end_label])
            self._emit("label", [set_true])
            self._emit("copy", [self._const("bool", 1)], result)
            self._emit("label", [end_label])
        return result

    def _unary(self, node):
        if node.op in ("++", "--"):
            current = self._load_lvalue(node.operand)
            dest = self._temp_value(base_type(getattr(node, "_type", None) or current.type or "int"))
            t = strip_top_ref(getattr(node, "_type", "") or current.type or "")
            if is_pointer_type(t):
                step = type_size_bytes(pointee_type(t) or "int") or 8
                self._emit("bin", [current, self._op(node.op[0] + "ptr"), self._const("int", step)], dest)
            else:
                self._emit("bin", [current, self._op(node.op[0]), self._const("int", 1)], dest)
            self._store_lvalue(node.operand, dest)
            return dest
        operand = self._expr(node.operand)
        dest = self._temp_value(getattr(node, "_type", None) or operand.type or "int")
        self._emit("un", [self._op(node.op), operand], dest)
        return dest

    def _postfix(self, node):
        old = self._temp_value(base_type(getattr(node, "_type", None) or "int"))
        self._emit("copy", [self._load_lvalue(node.operand)], old)
        new = self._temp_value(base_type(getattr(node, "_type", None) or old.type or "int"))
        t = strip_top_ref(getattr(node, "_type", "") or old.type or "")
        if is_pointer_type(t):
            step = type_size_bytes(pointee_type(t) or "int") or 8
            self._emit("bin", [old, self._op(node.op[0] + "ptr"), self._const("int", step)], new)
        else:
            self._emit("bin", [old, self._op(node.op[0]), self._const("int", 1)], new)
        self._store_lvalue(node.operand, new)
        return old

    def _call(self, node):
        info = getattr(node, "_function", None)
        for i, arg in enumerate(node.args):
            if info and i < len(info.params) and info.params[i].is_reference:
                self._emit("param", [self._address_of(arg)])
            else:
                self._emit("param", [self._expr(arg)])
        callee_name = getattr(info, "mangled_name", None) or getattr(node, "mangled_name", None) or node.callee.name
        if info and info.return_type != "void":
            dest = self._temp_value(info.return_type)
            self._emit("call", [self._const("string", callee_name),
                               self._const("int", len(node.args))], dest)
            return dest
        self._emit("call", [self._const("string", callee_name),
                           self._const("int", len(node.args))])
        return self._const("int", 0, "void")

    def _load_lvalue(self, node):
        if isinstance(node, ast.Identifier):
            symbol = getattr(node, "_symbol", None)
            if symbol is None:
                return self._const("int", 0)
            if getattr(symbol, "is_reference", False):
                ptr = self._temp_value(symbol.type_name + ("*" * getattr(symbol, "pointer_depth", 0)) + "*")
                self._emit("copy", [self._var(symbol)], ptr)
                t = symbol.type_name + ("*" * getattr(symbol, "pointer_depth", 0))
                if symbol.is_array or base_type(t) == "string":
                    return ptr
                dest = self._temp_value(t)
                self._emit("pload", [ptr], dest)
                return dest
            return self._var(symbol)
        if isinstance(node, ast.Index):
            return self._index_rvalue(node)
        if isinstance(node, ast.Deref):
            ptr = self._expr(node.operand)
            dest = self._temp_value(getattr(node, "_type", None) or pointee_type(ptr.type) or "int")
            self._emit("pload", [ptr], dest)
            return dest
        return self._expr(node)

    def _store_lvalue(self, node, value):
        if isinstance(node, ast.Identifier):
            symbol = getattr(node, "_symbol", None)
            if symbol is None:
                return
            if getattr(symbol, "is_reference", False):
                ptr = self._temp_value(symbol.type_name + ("*" * getattr(symbol, "pointer_depth", 0)) + "*")
                self._emit("copy", [self._var(symbol)], ptr)
                self._emit("pstore", [ptr, value])
                return
            self._emit("copy", [value], self._var(symbol))
            return
        if isinstance(node, ast.Index):
            arr_t = strip_top_ref(getattr(node.array, "_type", "") or "")
            if is_pointer_type(arr_t):
                base_ptr = self._expr(node.array)
                idx = self._expr(node.index)
                elem_t = pointee_type(arr_t) or "int"
                scaled = self._temp_value("int")
                self._emit("bin", [idx, self._op("*"), self._const("int", type_size_bytes(elem_t) or 8)], scaled)
                addr = self._temp_value(arr_t)
                self._emit("bin", [base_ptr, self._op("+ptr"), scaled], addr)
                self._emit("pstore", [addr, value])
                return
            base_arr = self._expr(node.array)
            idx = self._expr(node.index)
            self._emit("store", [base_arr, idx, value])
            return
        if isinstance(node, ast.Deref):
            ptr = self._expr(node.operand)
            self._emit("pstore", [ptr, value])
            return
        self._emit("copy", [value], self._expr(node))

    def _read_target(self, node):
        if isinstance(node, ast.Identifier):
            symbol = getattr(node, "_symbol", None)
            if symbol is not None and getattr(symbol, "is_reference", False):
                ptr = self._temp_value(symbol.type_name + ("*" * getattr(symbol, "pointer_depth", 0)) + "*")
                self._emit("copy", [self._var(symbol)], ptr)
                return ("read_ptr", [ptr])
            if symbol is not None:
                return ("read", [self._var(symbol)])
        if isinstance(node, ast.Index):
            arr_t = strip_top_ref(getattr(node.array, "_type", "") or "")
            if is_pointer_type(arr_t):
                addr = self._address_of(node)
                return ("read_ptr", [addr])
            return ("read_index", [self._expr(node.array), self._expr(node.index)])
        if isinstance(node, ast.Deref):
            return ("read_ptr", [self._expr(node.operand)])
        return ("read", [self._expr(node)])

    def _stmt(self, node):
        if node is None:
            return
        if isinstance(node, ast.Block):
            for stmt in node.statements:
                self._stmt(stmt)
        elif isinstance(node, ast.VarDecl):
            self._var_decl(node)
        elif isinstance(node, ast.ExprStmt):
            self._expr(node.expr)
        elif isinstance(node, ast.EmptyStmt):
            pass
        elif isinstance(node, ast.If):
            cond = self._expr(node.condition)
            else_label = self._label_value()
            end_label = self._label_value()
            self._emit("ifnot", [cond, else_label])
            self._stmt(node.then_branch)
            if node.else_branch is not None:
                self._emit("goto", [end_label])
                self._emit("label", [else_label])
                self._stmt(node.else_branch)
                self._emit("label", [end_label])
            else:
                self._emit("label", [else_label])
        elif isinstance(node, ast.While):
            start = self._label_value()
            end = self._label_value()
            self._emit("label", [start])
            cond = self._expr(node.condition)
            self._emit("ifnot", [cond, end])
            self.loops.append((end, start))
            self.break_targets.append(end)
            self._stmt(node.body)
            self.break_targets.pop()
            self.loops.pop()
            self._emit("goto", [start])
            self._emit("label", [end])
        elif isinstance(node, ast.DoWhile):
            start = self._label_value()
            end = self._label_value()
            continue_label = self._label_value()
            self._emit("label", [start])
            self.loops.append((end, continue_label))
            self.break_targets.append(end)
            self._stmt(node.body)
            self.break_targets.pop()
            self.loops.pop()
            self._emit("label", [continue_label])
            cond = self._expr(node.condition)
            self._emit("if", [cond, start])
            self._emit("label", [end])
        elif isinstance(node, ast.For):
            if node.init is not None:
                if isinstance(node.init, ast.VarDecl):
                    self._var_decl(node.init)
                else:
                    self._expr(node.init)
            start = self._label_value()
            end = self._label_value()
            update = self._label_value()
            self._emit("label", [start])
            if node.condition is not None:
                cond = self._expr(node.condition)
                self._emit("ifnot", [cond, end])
            self.loops.append((end, update))
            self.break_targets.append(end)
            self._stmt(node.body)
            self.break_targets.pop()
            self.loops.pop()
            self._emit("label", [update])
            if node.update is not None:
                self._expr(node.update)
            self._emit("goto", [start])
            self._emit("label", [end])
        elif isinstance(node, ast.Switch):
            cond_val = self._expr(node.expr)
            end_label = self._label_value()
            section_labels = [self._label_value() for _ in node.cases]
            default_index = None
            for i, section in enumerate(node.cases):
                if isinstance(section, ast.Default):
                    default_index = i
                    continue
                case_const = self._expr(section.value)
                cmp_tmp = self._temp_value("bool")
                self._emit("bin", [cond_val, self._op("=="), case_const], cmp_tmp)
                self._emit("if", [cmp_tmp, section_labels[i]])
            if default_index is not None:
                self._emit("goto", [section_labels[default_index]])
            else:
                self._emit("goto", [end_label])
            self.break_targets.append(end_label)
            for i, section in enumerate(node.cases):
                self._emit("label", [section_labels[i]])
                for stmt in section.body:
                    self._stmt(stmt)
            self.break_targets.pop()
            self._emit("label", [end_label])
        elif isinstance(node, ast.Return):
            if node.value is not None:
                self._emit("ret", [self._expr(node.value)])
            else:
                self._emit("ret")
        elif isinstance(node, ast.Break):
            if self.break_targets:
                self._emit("goto", [self.break_targets[-1]])
        elif isinstance(node, ast.Continue):
            if self.loops:
                self._emit("goto", [self.loops[-1][1]])
        elif isinstance(node, (ast.Preprocessor, ast.UsingDirective)):
            pass

    def _var_decl(self, node):
        for decl in node.declarators:
            symbol = getattr(decl, "_symbol", None)
            if symbol is None or decl.init is None:
                continue
            if isinstance(decl.init, ast.InitList):
                basev = self._var(symbol)
                for index, element in enumerate(decl.init.elements):
                    self._emit("store", [basev, self._const("int", index), self._expr(element)])
            elif symbol.is_array and symbol.type_name == "char" and isinstance(decl.init, ast.Literal):
                text = constexpr.parse_string_literal(decl.init.value) or ""
                self._emit("init_string", [self._const("string", text, decl.init.value)], self._var(symbol))
            else:
                val = self._expr(decl.init)
                self._emit("copy", [val], self._var(symbol))


def lower(program):
    """Lower a semantically annotated AST to :class:`IRProgram`."""
    return IRBuilder().lower(program)
