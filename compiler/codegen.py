"""x86-64 code generation for the supported C++ subset.

Targets:
  * Linux / macOS: System V AMD64 ABI
  * Windows: Microsoft x64 ABI (MinGW-w64 / MSVC-compatible calling)

User functions use a small private stack-based calling convention, so generated
code does not depend on the platform ABI for calls between user functions.
Only libc helpers (printf, scanf, strcpy) use the host ABI.

The output is GNU assembler syntax in Intel form, accepted by g++, gcc or
clang on x86-64.  ``compiler.toolchain`` detects what is installed.
"""
import platform
from dataclasses import dataclass

from syntax_analysis import ast_nodes as ast
from . import constexpr
from .semantic import base_type, is_array_type, is_pointer_type, strip_top_ref, type_size_bytes

NUMERIC = {"int", "float", "double", "char", "bool"}


def _array_count(type_name):
    t = strip_top_ref(type_name or "")
    if not is_array_type(t):
        return None
    body = t[t.find("[") + 1:t.rfind("]")]
    try:
        return int(body)
    except ValueError:
        return None


def element_size(type_name):
    base = base_type(type_name)
    if base in ("char", "bool"):
        return 1
    if base == "string":
        return 256
    # pointers and numeric stored in 8 bytes in this backend
    return 8


def storage_size(type_name, is_parameter=False):
    base = base_type(type_name)
    t = strip_top_ref(type_name or "")
    # references stored as pointer slots
    if type_name and type_name.endswith("*") and getattr is None:
        pass
    if is_parameter and (is_array_type(t) or base == "string" or is_pointer_type(t)):
        return 8
    if is_array_type(t):
        count = _array_count(t) or 0
        return max(1, element_size(base) * count)
    if base == "string":
        return 256
    return 8


def _align(value, boundary):
    return (value + boundary - 1) // boundary * boundary


def gas_escape(text):
    out = []
    for ch in text:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif 32 <= code < 127:
            out.append(ch)
        else:
            out.append("\\{:03o}".format(code & 0xFF))
    return "".join(out)


@dataclass
class Slot:
    offset: int
    type: str
    symbol: object = None


