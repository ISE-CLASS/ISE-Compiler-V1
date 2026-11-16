"""
Tests for the syntax analysis phase.

Run from the repository root:
    python -m unittest discover -s tests -v
"""
import os
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from syntax_analysis import ast_nodes as ast          # noqa: E402
from syntax_analysis import format_ast, parse_source  # noqa: E402
from syntax_analysis.tokens import EOF, lex           # noqa: E402


def show(e):
    """Fully parenthesized form of an expression, to check tree shape."""
    if isinstance(e, ast.Binary):
        return "({} {} {})".format(show(e.left), e.op, show(e.right))
    if isinstance(e, ast.Assign):
        return "({} {} {})".format(show(e.target), e.op, show(e.value))
    if isinstance(e, ast.Unary):
        return "({}{})".format(e.op, show(e.operand))
    if isinstance(e, ast.Postfix):
        return "({}{})".format(show(e.operand), e.op)
    if isinstance(e, ast.Call):
        return "{}({})".format(show(e.callee), ", ".join(show(a) for a in e.args))
    if isinstance(e, ast.Index):
        return "{}[{}]".format(show(e.array), show(e.index))
    if isinstance(e, ast.Identifier):
        return e.name if e.namespace is None else e.namespace + "::" + e.name
    if isinstance(e, ast.Literal):
        return e.value
    raise TypeError(type(e).__name__)


class ParserTestCase(unittest.TestCase):
    def parse_ok(self, src):
        result = parse_source(src)
        self.assertEqual(result.lexical_errors, [])
        self.assertEqual([str(e) for e in result.syntax_errors], [])
        return result.ast

    def body(self, stmts):
        """Parse statements inside main() and return the list of statements."""
        program = self.parse_ok("int main() {\n" + stmts + "\n}")
        return program.items[0].body.statements

    def expr(self, text):
        """Parse a single expression statement and return the expression."""
        stmt = self.body(text + ";")[0]
        self.assertIsInstance(stmt, ast.ExprStmt)
        return stmt.expr

    def errors(self, src):
        result = parse_source(src)
        self.assertEqual(result.lexical_errors, [])
        return [str(e) for e in result.syntax_errors]


# ---------------------------------------------------------------
# Lexer adapter
# ---------------------------------------------------------------
class TestTokenAdapter(unittest.TestCase):
    def test_eof_is_appended(self):
        tokens, errors = lex("int x;")
        self.assertEqual(errors, [])
        self.assertEqual([t.kind for t in tokens][-1], EOF)
        self.assertEqual((tokens[-1].line, tokens[-1].col), (1, 7))

    def test_eof_position_after_trailing_newline(self):
        tokens, _ = lex("int x;\n")
        self.assertEqual((tokens[-1].line, tokens[-1].col), (2, 1))

    def test_comments_are_not_passed_to_parser(self):
        tokens, _ = lex("int x; // note\n/* block */")
        self.assertEqual([t.text for t in tokens], ["int", "x", ";", ""])

    def test_empty_source_parses_to_empty_program(self):
        result = parse_source("")
        self.assertTrue(result.ok)
        self.assertEqual(result.ast.items, [])


