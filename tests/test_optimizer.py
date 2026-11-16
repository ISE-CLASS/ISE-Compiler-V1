"""
Tests for the optimization phase (Group 3).

Run from the repository root:
    python -m unittest discover -s tests -v
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from optimization import ast_stats, optimize, optimize_source, to_dict, to_dot, to_json, unparse  # noqa: E402
from syntax_analysis import ast_nodes as ast, parse_source  # noqa: E402


def opt(body, prelude="", ret="int main() {\n%s\n return 0;\n}\n"):
    """Optimize a main() with the given body, return the optimized C++ text."""
    src = "#include <iostream>\nusing namespace std;\n" + prelude + (ret % body)
    out = optimize_source(src)
    assert out.ok, (out.lexical_errors, [str(e) for e in out.syntax_errors])
    return out.code


def main_lines(code):
    """Statements of main() without braces/return, stripped."""
    lines = [l.strip() for l in code.splitlines()]
    start = lines.index("int main() {") + 1
    end = lines.index("return 0;")
    return lines[start:end]


class ConstantFolding(unittest.TestCase):
    def test_integer_arithmetic(self):
        self.assertIn("cout << 14 << endl;", opt("cout << 2 + 3 * 4 << endl;"))

    def test_division_truncates_toward_zero(self):
        code = opt("cout << -7 / 2 << \" \" << -7 % 2 << endl;")
        self.assertIn("-3", code)
        self.assertIn("-1", code)

    def test_division_by_zero_is_not_folded(self):
        self.assertIn("1 / 0", opt("cout << 1 / 0 << endl;"))

    def test_overflow_is_not_folded(self):
        self.assertIn("2147483647 + 1", opt("cout << 2147483647 + 1 << endl;"))

    def test_float_and_mixed(self):
        self.assertIn("3.5", opt("cout << 1 + 2.5 << endl;"))

    def test_comparison_and_logic(self):
        code = opt("cout << (3 < 4) << (1 && 0) << (!0) << endl;")
        self.assertIn("true", code)
        self.assertIn("false", code)

    def test_hex_literal(self):
        self.assertIn("cout << 32 << endl;", opt("cout << 0x1F + 1 << endl;"))

    def test_cout_chain_is_untouched(self):
        self.assertIn('cout << "a" << 1 << endl;', opt('cout << "a" << 1 << endl;'))


class ConstantPropagation(unittest.TestCase):
    def test_straight_line(self):
        code = opt("int a = 2; int b = a * 3; cout << b << endl;")
        self.assertEqual(main_lines(code), ["cout << 6 << endl;"])

    def test_loop_variable_is_not_propagated(self):
        code = opt("int s = 0; for (int i = 0; i < 3; i++) { s += i; } cout << s << endl;")
        self.assertIn("cout << s << endl;", code)

    def test_cin_invalidates(self):
        code = opt("int n = 5; cin >> n; cout << n << endl;")
        self.assertIn("cout << n << endl;", code)

    def test_branch_assignment_invalidates(self):
        code = opt("int n; cin >> n; int w = 1; if (n > 0) { w = 2; } cout << w << endl;")
        self.assertIn("cout << w << endl;", code)

    def test_increment_invalidates(self):
        code = opt("int k = 3; k++; int m; cin >> m; cout << k + m << endl;")
        self.assertNotIn("cout << 3 +", code)

    def test_globals_are_not_propagated(self):
        code = opt("cout << g << endl;", prelude="int g = 5;\n")
        self.assertIn("cout << g << endl;", code)

    def test_int_conversion_on_declaration(self):
        code = opt("int a = 7 / 2; cout << a << endl;")
        self.assertIn("cout << 3 << endl;", code)

    def test_shadowing(self):
        code = opt("int x = 1; { int x = 2; cout << x << endl; } cout << x << endl;")
        self.assertIn("cout << 2 << endl;", code)      # inner x is 2
        self.assertIn("cout << x << endl;", code)      # outer x: left alone (conservative)


class Algebra(unittest.TestCase):
    P = "int f(int v, double d, char c) {\n%s\n}\n"

    def body(self, expr, ret_type="int"):
        src = "#include <iostream>\nusing namespace std;\nint f(int v, double d, char c) { return %s; }\n" % expr
        out = optimize_source(src)
        self.assertTrue(out.ok)
        return out.code

    def test_identities(self):
        self.assertIn("return v;", self.body("v + 0"))
        self.assertIn("return v;", self.body("0 + v"))
        self.assertIn("return v;", self.body("v - 0"))
        self.assertIn("return v;", self.body("v * 1"))
        self.assertIn("return v;", self.body("v / 1"))
        self.assertIn("return 0;", self.body("v * 0"))
        self.assertIn("return 0;", self.body("v - v"))

    def test_char_is_left_alone(self):
        self.assertIn("c + 0", self.body("c + 0"))

    def test_strength_reduction(self):
        self.assertIn("return v << 3;", self.body("v * 8"))
        self.assertIn("return v + v;", self.body("2 * v"))
        self.assertIn("return d + d;", self.body("d * 2").replace("return int", "return"))

    def test_double_negation(self):
        self.assertIn("return v;", self.body("-(-v)"))


class DeadCode(unittest.TestCase):
    def test_if_true(self):
        code = opt('if (1 < 2) { cout << "yes" << endl; } else { cout << "no" << endl; }')
        self.assertIn('"yes"', code)
        self.assertNotIn('"no"', code)

    def test_if_false_without_else(self):
        self.assertEqual(main_lines(opt('if (0) { cout << "x" << endl; }')), [])

    def test_while_false(self):
        self.assertEqual(main_lines(opt('while (false) { cout << "x" << endl; }')), [])

    def test_for_false_keeps_nothing(self):
        self.assertEqual(main_lines(opt('for (int i = 0; false; i++) { cout << "x" << endl; }')), [])

    def test_do_while_false_runs_once(self):
        code = opt('do { cout << "x" << endl; } while (false);')
        self.assertEqual(main_lines(code), ['cout << "x" << endl;'])

    def test_do_while_false_with_break_is_kept(self):
        code = opt('do { break; } while (false);')
        self.assertIn("while (false)", code)

    def test_unreachable_after_return(self):
        out = optimize_source("int f() { return 1; int x = 2; x++; }\n")
        self.assertNotIn("x", out.code)

    def test_no_effect_statements(self):
        code = opt("int a; cin >> a; a = a; a += 0; a *= 1; 5;")
        self.assertEqual(main_lines(code), ["int a;", "cin >> a;"])


class UnusedVariables(unittest.TestCase):
    def test_unused_removed(self):
        self.assertEqual(main_lines(opt("int a = 1; int b = a + 1;")), [])

    def test_used_kept(self):
        code = opt("int n; cin >> n; cout << n << endl;")
        self.assertIn("int n;", code)

    def test_impure_initializer_kept(self):
        code = opt("int a = f();", prelude="int f() { cout << 1; return 1; }\n")
        self.assertIn("int a = f();", code)

    def test_arrays_kept(self):
        self.assertIn("int arr[3]", opt("int arr[3] = {1, 2, 3};"))


class Pipeline(unittest.TestCase):
    def test_input_ast_not_modified(self):
        parsed = parse_source("int main() { int a = 1 + 2; return a; }")
        before = to_json(parsed.ast)
        result = optimize(parsed.ast)
        self.assertEqual(to_json(parsed.ast), before)
        self.assertNotEqual(to_json(result.ast), before)

    def test_idempotent(self):
        with open(os.path.join(ROOT, "examples", "valid_program.cpp"), encoding="utf-8") as f:
            src = f.read()
        once = optimize_source(src).code
        twice = optimize_source(once).code
        self.assertEqual(once, twice)

    def test_syntax_errors_stop_pipeline(self):
        out = optimize_source("int main() { int x = ; }")
        self.assertFalse(out.ok)
        self.assertEqual(out.code, "")

    def test_lexical_errors_stop_pipeline(self):
        out = optimize_source("int main() { int x = 3.4.5; }")
        self.assertFalse(out.ok)

    def test_stats_counts(self):
        out = optimize_source("int main() { int a = 2 * 3; return a; }")
        self.assertGreaterEqual(out.stats.counts["constant_folding"], 1)
        self.assertGreaterEqual(out.stats.counts["constant_propagation"], 1)


class AstTools(unittest.TestCase):
    def setUp(self):
        self.tree = parse_source("int main() { int a = 1 + 2; return a; }").ast

    def test_dict_and_json(self):
        d = to_dict(self.tree)
        self.assertEqual(d["node"], "Program")
        self.assertIn('"node": "Binary"', to_json(self.tree))

    def test_dot(self):
        dot = to_dot(self.tree)
        self.assertTrue(dot.startswith("digraph AST"))
        self.assertIn("->", dot)

    def test_stats(self):
        s = ast_stats(self.tree)
        self.assertEqual(s["by_type"]["Binary"], 1)
        self.assertGreater(s["depth"], 3)

    def test_unparse_roundtrip(self):
        src = "int main() { int a = 1, b = 2; int c = (a + b) * 3 - (a - b); return c % 4; }"
        tree = parse_source(src).ast
        again = parse_source(unparse(tree))
        self.assertTrue(again.ok)
        self.assertEqual(unparse(again.ast), unparse(tree))


@unittest.skipUnless(shutil.which("g++"), "g++ not installed")
class BehaviourPreserved(unittest.TestCase):
    """Compile original and optimized program with g++; outputs must match."""

    def run_both(self, src, stdin=""):
        out = optimize_source(src)
        self.assertTrue(out.ok)
        results = []
        with tempfile.TemporaryDirectory() as tmp:
            for name, text in (("orig", src), ("opt", out.code)):
                cpp = os.path.join(tmp, name + ".cpp")
                exe = os.path.join(tmp, name + (".exe" if os.name == "nt" else ""))
                with open(cpp, "w") as f:
                    f.write(text)
                subprocess.run(["g++", "-std=c++17", "-w", "-o", exe, cpp], check=True)
                try:
                    completed = subprocess.run([exe], input=stdin, capture_output=True, text=True)
                except OSError as exc:
                    blocked = getattr(exc, "winerror", None) == 4551 or "Application Control policy has blocked this file" in str(exc)
                    if blocked:
                        self.skipTest("execution blocked by host policy: {}".format(exc))
                    raise
                results.append(completed.stdout)
        self.assertEqual(results[0], results[1])

    def test_examples(self):
        for name in ("valid_program.cpp", "optimize_demo.cpp"):
            with open(os.path.join(ROOT, "examples", name), encoding="utf-8") as f:
                src = f.read()
            for stdin in ("4\n", "0\n"):
                self.run_both(src, stdin)


if __name__ == "__main__":
    unittest.main()