class X86Codegen:
    def __init__(self, program, ir_program, semantic_result=None):
        self.program = program
        self.ir = ir_program
        self.semantic = semantic_result
        self.system = platform.system().lower()
        self.machine = platform.machine().lower()
        self.is_windows = self.system == "windows"
        self.is_darwin = self.system == "darwin"
        self.symbol_prefix = "_" if self.is_darwin else ""
        self.text = []
        self.rodata = []
        self.data = []
        self.bss = []
        self._string_labels = {}
        self._float_labels = {}
        self._label_counter = 0
        self._rodata_counter = 0
        self.slots = {}
        self.temp_slots = {}
        self.frame_size = 0
        self.current_function = None
        self.epilogue = ""
        self.pending_params = []
        self.func_nodes = {}
        for item in program.items:
            if isinstance(item, ast.FunctionDecl):
                mangled = getattr(item, "mangled_name", None) or item.name
                self.func_nodes[mangled] = item
                # keep last-seen name mapping as fallback for legacy/single defs
                self.func_nodes[item.name] = item
        self._global_labels = {}
        for item in program.items:
            if isinstance(item, ast.VarDecl):
                for decl in item.declarators:
                    symbol = getattr(decl, "_symbol", None)
                    if symbol is not None:
                        self._global_labels[symbol.uid] = "g_{}_{}".format(symbol.uid, symbol.name)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def generate(self):
        self._emit_globals()
        for fn in self.ir.functions:
            self._function(fn)
        lines = [".intel_syntax noprefix"]
        if self.rodata:
            lines.append(self._section_rodata())
            lines.extend(self.rodata)
        if self.data:
            lines.append(self._section_data())
            lines.extend(self.data)
        if self.bss:
            lines.append(".bss")
            lines.extend(self.bss)
        lines.append(".text")
        lines.extend(self.text)
        return "\n".join(lines).rstrip() + "\n"

    def _section_rodata(self):
        if self.is_darwin:
            return ".section __TEXT,__cstring,cstring_literals"
        return ".section .rodata"

    def _section_data(self):
        if self.is_darwin:
            return ".data"
        return ".data"

    def _string_label(self, text):
        if text in self._string_labels:
            return self._string_labels[text]
        self._rodata_counter += 1
        label = ".LC{}".format(self._rodata_counter)
        self._string_labels[text] = label
        self.rodata.append('{}: .asciz "{}"'.format(label, gas_escape(text)))
        return label

    def _float_label(self, value):
        key = repr(float(value))
        if key in self._float_labels:
            return self._float_labels[key]
        self._rodata_counter += 1
        label = ".LCF{}".format(self._rodata_counter)
        self._float_labels[key] = label
        self.rodata.append("{}: .double {}".format(label, repr(float(value))))
        return label

    def _emit_globals(self):
        for item in self.program.items:
            if not isinstance(item, ast.VarDecl):
                continue
            for decl in item.declarators:
                symbol = getattr(decl, "_symbol", None)
                if symbol is None:
                    continue
                label = self._global_labels[symbol.uid]
                typ = symbol.full_type
                size = storage_size(typ)
                if decl.init is None:
                    self.bss.append(".align 8")
                    self.bss.append("{}: .zero {}".format(label, size))
                    continue
                if symbol.is_array and symbol.type_name == "char" and isinstance(decl.init, ast.Literal):
                    text = constexpr.parse_string_literal(decl.init.value) or ""
                    self.data.append(".align 8")
                    self.data.append('{}: .asciz "{}"'.format(label, gas_escape(text)))
                    remaining = max(0, size - len(text) - 1)
                    if remaining:
                        self.data.append("    .zero {}".format(remaining))
                    continue
                if isinstance(decl.init, ast.InitList):
                    self._emit_global_array(label, symbol, decl.init)
                    continue
                typ_const, value = constexpr.const_value(decl.init)
                if base_type(typ) == "string":
                    text = ""
                    if isinstance(decl.init, ast.Literal):
                        text = constexpr.parse_string_literal(decl.init.value) or ""
                    self.data.append(".align 8")
                    self.data.append('{}: .asciz "{}"'.format(label, gas_escape(text)))
                    remaining = max(0, size - len(text) - 1)
                    if remaining:
                        self.data.append("    .zero {}".format(remaining))
                    continue
                self.data.append(".align 8")
                self.data.append("{}: {}".format(label, self._data_directive(base_type(typ), value if value is not None else 0)))

    def _emit_global_array(self, label, symbol, init_list):
        base = symbol.type_name
        count = symbol.array_size or len(init_list.elements)
        self.data.append(".align 8")
        self.data.append("{}:".format(label))
        for element in init_list.elements:
            typ, value = constexpr.const_value(element)
            if base == "string":
                text = ""
                if isinstance(element, ast.Literal):
                    text = constexpr.parse_string_literal(element.value) or ""
                self.data.append('    .asciz "{}"'.format(gas_escape(text)))
                remaining = max(0, 256 - len(text) - 1)
                if remaining:
                    self.data.append("    .zero {}".format(remaining))
            else:
                self.data.append("    " + self._data_directive(base, value if value is not None else 0))
        remaining = max(0, count - len(init_list.elements))
        if remaining:
            self.data.append("    .zero {}".format(remaining * element_size(base)))

    def _data_directive(self, base, value):
        if base in ("float", "double"):
            return ".double {}".format(repr(float(value)))
        if base in ("char", "bool"):
            return ".byte {}".format(int(bool(value)) if base == "bool" else int(value))
        return ".quad {}".format(int(value))

    # ------------------------------------------------------------------
    # Functions
    # ------------------------------------------------------------------
    def _function(self, fn):
        mangled = fn.mangled_name or fn.name
        node = self.func_nodes.get(mangled) or self.func_nodes.get(fn.name)
        self.current_function = fn
        self.slots = {}
        self.temp_slots = {}
        self.pending_params = []
        self._label_counter = 0
        self._collect_frame(fn, node)
        self.epilogue = ".Lend_{}".format(mangled)
        label = self.func_label(mangled)
        self.text.append(".globl {}".format(label))
        self.text.append("{}:".format(label))
        self.text.append("    push rbp")
        self.text.append("    mov rbp, rsp")
        if self.frame_size:
            self.text.append("    sub rsp, {}".format(self.frame_size))
        self._copy_parameters(fn, node)
        for instr in fn.instructions:
            self._instruction(instr)
        if fn.return_type == "void" or fn.name == "main":
            self.text.append("    xor eax, eax")
        self.text.append("{}:".format(self.epilogue))
        self.text.append("    mov rsp, rbp")
        self.text.append("    pop rbp")
        self.text.append("    ret")
        self.text.append("")

    def func_label(self, name):
        if name == "main":
            return self.symbol_prefix + "main"
        return self.symbol_prefix + "userfn_" + name

    def _collect_frame(self, fn, node):
        offset = 0
        seen = set()

        def add(symbol, is_parameter=False):
            nonlocal offset
            if symbol is None or symbol.uid in seen:
                return
            seen.add(symbol.uid)
            size = storage_size(symbol.full_type, is_parameter=is_parameter or (getattr(symbol, "kind", "") == "parameter"))
            # references: pointer slot
            if getattr(symbol, "is_reference", False):
                size = 8
            offset = _align(offset, 8)
            offset += size
            self.slots[symbol.uid] = Slot(-offset, symbol.full_type, symbol)

        if node is not None:
            for param in node.params:
                if param.type_name == "void" and not param.is_array and getattr(param, "pointer_depth", 0) == 0 and not getattr(param, "is_reference", False):
                    continue
                add(getattr(param, "_symbol", None), True)
            self._walk_locals(node.body, add)

        for instr in fn.instructions:
            values = []
            if instr.dest is not None:
                values.append(instr.dest)
            values.extend(instr.args)
            for value in values:
                if value.kind == "temp" and value.text not in self.temp_slots:
                    base = base_type(value.type) or "int"
                    size = 256 if base == "string" or is_array_type(value.type) else 8
                    # pointers/ints/floats/bools 8 bytes
                    offset = _align(offset, 8)
                    offset += size
                    self.temp_slots[value.text] = Slot(-offset, value.type)
        self.frame_size = _align(offset, 16) or 16

    def _walk_locals(self, node, add):
        if node is None:
            return
        if isinstance(node, ast.Block):
            for stmt in node.statements:
                self._walk_locals(stmt, add)
        elif isinstance(node, ast.VarDecl):
            for decl in node.declarators:
                add(getattr(decl, "_symbol", None))
        elif isinstance(node, ast.If):
            self._walk_locals(node.then_branch, add)
            self._walk_locals(node.else_branch, add)
        elif isinstance(node, (ast.While, ast.DoWhile)):
            self._walk_locals(node.body, add)
        elif isinstance(node, ast.For):
            if isinstance(node.init, ast.VarDecl):
                self._walk_locals(node.init, add)
            self._walk_locals(node.body, add)
        elif isinstance(node, ast.Switch):
            for section in node.cases:
                for stmt in getattr(section, "body", []):
                    self._walk_locals(stmt, add)

    def _copy_parameters(self, fn, node):
        if node is None:
            return
        params = [p for p in node.params if not (p.type_name == "void" and not p.is_array and getattr(p, "pointer_depth", 0) == 0 and not getattr(p, "is_reference", False))]
        for index, param in enumerate(params):
            symbol = getattr(param, "_symbol", None)
            if symbol is None or symbol.uid not in self.slots:
                continue
            slot = self.slots[symbol.uid]
            src = "[rbp+{}]".format(16 + index * 8)
            if base_type(symbol.full_type) in ("float", "double") and not getattr(symbol, "is_reference", False):
                self.text.append("    movsd xmm0, QWORD PTR {}".format(src))
                self.text.append("    movsd QWORD PTR [rbp{}], xmm0".format(slot.offset))
            else:
                self.text.append("    mov rax, QWORD PTR {}".format(src))
                self.text.append("    mov QWORD PTR [rbp{}], rax".format(slot.offset))

    # ------------------------------------------------------------------
    # Instructions
    # ------------------------------------------------------------------
    def _instruction(self, instr):
        op = instr.op
        if op == "copy":
            self._copy(instr.args[0], instr.dest)
        elif op == "bin":
            self._binary(instr)
        elif op == "un":
            self._unary(instr)
        elif op == "load":
            self._array_load(instr)
        elif op == "store":
            self._array_store(instr)
        elif op == "pload":
            self._ptr_load(instr)
        elif op == "pstore":
            self._ptr_store(instr)
        elif op == "addr":
            self._addr(instr.args[0], instr.dest)
        elif op == "addr_index":
            self._addr_index(instr.args[0], instr.args[1], instr.dest)
        elif op == "param":
            self.pending_params.append(instr.args[0])
        elif op == "call":
            self._call(instr)
        elif op == "ret":
            if instr.args:
                self._load_value(instr.args[0])
            self.text.append("    jmp {}".format(self.epilogue))
        elif op == "goto":
            self.text.append("    jmp .L{}_{}".format(self.current_function.mangled_name or self.current_function.name, instr.args[0].text))
        elif op == "label":
            self.text.append(".L{}_{}:".format(self.current_function.mangled_name or self.current_function.name, instr.args[0].text))
        elif op in ("if", "ifnot"):
            self._jump(instr.args[0], ".L{}_{}".format(
                self.current_function.mangled_name or self.current_function.name, instr.args[1].text), op == "if")
        elif op == "print":
            self._print(instr.args[0])
        elif op == "print_endline":
            self._printf("\n")
        elif op == "read":
            self._read(instr.args[0])
        elif op == "read_index":
            self._read_index(instr.args[0], instr.args[1])
        elif op == "read_ptr":
            self._read_ptr(instr.args[0])
        elif op == "init_string":
            self._init_string(instr.args[0], instr.dest)

    def _copy(self, src, dest):
        if dest is None:
            return
        if base_type(dest.type) == "string":
            self._copy_string(src, dest)
            return
        self._load_value(src)
        self._store_value(dest)

    def _copy_string(self, src, dest):
        self._address(dest)
        self.text.append("    push rax")
        self._load_value(src)
        if self.is_windows:
            self.text.append("    mov rdx, rax")
            self.text.append("    pop rcx")
        else:
            self.text.append("    mov rsi, rax")
            self.text.append("    pop rdi")
        self._libc_call("strcpy")

    def _binary(self, instr):
        left, op_value, right = instr.args
        op = op_value.text
        dest = instr.dest
        float_op = (base_type(left.type) in ("float", "double") or base_type(right.type) in ("float", "double")) and op not in ("+ptr", "-ptr")
        if float_op:
            self._load_float(left)
            self.text.append("    sub rsp, 8")
            self.text.append("    movsd QWORD PTR [rsp], xmm0")
            self._load_float(right)
            self.text.append("    movapd xmm1, xmm0")
            self.text.append("    movsd xmm0, QWORD PTR [rsp]")
            self.text.append("    add rsp, 8")
            if op in ("+", "-", "*", "/"):
                mnemonic = {"+": "addsd", "-": "subsd", "*": "mulsd", "/": "divsd"}[op]
                self.text.append("    {} xmm0, xmm1".format(mnemonic))
                self._store_value(dest)
            else:
                self.text.append("    ucomisd xmm0, xmm1")
                self._set_comparison(op, is_float=True)
                self._store_value(dest)
            return
        self._load_value(left)
        self.text.append("    push rax")
        self._load_value(right)
        self.text.append("    mov rcx, rax")
        self.text.append("    pop rax")
        if op in ("+", "+ptr"):
            self.text.append("    add rax, rcx")
        elif op in ("-", "-ptr"):
            self.text.append("    sub rax, rcx")
        elif op == "*":
            self.text.append("    imul rax, rcx")
        elif op in ("/", "%"):
            self.text.append("    cqo")
            self.text.append("    idiv rcx")
            if op == "%":
                self.text.append("    mov rax, rdx")
        elif op == "<<":
            self.text.append("    shl rax, cl")
        elif op == ">>":
            self.text.append("    sar rax, cl")
        elif op == "&":
            self.text.append("    and rax, rcx")
        elif op == "|":
            self.text.append("    or rax, rcx")
        elif op == "^":
            self.text.append("    xor rax, rcx")
        elif op in ("<", ">", "<=", ">=", "==", "!="):
            self.text.append("    cmp rax, rcx")
            self._set_comparison(op)
        else:
            self.text.append("    xor eax, eax")
        self._store_value(dest)

    def _set_comparison(self, op, is_float=False):
        table = {
            "<": "setl", ">": "setg", "<=": "setle", ">=": "setge",
            "==": "sete", "!=": "setne",
        }
        float_table = {
            "<": "setb", ">": "seta", "<=": "setbe", ">=": "setae",
            "==": "sete", "!=": "setne",
        }
        mnemonic = (float_table if is_float else table)[op]
        self.text.append("    {} al".format(mnemonic))
        self.text.append("    movzx rax, al")

    def _unary(self, instr):
        op_value, value = instr.args
        op = op_value.text
        dest = instr.dest
        if base_type(value.type) in ("float", "double"):
            self._load_float(value)
            if op == "-":
                self.text.append("    mov rax, 0x8000000000000000")
                self.text.append("    movq xmm1, rax")
                self.text.append("    xorpd xmm0, xmm1")
            elif op == "!":
                self.text.append("    xorpd xmm1, xmm1")
                self.text.append("    ucomisd xmm0, xmm1")
                self.text.append("    sete al")
                self.text.append("    movzx rax, al")
                self._store_value(dest)
                return
            self._store_value(dest)
            return
        self._load_value(value)
        if op == "-":
            self.text.append("    neg rax")
        elif op == "!":
            self.text.append("    cmp rax, 0")
            self.text.append("    sete al")
            self.text.append("    movzx rax, al")
        elif op == "~":
            self.text.append("    not rax")
        self._store_value(dest)

    def _array_load(self, instr):
        array, index = instr.args
        self._address(array)
        self.text.append("    push rax")
        self._load_value(index)
        self.text.append("    mov rcx, rax")
        self.text.append("    pop rax")
        size = element_size(array.type)
        self.text.append("    imul rcx, {}".format(size))
        self.text.append("    add rax, rcx")
        base = base_type(array.type)
        if base == "char":
            self.text.append("    movsx rax, BYTE PTR [rax]")
        elif base == "bool":
            self.text.append("    movzx eax, BYTE PTR [rax]")
        elif base in ("float", "double"):
            self.text.append("    movsd xmm0, QWORD PTR [rax]")
        elif base == "string":
            pass
        else:
            self.text.append("    mov rax, QWORD PTR [rax]")
        self._store_value(instr.dest)

    def _array_store(self, instr):
        array, index, value = instr.args
        self._address(array)
        self.text.append("    push rax")
        self._load_value(index)
        self.text.append("    mov rcx, rax")
        self.text.append("    pop rax")
        size = element_size(array.type)
        self.text.append("    imul rcx, {}".format(size))
        self.text.append("    add rax, rcx")
        self.text.append("    push rax")
        self._load_value(value)
        self.text.append("    pop rcx")
        base = base_type(array.type)
        if base == "string":
            if self.is_windows:
                self.text.append("    mov rdx, rax")
            else:
                self.text.append("    mov rsi, rax")
                self.text.append("    mov rdi, rcx")
            if self.is_windows:
                self.text.append("    mov rcx, rcx")  # keep rcx as dest
            self._libc_call("strcpy")
        elif base in ("char", "bool"):
            self.text.append("    mov BYTE PTR [rcx], al")
        elif base in ("float", "double"):
            self.text.append("    movsd QWORD PTR [rcx], xmm0")
        else:
            self.text.append("    mov QWORD PTR [rcx], rax")

    def _ptr_load(self, instr):
        ptr = instr.args[0]
        self._load_value(ptr)
        # rax is address
        t = strip_top_ref(instr.dest.type or "")
        base = base_type(t)
        if base == "char":
            self.text.append("    movsx rax, BYTE PTR [rax]")
        elif base == "bool":
            self.text.append("    movzx eax, BYTE PTR [rax]")
        elif base in ("float", "double"):
            self.text.append("    movsd xmm0, QWORD PTR [rax]")
        elif base == "string":
            # pointer to string buffer
            pass
        else:
            self.text.append("    mov rax, QWORD PTR [rax]")
        self._store_value(instr.dest)

    def _ptr_store(self, instr):
        ptr, value = instr.args
        self._load_value(ptr)
        self.text.append("    push rax")
        self._load_value(value)
        self.text.append("    pop rcx")
        t = strip_top_ref(ptr.type or "")
        # if ptr is T* then store T; best effort use value type
        base = base_type(value.type or ptr.type)
        if base == "char":
            self.text.append("    mov BYTE PTR [rcx], al")
        elif base == "bool":
            self.text.append("    mov BYTE PTR [rcx], al")
        elif base in ("float", "double"):
            self.text.append("    movsd QWORD PTR [rcx], xmm0")
        elif base == "string":
            if self.is_windows:
                self.text.append("    mov rdx, rax")
                # rcx already dest
            else:
                self.text.append("    mov rsi, rax")
                self.text.append("    mov rdi, rcx")
            self._libc_call("strcpy")
        else:
            self.text.append("    mov QWORD PTR [rcx], rax")

    def _addr(self, value, dest):
        self._address(value)
        self._store_value(dest)

    def _addr_index(self, array, index, dest):
        self._address(array)
        self.text.append("    push rax")
        self._load_value(index)
        self.text.append("    mov rcx, rax")
        self.text.append("    pop rax")
        self.text.append("    imul rcx, {}".format(element_size(array.type)))
        self.text.append("    add rax, rcx")
        self._store_value(dest)

    def _jump(self, value, label, jump_if_true):
        if base_type(value.type) in ("float", "double"):
            self._load_float(value)
            self.text.append("    xorpd xmm1, xmm1")
            self.text.append("    ucomisd xmm0, xmm1")
            self.text.append("    {} {}".format("jne" if jump_if_true else "je", label))
        else:
            self._load_value(value)
            self.text.append("    cmp rax, 0")
            self.text.append("    {} {}".format("jne" if jump_if_true else "je", label))

    def _print(self, value):
        typ = base_type(value.type)
        if typ == "string":
            self._load_value(value)
            if self.is_windows:
                self.text.append("    mov rdx, rax")
                self.text.append("    lea rcx, [rip+{}]".format(self._string_label("%s")))
            else:
                self.text.append("    mov rsi, rax")
                self.text.append("    lea rdi, [rip+{}]".format(self._string_label("%s")))
            self.text.append("    xor eax, eax")
            self._libc_call("printf")
        elif typ == "char":
            self._load_value(value)
            if self.is_windows:
                self.text.append("    mov rdx, rax")
                self.text.append("    lea rcx, [rip+{}]".format(self._string_label("%c")))
            else:
                self.text.append("    mov rsi, rax")
                self.text.append("    lea rdi, [rip+{}]".format(self._string_label("%c")))
            self.text.append("    xor eax, eax")
            self._libc_call("printf")
        elif typ in ("float", "double"):
            self._load_float(value)
            if self.is_windows:
                self.text.append("    movapd xmm1, xmm0")
                self.text.append("    movq rdx, xmm0")
                self.text.append("    lea rcx, [rip+{}]".format(self._string_label("%f")))
            else:
                self.text.append("    lea rdi, [rip+{}]".format(self._string_label("%f")))
            self.text.append("    mov eax, 1")
            self._libc_call("printf")
        else:
            self._load_value(value)
            if self.is_windows:
                self.text.append("    mov rdx, rax")
                self.text.append("    lea rcx, [rip+{}]".format(self._string_label("%d")))
            else:
                self.text.append("    mov rsi, rax")
                self.text.append("    lea rdi, [rip+{}]".format(self._string_label("%d")))
            self.text.append("    xor eax, eax")
            self._libc_call("printf")

    def _printf(self, text):
        label = self._string_label(text)
        if self.is_windows:
            self.text.append("    lea rcx, [rip+{}]".format(label))
        else:
            self.text.append("    lea rdi, [rip+{}]".format(label))
        self.text.append("    xor eax, eax")
        self._libc_call("printf")

    def _read(self, value):
        self._address(value)
        typ = base_type(value.type)
        fmt = {"int": "%d", "float": "%lf", "double": "%lf", "char": " %c",
               "bool": "%d", "string": "%255s"}.get(typ, "%d")
        if typ in ("int", "char", "bool"):
            self.text.append("    mov QWORD PTR [rax], 0")
        if self.is_windows:
            self.text.append("    mov rdx, rax")
            self.text.append("    lea rcx, [rip+{}]".format(self._string_label(fmt)))
        else:
            self.text.append("    mov rsi, rax")
            self.text.append("    lea rdi, [rip+{}]".format(self._string_label(fmt)))
        self.text.append("    xor eax, eax")
        self._libc_call("scanf")

    def _read_index(self, array, index):
        self._address(array)
        self.text.append("    push rax")
        self._load_value(index)
        self.text.append("    mov rcx, rax")
        self.text.append("    pop rax")
        self.text.append("    imul rcx, {}".format(element_size(array.type)))
        self.text.append("    add rax, rcx")
        base = base_type(array.type)
        fmt = {"int": "%d", "float": "%lf", "double": "%lf", "char": " %c",
               "bool": "%d", "string": "%255s"}.get(base, "%d")
        if base in ("int", "char", "bool"):
            self.text.append("    mov QWORD PTR [rax], 0")
        if self.is_windows:
            self.text.append("    mov rdx, rax")
            self.text.append("    lea rcx, [rip+{}]".format(self._string_label(fmt)))
        else:
            self.text.append("    mov rsi, rax")
            self.text.append("    lea rdi, [rip+{}]".format(self._string_label(fmt)))
        self.text.append("    xor eax, eax")
        self._libc_call("scanf")

    def _read_ptr(self, ptr):
        self._load_value(ptr)
        # rax address to read into; infer type from ptr type
        t = strip_top_ref(ptr.type or "")
        base = base_type(t[:-1]) if t.endswith("*") else base_type(t)
        fmt = {"int": "%d", "float": "%lf", "double": "%lf", "char": " %c",
               "bool": "%d", "string": "%255s"}.get(base, "%d")
        if base in ("int", "char", "bool"):
            self.text.append("    mov QWORD PTR [rax], 0")
        if self.is_windows:
            self.text.append("    mov rdx, rax")
            self.text.append("    lea rcx, [rip+{}]".format(self._string_label(fmt)))
        else:
            self.text.append("    mov rsi, rax")
            self.text.append("    lea rdi, [rip+{}]".format(self._string_label(fmt)))
        self.text.append("    xor eax, eax")
        self._libc_call("scanf")

    def _init_string(self, value, dest):
        if dest is None:
            return
        self._address(dest)
        self.text.append("    push rax")
        self._load_value(value)
        if self.is_windows:
            self.text.append("    mov rdx, rax")
            self.text.append("    pop rcx")
        else:
            self.text.append("    mov rsi, rax")
            self.text.append("    pop rdi")
        self._libc_call("strcpy")

    def _call(self, instr):
        name = instr.args[0].value if instr.args[0].value is not None else instr.args[0].text
        args = self.pending_params
        self.pending_params = []
        pad = 8 if len(args) % 2 == 1 else 0
        if pad:
            self.text.append("    sub rsp, 8")
        for arg in reversed(args):
            if base_type(arg.type) in ("float", "double"):
                self._load_float(arg)
                self.text.append("    sub rsp, 8")
                self.text.append("    movsd QWORD PTR [rsp], xmm0")
            else:
                self._load_value(arg)
                self.text.append("    push rax")
        self.text.append("    call {}".format(self.func_label(name)))
        cleanup = pad + 8 * len(args)
        if cleanup:
            self.text.append("    add rsp, {}".format(cleanup))
        if instr.dest is not None:
            self._store_value(instr.dest)

    def _libc_call(self, name):
        symbol = self.symbol_prefix + name
        if self.is_windows:
            self.text.append("    sub rsp, 32")
            self.text.append("    call {}".format(symbol))
            self.text.append("    add rsp, 32")
        else:
            self.text.append("    call {}".format(symbol))

    # ------------------------------------------------------------------
    # Values and addresses
    # ------------------------------------------------------------------
    def _global_label(self, value):
        if value is not None and value.symbol is not None and getattr(
                value.symbol, "is_global", False):
            return self._global_labels.get(value.symbol.uid)
        return None

    def _load_value(self, value):
        if value is None:
            self.text.append("    xor eax, eax")
            return
        if value.kind == "const":
            typ = base_type(value.type)
            if typ == "string":
                self.text.append("    lea rax, [rip+{}]".format(
                    self._string_label(value.value if value.value is not None else value.text)))
            elif typ in ("float", "double"):
                self._load_float(value)
            elif typ == "bool":
                self.text.append("    mov rax, {}".format(1 if value.value else 0))
            elif typ == "char":
                self.text.append("    mov rax, {}".format(int(value.value or 0)))
            else:
                self.text.append("    mov rax, {}".format(int(value.value or 0)))
            return
        if value.kind == "builtin":
            self.text.append("    xor eax, eax")
            return
        global_label = self._global_label(value)
        if global_label is not None:
            typ = base_type(value.type)
            t = strip_top_ref(value.type or "")
            if is_array_type(t) or typ == "string":
                self.text.append("    lea rax, [rip+{}]".format(global_label))
            elif typ in ("float", "double"):
                self.text.append("    movsd xmm0, QWORD PTR [rip+{}]".format(global_label))
            elif typ == "char":
                self.text.append("    movsx rax, BYTE PTR [rip+{}]".format(global_label))
            elif typ == "bool":
                self.text.append("    movzx eax, BYTE PTR [rip+{}]".format(global_label))
            else:
                self.text.append("    mov rax, QWORD PTR [rip+{}]".format(global_label))
            return
        slot = self._slot_for(value)
        if slot is None:
            self.text.append("    xor eax, eax")
            return
        typ = base_type(value.type)
        t = strip_top_ref(value.type or "")
        # array params decay to pointer stored in slot
        if is_array_type(t) or typ == "string":
            if value.symbol is not None and (value.symbol.kind == "parameter" or getattr(value.symbol, "is_reference", False)):
                self.text.append("    mov rax, QWORD PTR [rbp{}]".format(slot.offset))
            else:
                self.text.append("    lea rax, [rbp{}]".format(slot.offset))
        elif typ in ("float", "double"):
            self.text.append("    movsd xmm0, QWORD PTR [rbp{}]".format(slot.offset))
        elif typ == "char":
            self.text.append("    movsx rax, BYTE PTR [rbp{}]".format(slot.offset))
        elif typ == "bool":
            self.text.append("    movzx eax, BYTE PTR [rbp{}]".format(slot.offset))
        else:
            self.text.append("    mov rax, QWORD PTR [rbp{}]".format(slot.offset))

    def _load_float(self, value):
        if value.kind == "const":
            if value.type in ("float", "double"):
                self.text.append("    movsd xmm0, QWORD PTR [rip+{}]".format(
                    self._float_label(value.value if value.value is not None else 0.0)))
            else:
                self.text.append("    mov rax, {}".format(int(value.value or 0)))
                self.text.append("    cvtsi2sd xmm0, rax")
            return
        global_label = self._global_label(value)
        if global_label is not None:
            if base_type(value.type) in ("float", "double"):
                self.text.append("    movsd xmm0, QWORD PTR [rip+{}]".format(global_label))
            else:
                self.text.append("    mov rax, QWORD PTR [rip+{}]".format(global_label))
                self.text.append("    cvtsi2sd xmm0, rax")
            return
        slot = self._slot_for(value)
        typ = base_type(value.type)
        if slot is None:
            self.text.append("    xorpd xmm0, xmm0")
        elif typ in ("float", "double"):
            self.text.append("    movsd xmm0, QWORD PTR [rbp{}]".format(slot.offset))
        else:
            self.text.append("    mov rax, QWORD PTR [rbp{}]".format(slot.offset))
            self.text.append("    cvtsi2sd xmm0, rax")

    def _store_value(self, dest):
        if dest is None:
            return
        global_label = self._global_label(dest)
        typ = base_type(dest.type)
        if global_label is not None:
            if typ in ("float", "double"):
                self.text.append("    movsd QWORD PTR [rip+{}], xmm0".format(global_label))
            elif typ in ("char", "bool"):
                self.text.append("    mov BYTE PTR [rip+{}], al".format(global_label))
            else:
                self.text.append("    mov QWORD PTR [rip+{}], rax".format(global_label))
            return
        slot = self._slot_for(dest)
        if slot is None:
            return
        if typ in ("float", "double"):
            self.text.append("    movsd QWORD PTR [rbp{}], xmm0".format(slot.offset))
        elif typ in ("char", "bool"):
            self.text.append("    mov BYTE PTR [rbp{}], al".format(slot.offset))
        else:
            self.text.append("    mov QWORD PTR [rbp{}], rax".format(slot.offset))

    def _address(self, value):
        if value.kind == "const" and base_type(value.type) == "string":
            self.text.append("    lea rax, [rip+{}]".format(
                self._string_label(value.value if value.value is not None else value.text)))
            return
        global_label = self._global_label(value)
        if global_label is not None:
            self.text.append("    lea rax, [rip+{}]".format(global_label))
            return
        slot = self._slot_for(value)
        if slot is None:
            self.text.append("    xor eax, eax")
            return
        t = strip_top_ref(value.type or "")
        # array parameter and reference parameter store pointer value in slot
        if value.symbol is not None and value.symbol.kind == "parameter" and (
                is_array_type(t) or base_type(t) == "string" or getattr(value.symbol, "is_reference", False)):
            self.text.append("    mov rax, QWORD PTR [rbp{}]".format(slot.offset))
        elif getattr(value, 'kind', None) == 'temp' and is_array_type(t):
            self.text.append("    lea rax, [rbp{}]".format(slot.offset))
        elif value.symbol is not None and is_array_type(t):
            self.text.append("    lea rax, [rbp{}]".format(slot.offset))
        else:
            self.text.append("    lea rax, [rbp{}]".format(slot.offset))

    def _slot_for(self, value):
        if value.symbol is not None:
            if getattr(value.symbol, "is_global", False):
                return self._global_slot(value.symbol)
            return self.slots.get(value.symbol.uid)
        if value.kind == "temp":
            return self.temp_slots.get(value.text)
        return None

    def _global_slot(self, symbol):
        return _GlobalSlot(self._global_labels.get(symbol.uid, "g_{}_{}".format(symbol.uid, symbol.name)), symbol.full_type)


class _GlobalSlot:
    def __init__(self, label, type_name):
        self.label = label
        self.type = type_name
        self.offset = 0
        self.is_global = True