# ---------------------------------------------------------------
# Top level: preprocessor, using, declarations, functions
# ---------------------------------------------------------------
class TestTopLevel(ParserTestCase):
    def test_preprocessor_and_using(self):
        p = self.parse_ok("#include <iostream>\nusing namespace std;\n")
        self.assertIsInstance(p.items[0], ast.Preprocessor)
        self.assertEqual(p.items[0].text, "#include <iostream>")
        self.assertIsInstance(p.items[1], ast.UsingDirective)
        self.assertEqual(p.items[1].namespace, "std")

    def test_global_variable_with_multiple_declarators(self):
        decl = self.parse_ok("int a = 1, b;").items[0]
        self.assertIsInstance(decl, ast.VarDecl)
        self.assertEqual(decl.type_name, "int")
        self.assertEqual([d.name for d in decl.declarators], ["a", "b"])
        self.assertEqual(show(decl.declarators[0].init), "1")
        self.assertIsNone(decl.declarators[1].init)

    def test_all_types(self):
        src = "int a; float b; double c; char d; bool e; string f; std::string g;"
        types = [d.type_name for d in self.parse_ok(src).items]
        self.assertEqual(types, ["int", "float", "double", "char", "bool", "string", "string"])

    def test_array_declarations(self):
        p = self.parse_ok("int arr[10];\nint v[] = {1, 2, 3};")
        a, v = p.items[0].declarators[0], p.items[1].declarators[0]
        self.assertTrue(a.is_array)
        self.assertEqual(show(a.array_size), "10")
        self.assertTrue(v.is_array)
        self.assertIsNone(v.array_size)
        self.assertIsInstance(v.init, ast.InitList)
        self.assertEqual([show(e) for e in v.init.elements], ["1", "2", "3"])

    def test_function_definition_with_params(self):
        f = self.parse_ok("double avg(int values[], int n) { return 0.0; }").items[0]
        self.assertIsInstance(f, ast.FunctionDecl)
        self.assertEqual((f.return_type, f.name), ("double", "avg"))
        self.assertEqual([(p.type_name, p.name, p.is_array) for p in f.params],
                         [("int", "values", True), ("int", "n", False)])
        self.assertIsInstance(f.body, ast.Block)

    def test_function_prototype_and_void_params(self):
        p = self.parse_ok("int f(int);\nvoid g(void);\nint main() { return 0; }")
        self.assertIsNone(p.items[0].body)
        self.assertEqual(p.items[1].params, [])
        self.assertEqual(p.items[2].name, "main")

    def test_unnamed_prototype_params(self):
        f = self.parse_ok("int f(int, double[]);").items[0]
        self.assertEqual([(p.type_name, p.name, p.is_array) for p in f.params],
                         [("int", None, False), ("double", None, True)])

    def test_positions_are_recorded(self):
        f = self.parse_ok("\n  int main() { return 0; }").items[0]
        self.assertEqual((f.line, f.col), (2, 3))
        ret = f.body.statements[0]
        self.assertEqual((ret.line, ret.col), (2, 16))


