"""
Recursive descent parser for the C++ subset.

Each grammar rule in README.md has a matching parse_<rule> method.
Expressions use one method per precedence level, lowest precedence first,
so precedence and associativity come straight from the call structure.

Error handling: a rule raises ParseError on the first unexpected token.
The error is caught at a statement (or top-level declaration) boundary,
recorded, and the parser skips ahead to a safe point (panic mode), so one
run can report several errors.
"""
from dataclasses import dataclass, field
from typing import List, Optional

from . import ast_nodes as ast
from .errors import ParseError
from .tokens import (
    CHAR_LITERAL, EOF, FLOAT_LITERAL, IDENTIFIER, INTEGER_LITERAL,
    PREPROCESSOR, STRING_LITERAL, STRING_TYPE, TYPE_KEYWORDS,
    describe, is_identifier, is_keyword, is_symbol, lex,
)

ASSIGN_OPS = ("=", "+=", "-=", "*=", "/=", "%=", "<<=", ">>=", "&=", "|=", "^=")
PREFIX_OPS = ("-", "+", "!", "~", "++", "--", "*", "&")
POSTFIX_OPS = ("++", "--")

LITERAL_KINDS = {
    INTEGER_LITERAL: "int",
    FLOAT_LITERAL: "float",
    STRING_LITERAL: "string",
    CHAR_LITERAL: "char",
}

# Keywords that start a statement; panic mode stops in front of them.
STATEMENT_KEYWORDS = {"if", "while", "do", "for", "return", "break", "continue"}

# Real C++ keywords that are outside our subset, reported with a clear message.
UNSUPPORTED_KEYWORDS = {
    "class", "struct", "union", "enum", "template", "typename", "namespace",
    "goto", "try", "catch", "throw", "new",
    "delete", "operator", "typedef", "auto", "constexpr", "static",
    "extern", "long", "short", "signed", "unsigned", "nullptr", "this",
    "and", "or", "not", "sizeof", "virtual", "public", "private", "protected",
}


@dataclass
class ParseResult:
    ast: Optional[ast.Program]
    lexical_errors: List[str] = field(default_factory=list)
    syntax_errors: List[ParseError] = field(default_factory=list)

    @property
    def ok(self):
        return not self.lexical_errors and not self.syntax_errors


def parse_source(source):
    """Lex and parse source text.

    If the lexer reports errors, parsing is skipped (ast is None).
    If there are syntax errors, ast holds whatever could be parsed; later
    phases should only run when result.ok is True.
    """
    tokens, lexical_errors = lex(source)
    if lexical_errors:
        return ParseResult(None, lexical_errors, [])
    parser = Parser(tokens)
    program = parser.parse_program()
    return ParseResult(program, [], parser.errors)