# ---------------------------------------------------------------
# Statements
# ---------------------------------------------------------------
class TestStatements(ParserTestCase):
    def test_local_declarations_and_assignment(self):
        decl, stmt = self.body("std::string s = \"hi\";\ns = \"bye\";")
        self.assertEqual(decl.type_name, "string")
        self.assertEqual(show(stmt.expr), '(s = "bye")')

    def test_if_else(self):
        stmt = self.body("if (x > 0) y = 1; else y = 2;")[0]
        self.assertIsInstance(stmt, ast.If)
        self.assertEqual(show(stmt.condition), "(x > 0)")
        self.assertIsInstance(stmt.else_branch, ast.ExprStmt)

    def test_dangling_else_binds_to_nearest_if(self):
        outer = self.body("if (a) if (b) x = 1; else x = 2;")[0]
        self.assertIsNone(outer.else_branch)
        self.assertIsNotNone(outer.then_branch.else_branch)

    def test_else_if_chain(self):
        stmt = self.body("if (a) {} else if (b) {} else {}")[0]
        self.assertIsInstance(stmt.else_branch, ast.If)
        self.assertIsInstance(stmt.else_branch.else_branch, ast.Block)

    def test_while(self):
        stmt = self.body("while (i < 10) i++;")[0]
        self.assertIsInstance(stmt, ast.While)
        self.assertEqual(show(stmt.body.expr), "(i++)")

    def test_do_while(self):
        stmt = self.body("do { i--; } while (i > 0);")[0]
        self.assertIsInstance(stmt, ast.DoWhile)
        self.assertEqual(show(stmt.condition), "(i > 0)")

    def test_for_with_declaration(self):
        stmt = self.body("for (int i = 0; i < n; ++i) sum += i;")[0]
        self.assertIsInstance(stmt, ast.For)
        self.assertIsInstance(stmt.init, ast.VarDecl)
        self.assertEqual(show(stmt.condition), "(i < n)")
        self.assertEqual(show(stmt.update), "(++i)")

    def test_for_with_expression_and_empty_parts(self):
        a, b = self.body("for (i = 0; i < 3; i++) {}\nfor (;;) break;")
        self.assertEqual(show(a.init), "(i = 0)")
        self.assertIsNone(b.init)
        self.assertIsNone(b.condition)
        self.assertIsNone(b.update)
        self.assertIsInstance(b.body, ast.Break)

    def test_return_break_continue_empty(self):
        stmts = self.body("return;\nreturn x + 1;\nbreak;\ncontinue;\n;")
        self.assertEqual([type(s).__name__ for s in stmts],
                         ["Return", "Return", "Break", "Continue", "EmptyStmt"])
        self.assertIsNone(stmts[0].value)
        self.assertEqual(show(stmts[1].value), "(x + 1)")

    def test_nested_blocks(self):
        outer = self.body("{ int a; { int b; { a = b; } } }")[0]
        self.assertIsInstance(outer, ast.Block)
        middle = outer.statements[1]
        inner = middle.statements[1]
        self.assertEqual(show(inner.statements[0].expr), "(a = b)")

    def test_cout_and_cin(self):
        out, inp = self.body("cout << \"x = \" << x << endl;\ncin >> a >> b;")
        self.assertEqual(show(out.expr), '(((cout << "x = ") << x) << endl)')
        self.assertEqual(show(inp.expr), "((cin >> a) >> b)")

    def test_full_example_file_parses(self):
        for name in ("valid_program.cpp", "valid_expressions.cpp"):
            with open(os.path.join(ROOT, "examples", name), encoding="utf-8") as f:
                self.parse_ok(f.read())


# ---------------------------------------------------------------
# Expressions: precedence and associativity
# ---------------------------------------------------------------
class TestExpressions(ParserTestCase):
    def test_multiplication_before_addition(self):
        self.assertEqual(show(self.expr("1 + 2 * 3")), "(1 + (2 * 3))")

    def test_parentheses_override_precedence(self):
        self.assertEqual(show(self.expr("(1 + 2) * 3")), "((1 + 2) * 3)")

    def test_left_associativity(self):
        self.assertEqual(show(self.expr("a - b - c")), "((a - b) - c)")
        self.assertEqual(show(self.expr("a / b % c")), "((a / b) % c)")

    def test_assignment_is_right_associative(self):
        self.assertEqual(show(self.expr("a = b = c")), "(a = (b = c))")
        self.assertEqual(show(self.expr("a += b -= 2")), "(a += (b -= 2))")

    def test_compound_assignments(self):
        for op in ("=", "+=", "-=", "*=", "/=", "%="):
            self.assertEqual(show(self.expr("x {} 1".format(op))), "(x {} 1)".format(op))

    def test_logical_precedence(self):
        self.assertEqual(show(self.expr("a || b && c")), "(a || (b && c))")
        self.assertEqual(show(self.expr("a && b == c")), "(a && (b == c))")

    def test_relational_before_equality(self):
        self.assertEqual(show(self.expr("a < b == c >= d")), "((a < b) == (c >= d))")

    def test_shift_between_additive_and_relational(self):
        self.assertEqual(show(self.expr("cout << a + b")), "(cout << (a + b))")
        self.assertEqual(show(self.expr("a << 1 < b")), "((a << 1) < b)")

    def test_unary_operators(self):
        self.assertEqual(show(self.expr("-a * b")), "((-a) * b)")
        self.assertEqual(show(self.expr("!a && b")), "((!a) && b)")
        self.assertEqual(show(self.expr("- -a")), "(-(-a))")
        self.assertEqual(show(self.expr("+a")), "(+a)")

    def test_prefix_and_postfix_increment(self):
        self.assertEqual(show(self.expr("++x")), "(++x)")
        self.assertEqual(show(self.expr("x--")), "(x--)")
        self.assertEqual(show(self.expr("-x++")), "(-(x++))")

    def test_calls_and_indexing(self):
        self.assertEqual(show(self.expr("f()")), "f()")
        self.assertEqual(show(self.expr("f(a, b + 1)[i]")), "f(a, (b + 1))[i]")
        self.assertEqual(show(self.expr("arr[i] = arr[i - 1] * 2")),
                         "(arr[i] = (arr[(i - 1)] * 2))")

    def test_literals(self):
        kinds = [(e.kind, e.value) for e in self.expr("f(42, 3.14, 'c', \"s\", true, false)").args]
        self.assertEqual(kinds, [("int", "42"), ("float", "3.14"), ("char", "'c'"),
                                 ("string", '"s"'), ("bool", "true"), ("bool", "false")])

    def test_std_qualified_names(self):
        e = self.expr("std::cout << std::endl")
        self.assertEqual(show(e), "(std::cout << std::endl)")
        self.assertEqual(e.left.namespace, "std")

    def test_binary_position_is_operator(self):
        e = self.expr("a + b")
        self.assertEqual((e.line, e.col), (2, 3))


# ---------------------------------------------------------------
# Invalid programs: exact error messages
# ---------------------------------------------------------------
class TestSyntaxErrors(ParserTestCase):
    def test_missing_semicolon_points_after_previous_token(self):
        src = "int main() {\n    int x = 5\n    return x;\n}\n"
        self.assertEqual(self.errors(src),
                         ["Syntax error: expected ';' but found 'return' at line 2, column 14"])

    def test_missing_closing_paren(self):
        src = "int main() {\n    if (x > 0 {\n        x = 1;\n    }\n    return 0;\n}"
        self.assertEqual(self.errors(src),
                         ["Syntax error: expected ')' but found '{' at line 2, column 15"])

    def test_unclosed_block(self):
        src = "int main() {\n    int x = 1;\n"
        self.assertEqual(self.errors(src), [
            "Syntax error: expected '}' to close the block opened at line 1, column 12 "
            "but found end of file at line 3, column 1"])

    def test_missing_operand(self):
        self.assertEqual(self.errors("int x = * ;"),
                         ["Syntax error: expected an expression but found ';' at line 1, column 11"])

    def test_invalid_assignment_target(self):
        self.assertEqual(self.errors("int main() { 5 = x; }"), [
            "Syntax error: left side of '=' must be a variable or array element at line 1, column 16"])

    def test_missing_variable_name(self):
        self.assertEqual(self.errors("int = 5;"),
                         ["Syntax error: expected identifier but found '=' at line 1, column 5"])

    def test_else_without_if(self):
        self.assertEqual(self.errors("int main() { else x = 1; }"),
                         ["Syntax error: 'else' without a matching 'if' at line 1, column 14"])

    def test_do_without_while(self):
        self.assertEqual(self.errors("int main() { do x++; return 0; }"),
                         ["Syntax error: expected 'while' but found 'return' at line 1, column 22"])

    def test_unsupported_feature_is_named(self):
        result = parse_source("class Foo {};\nint main() { return 0; }")
        self.assertEqual([str(e) for e in result.syntax_errors], [
            "Syntax error: 'class' is not supported in this C++ subset at line 1, column 1"])
        # recovery skipped the class and still parsed main
        self.assertEqual([f.name for f in result.ast.items], ["main"])

    def test_only_std_qualifier_supported(self):
        self.assertEqual(self.errors("int main() { foo::bar(); }"), [
            "Syntax error: only 'std::' qualified names are supported at line 1, column 17"])

    def test_only_using_namespace_std(self):
        self.assertEqual(self.errors("using namespace foo;"), [
            "Syntax error: expected 'std' (only 'using namespace std;' "
            "is supported) but found identifier 'foo' at line 1, column 17"])

    def test_stray_closing_brace_at_top_level(self):
        self.assertEqual(self.errors("int x;\n}\nint y;"),
                         ["Syntax error: expected a declaration but found '}' at line 2, column 1"])