class Parser:
    def __init__(self, tokens):
        # tokens must end with an EOF token (tokens.lex() guarantees this)
        self.tokens = tokens
        self.pos = 0
        self.errors = []

    # ------------------------------------------------------------
    # Token helpers
    # ------------------------------------------------------------
    @property
    def current(self):
        return self.tokens[self.pos]

    def peek(self, offset=1):
        return self.tokens[min(self.pos + offset, len(self.tokens) - 1)]

    def at_eof(self):
        return self.current.kind == EOF

    def advance(self):
        tok = self.current
        if tok.kind != EOF:
            self.pos += 1
        return tok

    def expect_symbol(self, text):
        if is_symbol(self.current, text):
            return self.advance()
        raise self.error_expected("'{}'".format(text), after_previous=True)

    def expect_keyword(self, text):
        if is_keyword(self.current, text):
            return self.advance()
        raise self.error_expected("'{}'".format(text))

    def expect_identifier(self):
        if self.current.kind == IDENTIFIER:
            return self.advance()
        raise self.error_expected("identifier")

    def error_expected(self, what, after_previous=False):
        """Build "expected X but found Y".

        With after_previous (used for missing symbols such as ';' or ')'):
        if the unexpected token is on a later line than the previous token,
        the error points just after the previous token, like gcc/clang do,
        because that is where the symbol was forgotten.
        """
        found = self.current
        line, col = found.line, found.col
        if after_previous and self.pos > 0:
            prev = self.tokens[self.pos - 1]
            if found.line > prev.line:
                line, col = prev.line, prev.col + len(prev.text)
        return ParseError("expected {} but found {}".format(what, describe(found)), line, col)

    def error_here(self, message):
        return ParseError(message, self.current.line, self.current.col)

    def report(self, error):
        # Ignore a second error at the exact same place (cascade from recovery).
        if self.errors and (self.errors[-1].line, self.errors[-1].col) == (error.line, error.col):
            return
        self.errors.append(error)

    def check_unsupported(self):
        tok = self.current
        if is_keyword(tok, *UNSUPPORTED_KEYWORDS):
            raise self.error_here("'{}' is not supported in this C++ subset".format(tok.text))

    # ------------------------------------------------------------
    # Panic-mode recovery
    # ------------------------------------------------------------
    def sync_statement(self):
        """Skip to the end of the broken statement.

        Stops after a ';', after a whole '{ ... }' block (e.g. the body of a
        broken 'if (x > y {'), or in front of '}', a statement keyword, a
        type name, or end of file.
        """
        while not self.at_eof():
            tok = self.current
            if is_symbol(tok, ";"):
                self.advance()
                return
            if is_symbol(tok, "{"):
                self.skip_braces()
                return
            if is_symbol(tok, "}") or is_keyword(tok, *STATEMENT_KEYWORDS) or self.at_type():
                return
            self.advance()

    def sync_top_level(self, error_line):
        """Skip to the end of the broken top-level declaration.

        Stops after a ';', after a whole '{ ... }' body (plus a following
        ';'), in front of a
        preprocessor line or 'using', or in front of a type name that starts
        a later line than the error (the next declaration).
        """
        while not self.at_eof():
            tok = self.current
            if is_symbol(tok, ";"):
                self.advance()
                return
            if is_symbol(tok, "{"):
                self.skip_braces()
                if is_symbol(self.current, ";"):   # class Foo { ... };
                    self.advance()
                return
            if tok.kind == PREPROCESSOR or is_keyword(tok, "using"):
                return
            if self.at_type() and tok.line > error_line:
                return
            self.advance()

    def skip_braces(self):
        depth = 0
        while not self.at_eof():
            tok = self.advance()
            if is_symbol(tok, "{"):
                depth += 1
            elif is_symbol(tok, "}"):
                depth -= 1
                if depth == 0:
                    return

    # ------------------------------------------------------------
    # Types
    # ------------------------------------------------------------
    def _is_base_type_token(self, offset=0):
        tok = self.peek(offset)
        if is_keyword(tok, *TYPE_KEYWORDS) or is_identifier(tok, STRING_TYPE):
            return True
        return (is_identifier(tok, "std")
                and is_symbol(self.peek(offset + 1), "::")
                and is_identifier(self.peek(offset + 2), STRING_TYPE))

    def at_type(self, offset=0):
        """True if a type name (optionally prefixed by const) starts at current + offset."""
        if is_keyword(self.peek(offset), "const"):
            return self._is_base_type_token(offset + 1)
        return self._is_base_type_token(offset)

    def parse_type(self):
        """Parse a base type name and return (type_name, is_const)."""
        is_const = False
        if is_keyword(self.current, "const"):
            self.advance()
            is_const = True
        if not self._is_base_type_token():
            raise self.error_expected("a type name")
        tok = self.advance()
        if is_identifier(tok, "std"):
            self.advance()   # ::
            self.advance()   # string
        type_name = "string" if tok.kind == IDENTIFIER else tok.text
        return type_name, is_const

    # ------------------------------------------------------------
    # Program and top-level declarations
    # ------------------------------------------------------------
    def parse_program(self):
        """program = { preprocessor | using_dir | external_decl } EOF ;"""
        program = ast.Program()
        while not self.at_eof():
            start = self.pos
            try:
                program.items.append(self.parse_external())
            except ParseError as e:
                self.report(e)
                if self.pos == start:
                    self.advance()   # always make progress
                self.sync_top_level(e.line)
        return program

    def parse_external(self):
        tok = self.current
        if tok.kind == PREPROCESSOR:
            self.advance()
            return ast.Preprocessor(tok.text, tok.line, tok.col)
        if is_keyword(tok, "using"):
            return self.parse_using()
        self.check_unsupported()
        if not self.at_type():
            raise self.error_expected("a declaration")
        type_name, is_const = self.parse_type()
        # Function declarator: [*...] name '(' ... ')'  vs var declarator
        offset = 0
        while is_symbol(self.peek(offset), "*"):
            offset += 1
        if self.peek(offset).kind == IDENTIFIER and is_symbol(self.peek(offset + 1), "("):
            # Consume pointer stars (function pointers not modeled; keep simple)
            while is_symbol(self.current, "*"):
                self.advance()
            name = self.expect_identifier()
            if is_symbol(self.current, "("):
                return self.parse_function_rest(type_name, is_const, name, tok)
            decl = ast.VarDecl(type_name, [], is_const, tok.line, tok.col)
            decl.declarators.append(self.parse_declarator_tail(name, is_const))
            self.parse_more_declarators(decl, is_const)
            return decl
        decl = ast.VarDecl(type_name, [], is_const, tok.line, tok.col)
        decl.declarators.append(self.parse_declarator(is_const))
        self.parse_more_declarators(decl, is_const)
        return decl

    def parse_using(self):
        """using_dir = "using" "namespace" "std" ";" ;"""
        tok = self.expect_keyword("using")
        self.expect_keyword("namespace")
        if not is_identifier(self.current, "std"):
            raise self.error_expected("'std' (only 'using namespace std;' is supported)")
        self.advance()
        self.expect_symbol(";")
        return ast.UsingDirective("std", tok.line, tok.col)

    def parse_function_rest(self, return_type, return_is_const, name, start):
        """func_rest = "(" [ params ] ")" ( block | ";" ) ;"""
        func = ast.FunctionDecl(return_type, name.text, [], None, line=start.line, col=start.col)
        # Return-type const is parsed/accepted but not modeled separately yet in FunctionDecl.
        self.expect_symbol("(")
        func.params = self.parse_params()
        self.expect_symbol(")")
        if is_symbol(self.current, ";"):
            self.advance()
        elif is_symbol(self.current, "{"):
            func.body = self.parse_block()
        else:
            raise self.error_expected("'{' or ';'")
        return func

    def parse_params(self):
        """params = "void" | param { "," param } ;"""
        if is_symbol(self.current, ")"):
            return []
        if is_keyword(self.current, "void") and is_symbol(self.peek(), ")"):
            self.advance()
            return []
        params = [self.parse_param()]
        while is_symbol(self.current, ","):
            self.advance()
            params.append(self.parse_param())
        return params

    def parse_param(self):
        """param = [ "const" ] type [ "*" ... ] [ "&" ] [ IDENT ] [ "[" [ INTEGER_LITERAL ] "]" ] ;

        The name is optional, as in C++ prototypes like "int f(int);".
        """
        tok = self.current
        type_name, is_const = self.parse_type()
        pointer_depth = 0
        while is_symbol(self.current, "*"):
            self.advance()
            pointer_depth += 1
            # Optional const after * not modeled yet.
        is_reference = False
        if is_symbol(self.current, "&"):
            self.advance()
            is_reference = True
        name = None
        if self.current.kind == IDENTIFIER:
            name = self.advance().text
        is_array = False
        if is_symbol(self.current, "["):
            self.advance()
            if self.current.kind == INTEGER_LITERAL:
                self.advance()
            self.expect_symbol("]")
            is_array = True
        return ast.Param(type_name, name, is_array, is_const, pointer_depth, is_reference, tok.line, tok.col)

    # ------------------------------------------------------------
    # Variable declarations
    # ------------------------------------------------------------
    def parse_var_decl(self):
        """var_decl = [ "const" ] type declarator { "," declarator } ";" ;"""
        tok = self.current
        type_name, is_const = self.parse_type()
        decl = ast.VarDecl(type_name, [], is_const, tok.line, tok.col)
        decl.declarators.append(self.parse_declarator(is_const))
        self.parse_more_declarators(decl, is_const)
        return decl

    def parse_more_declarators(self, decl, is_const=False):
        while is_symbol(self.current, ","):
            self.advance()
            decl.declarators.append(self.parse_declarator(is_const))
        self.expect_symbol(";")

    def parse_declarator(self, is_const=False):
        pointer_depth = 0
        while is_symbol(self.current, "*"):
            self.advance()
            pointer_depth += 1
        name = self.expect_identifier()
        d = ast.Declarator(name.text, False, None, None, is_const, pointer_depth, name.line, name.col)
        if is_symbol(self.current, "["):
            self.advance()
            d.is_array = True
            if not is_symbol(self.current, "]"):
                d.array_size = self.parse_expression()
            self.expect_symbol("]")
        if is_symbol(self.current, "="):
            self.advance()
            d.init = self.parse_initializer()
        return d

    def parse_declarator_tail(self, name, is_const=False, pointer_depth=0):
        """declarator tail after IDENT already consumed."""
        d = ast.Declarator(name.text, False, None, None, is_const, pointer_depth, name.line, name.col)
        if is_symbol(self.current, "["):
            self.advance()
            d.is_array = True
            if not is_symbol(self.current, "]"):
                d.array_size = self.parse_expression()
            self.expect_symbol("]")
        if is_symbol(self.current, "="):
            self.advance()
            d.init = self.parse_initializer()
        return d

    def parse_initializer(self):
        """initializer = assignment | "{" [ assignment { "," assignment } ] "}" ;"""
        if not is_symbol(self.current, "{"):
            return self.parse_assignment()
        tok = self.advance()
        init = ast.InitList([], tok.line, tok.col)
        if not is_symbol(self.current, "}"):
            init.elements.append(self.parse_assignment())
            while is_symbol(self.current, ","):
                self.advance()
                init.elements.append(self.parse_assignment())
        self.expect_symbol("}")
        return init

    # ------------------------------------------------------------
    # Statements
    # ------------------------------------------------------------
    def parse_block(self):
        """block = "{" { statement } "}" ;"""
        open_tok = self.expect_symbol("{")
        block = ast.Block([], open_tok.line, open_tok.col)
        while not is_symbol(self.current, "}") and not self.at_eof():
            start = self.pos
            try:
                block.statements.append(self.parse_statement())
            except ParseError as e:
                self.report(e)
                if self.pos == start:
                    self.advance()   # always make progress
                self.sync_statement()
        if self.at_eof():
            raise ParseError(
                "expected '}}' to close the block opened at line {}, column {} "
                "but found end of file".format(
                    open_tok.line, open_tok.col),
                self.current.line, self.current.col)
        self.advance()   # }
        return block

    def parse_statement(self):
        tok = self.current
        if is_symbol(tok, "{"):
            return self.parse_block()
        if is_symbol(tok, ";"):
            self.advance()
            return ast.EmptyStmt(tok.line, tok.col)
        if tok.kind == PREPROCESSOR:
            self.advance()
            return ast.Preprocessor(tok.text, tok.line, tok.col)
        if is_keyword(tok, "using"):
            return self.parse_using()
        if is_keyword(tok, "if"):
            return self.parse_if()
        if is_keyword(tok, "while"):
            return self.parse_while()
        if is_keyword(tok, "do"):
            return self.parse_do_while()
        if is_keyword(tok, "for"):
            return self.parse_for()
        if is_keyword(tok, "switch"):
            return self.parse_switch()
        if is_keyword(tok, "case"):
            raise self.error_here("'case' outside of switch")
        if is_keyword(tok, "default"):
            raise self.error_here("'default' outside of switch")
        if is_keyword(tok, "return"):
            return self.parse_return()
        if is_keyword(tok, "break"):
            self.advance()
            self.expect_symbol(";")
            return ast.Break(tok.line, tok.col)
        if is_keyword(tok, "continue"):
            self.advance()
            self.expect_symbol(";")
            return ast.Continue(tok.line, tok.col)
        if is_keyword(tok, "else"):
            raise self.error_here("'else' without a matching 'if'")
        self.check_unsupported()
        if self.at_type():
            return self.parse_var_decl()
        expr = self.parse_expression()
        self.expect_symbol(";")
        return ast.ExprStmt(expr, tok.line, tok.col)

    def parse_condition(self):
        """"(" expr ")" as used by if / while / do-while."""
        self.expect_symbol("(")
        cond = self.parse_expression()
        self.expect_symbol(")")
        return cond

    def parse_if(self):
        """if_stmt = "if" "(" expr ")" statement [ "else" statement ] ;

        A dangling 'else' binds to the nearest 'if', as in C++.
        """
        tok = self.advance()
        cond = self.parse_condition()
        then_branch = self.parse_statement()
        else_branch = None
        if is_keyword(self.current, "else"):
            self.advance()
            else_branch = self.parse_statement()
        return ast.If(cond, then_branch, else_branch, tok.line, tok.col)

    def parse_while(self):
        """while_stmt = "while" "(" expr ")" statement ;"""
        tok = self.advance()
        cond = self.parse_condition()
        return ast.While(cond, self.parse_statement(), tok.line, tok.col)

    def parse_do_while(self):
        """do_stmt = "do" statement "while" "(" expr ")" ";" ;"""
        tok = self.advance()
        body = self.parse_statement()
        self.expect_keyword("while")
        cond = self.parse_condition()
        self.expect_symbol(";")
        return ast.DoWhile(body, cond, tok.line, tok.col)

    def parse_for(self):
        """for_stmt = "for" "(" ( var_decl | [ expr ] ";" ) [ expr ] ";" [ expr ] ")" statement ;"""
        tok = self.advance()
        self.expect_symbol("(")
        init = None
        if self.at_type():
            init = self.parse_var_decl()        # consumes the ';'
        else:
            if not is_symbol(self.current, ";"):
                init = self.parse_expression()
            self.expect_symbol(";")
        cond = None
        if not is_symbol(self.current, ";"):
            cond = self.parse_expression()
        self.expect_symbol(";")
        update = None
        if not is_symbol(self.current, ")"):
            update = self.parse_expression()
        self.expect_symbol(")")
        body = self.parse_statement()
        return ast.For(init, cond, update, body, tok.line, tok.col)

    def parse_switch(self):
        """switch_stmt = "switch" "(" expr ")" "{" { case_label | default_label | statement } "}" ;"""
        tok = self.advance()  # switch
        expr = self.parse_condition()
        self.expect_symbol("{")
        cases = []
        current = None  # active Case/Default collecting statements
        while not is_symbol(self.current, "}") and not self.at_eof():
            if is_keyword(self.current, "case"):
                ctok = self.advance()
                value = self.parse_expression()
                self.expect_symbol(":")
                current = ast.Case(value, [], ctok.line, ctok.col)
                cases.append(current)
                continue
            if is_keyword(self.current, "default"):
                dtok = self.advance()
                self.expect_symbol(":")
                current = ast.Default([], dtok.line, dtok.col)
                cases.append(current)
                continue
            # Statements before first label: parse but attach to a synthetic section-less
            # container only if needed; simpler to require labels/allow by creating
            # an implicit leading section is complex. For subset, collect into current
            # if present; otherwise parse and discard? Better: if no current, create
            # a dummy default-like behavior is wrong. We'll attach to a temporary
            # prelude list stored on switch? Not in AST. Instead, if no current,
            # start a synthetic Case-like container isn't typed. Practical: treat as
            # statement in current if exists else raise.
            if current is None:
                raise self.error_here("statement before first 'case' or 'default' in switch")
            start = self.pos
            try:
                current.body.append(self.parse_statement())
            except ParseError as e:
                self.report(e)
                if self.pos == start:
                    self.advance()
                self.sync_statement()
        if self.at_eof():
            raise ParseError(
                "expected '}' to close switch body but found end of file",
                self.current.line, self.current.col)
        self.advance()  # }
        return ast.Switch(expr, cases, tok.line, tok.col)

    def parse_return(self):
        """"return" [ expr ] ";" """
        tok = self.advance()
        value = None
        if not is_symbol(self.current, ";"):
            value = self.parse_expression()
        self.expect_symbol(";")
        return ast.Return(value, tok.line, tok.col)

    # ------------------------------------------------------------
    # Expressions, lowest precedence first
    # ------------------------------------------------------------
    def parse_expression(self):
        """expr = assignment ;"""
        return self.parse_assignment()

    def parse_assignment(self):
        """assignment = ternary [ assign_op assignment ] ;   (right-associative)"""
        left = self.parse_ternary()
        if is_symbol(self.current, *ASSIGN_OPS):
            op = self.advance()
            if not isinstance(left, (ast.Identifier, ast.Index, ast.Deref)):
                raise ParseError(
                    "left side of '{}' must be a variable or array element".format(op.text),
                    op.line, op.col)
            value = self.parse_assignment()
            return ast.Assign(op.text, left, value, op.line, op.col)
        return left

    def parse_ternary(self):
        """ternary = logical_or [ "?" expression ":" ternary ] ;   (right-associative)"""
        cond = self.parse_logical_or()
        if is_symbol(self.current, "?"):
            q = self.advance()
            when_true = self.parse_expression()
            self.expect_symbol(":")
            when_false = self.parse_ternary()
            return ast.Ternary(cond, when_true, when_false, q.line, q.col)
        return cond

    def parse_left_assoc(self, operand, ops):
        """operand { op operand }, building a left-leaning tree."""
        left = operand()
        while is_symbol(self.current, *ops):
            op = self.advance()
            right = operand()
            left = ast.Binary(op.text, left, right, op.line, op.col)
        return left

    def parse_logical_or(self):
        """logical_or = logical_and { "||" logical_and } ;"""
        return self.parse_left_assoc(self.parse_logical_and, ("||",))

    def parse_logical_and(self):
        """logical_and = bitwise_or { "&&" bitwise_or } ;"""
        return self.parse_left_assoc(self.parse_bitwise_or, ("&&",))

    def parse_bitwise_or(self):
        """bitwise_or = bitwise_xor { "|" bitwise_xor } ;"""
        return self.parse_left_assoc(self.parse_bitwise_xor, ("|",))

    def parse_bitwise_xor(self):
        """bitwise_xor = bitwise_and { "^" bitwise_and } ;"""
        return self.parse_left_assoc(self.parse_bitwise_and, ("^",))

    def parse_bitwise_and(self):
        """bitwise_and = equality { "&" equality } ;"""
        return self.parse_left_assoc(self.parse_equality, ("&",))

    def parse_equality(self):
        """equality = relational { ("==" | "!=") relational } ;"""
        return self.parse_left_assoc(self.parse_relational, ("==", "!="))

    def parse_relational(self):
        """relational = shift { ("<" | ">" | "<=" | ">=") shift } ;"""
        return self.parse_left_assoc(self.parse_shift, ("<", ">", "<=", ">="))

    def parse_shift(self):
        """shift = additive { ("<<" | ">>") additive } ;   (cout << / cin >>)"""
        return self.parse_left_assoc(self.parse_additive, ("<<", ">>"))

    def parse_additive(self):
        """additive = multiplicative { ("+" | "-") multiplicative } ;"""
        return self.parse_left_assoc(self.parse_multiplicative, ("+", "-"))

    def parse_multiplicative(self):
        """multiplicative = unary { ("*" | "/" | "%") unary } ;"""
        return self.parse_left_assoc(self.parse_unary, ("*", "/", "%"))

    def parse_unary(self):
        """unary = ("-" | "+" | "!" | "~" | "++" | "--" | "*" | "&") unary | postfix ;"""
        if is_symbol(self.current, *PREFIX_OPS):
            op = self.advance()
            operand = self.parse_unary()
            if op.text == "&":
                return ast.AddressOf(operand, op.line, op.col)
            if op.text == "*":
                return ast.Deref(operand, op.line, op.col)
            return ast.Unary(op.text, operand, op.line, op.col)
        return self.parse_postfix()

    def parse_postfix(self):
        """postfix = primary { "(" [ args ] ")" | "[" expr "]" | "++" | "--" } ;"""
        expr = self.parse_primary()
        while True:
            tok = self.current
            if is_symbol(tok, "("):
                self.advance()
                expr = ast.Call(expr, self.parse_args(), tok.line, tok.col)
                self.expect_symbol(")")
            elif is_symbol(tok, "["):
                self.advance()
                index = self.parse_expression()
                self.expect_symbol("]")
                expr = ast.Index(expr, index, tok.line, tok.col)
            elif is_symbol(tok, *POSTFIX_OPS):
                self.advance()
                expr = ast.Postfix(tok.text, expr, tok.line, tok.col)
            else:
                return expr

    def parse_args(self):
        """args = assignment { "," assignment } ;"""
        if is_symbol(self.current, ")"):
            return []
        args = [self.parse_assignment()]
        while is_symbol(self.current, ","):
            self.advance()
            args.append(self.parse_assignment())
        return args

    def parse_primary(self):
        """primary = literal | "true" | "false" | IDENT | "std" "::" IDENT | "(" expr ")" ;"""
        tok = self.current
        if tok.kind in LITERAL_KINDS:
            self.advance()
            return ast.Literal(LITERAL_KINDS[tok.kind], tok.text, tok.line, tok.col)
        if is_keyword(tok, "true", "false"):
            self.advance()
            return ast.Literal("bool", tok.text, tok.line, tok.col)
        if tok.kind == IDENTIFIER:
            self.advance()
            if is_symbol(self.current, "::"):
                if tok.text != "std":
                    raise self.error_here("only 'std::' qualified names are supported")
                self.advance()
                name = self.expect_identifier()
                return ast.Identifier(name.text, "std", tok.line, tok.col)
            return ast.Identifier(tok.text, None, tok.line, tok.col)
        if is_symbol(tok, "("):
            self.advance()
            expr = self.parse_expression()
            self.expect_symbol(")")
            return expr
        self.check_unsupported()
        raise self.error_expected("an expression")