class TestErrorRecovery(ParserTestCase):
    def test_multiple_errors_in_one_run(self):
        with open(os.path.join(ROOT, "examples", "invalid_syntax.cpp"), encoding="utf-8") as f:
            errors = self.errors(f.read())
        self.assertEqual(errors, [
            "Syntax error: expected ';' but found 'int' at line 5, column 14",
            "Syntax error: expected ')' but found '{' at line 7, column 15",
            "Syntax error: left side of '=' must be a variable or array element at line 10, column 7",
            "Syntax error: expected an expression but found ';' at line 12, column 16",
        ])

    def test_parsing_continues_after_error(self):
        result = parse_source("int main() {\n  x = ;\n  y = 2;\n}\nint other() { return 1; }")
        self.assertEqual(len(result.syntax_errors), 1)
        main, other = result.ast.items
        self.assertEqual(show(main.body.statements[0].expr), "(y = 2)")
        self.assertEqual(other.name, "other")

    def test_error_in_function_header_skips_its_body(self):
        result = parse_source("int f(int a, , int b) { int z = 1; }\nint g() { return 0; }")
        self.assertEqual([str(e) for e in result.syntax_errors],
                         ["Syntax error: expected a type name but found ',' at line 1, column 14"])
        self.assertEqual([f.name for f in result.ast.items], ["g"])

    def test_lexical_errors_stop_before_parsing(self):
        result = parse_source("int a = 3.4.5;\nint b = 5 @ 3")   # second line also lacks ';'
        self.assertIsNone(result.ast)
        self.assertEqual(result.syntax_errors, [])
        self.assertEqual(result.lexical_errors, [
            "Error: malformed number '3.4.5' at line 1, column 9",
            "Error: invalid character '@' at line 2, column 11",
        ])
        self.assertFalse(result.ok)


# ---------------------------------------------------------------
# Pretty-printer and command line
# ---------------------------------------------------------------
class TestPrinterAndCli(unittest.TestCase):
    def test_printer_output(self):
        program = parse_source("int main() { return 1 + 2; }").ast
        self.assertEqual(format_ast(program, ascii_only=True), "\n".join([
            "Program",
            "`-- FunctionDecl return_type='int' name='main'",
            "    `-- body: Block",
            "        `-- Return",
            "            `-- value: Binary op='+'",
            "                |-- left: Literal kind='int' value='1'",
            "                `-- right: Literal kind='int' value='2'",
        ]))

    def test_printer_positions(self):
        program = parse_source("int x;").ast
        self.assertIn("VarDecl type_name='int' @1:1", format_ast(program, show_positions=True))

    def run_cli(self, *args):
        return subprocess.run([sys.executable, "-m", "syntax_analysis"] + list(args),
                              cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True)

    def test_cli_valid_file(self):
        proc = self.run_cli(os.path.join("examples", "valid_program.cpp"))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Parsed successfully.", proc.stdout)

    def test_cli_syntax_errors(self):
        proc = self.run_cli(os.path.join("examples", "invalid_syntax.cpp"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("4 syntax error(s) found.", proc.stdout)

    def test_cli_lexical_errors(self):
        proc = self.run_cli(os.path.join("examples", "invalid_lexical.cpp"))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Lexical errors (parsing skipped):", proc.stdout)

    def test_cli_missing_file(self):
        proc = self.run_cli("does_not_exist.cpp")
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
