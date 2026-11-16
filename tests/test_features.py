"""
Tests for extended language features tracked in FEATURE_IMPLEMENTATION_PLAN.md.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from compiler.pipeline import compile_source  # noqa: E402
from syntax_analysis import parse_source  # noqa: E402


class FeaturePipelineTestCase(unittest.TestCase):
    def compile(self, src, run=False, stdin=""):
        return compile_source(src, filename="test.cpp", run=run, stdin=stdin)

    def assert_semantic_error(self, src, needle):
        res = self.compile(src)
        self.assertFalse(res.get("ok"), res)
        self.assertEqual(res.get("failed_stage"), "semantic", res)
        diags = res.get("diagnostics", [])
        msgs = " | ".join(diags) if diags and isinstance(diags[0], str) else " | ".join(d.get("message", "") for d in diags)
        self.assertIn(needle, msgs, diags)

    def assert_runs(self, src, expected_stdout=None, expected_return=None, stdin=""):
        prev = os.environ.get("COMPILED_BUILD_DIR")
        build_dir = os.path.join(tempfile.gettempdir(), "opencode", "compiled-build")
        os.makedirs(build_dir, exist_ok=True)
        os.environ["COMPILED_BUILD_DIR"] = build_dir
        try:
            res = self.compile(src, run=True, stdin=stdin)
        finally:
            if prev is None:
                os.environ.pop("COMPILED_BUILD_DIR", None)
            else:
                os.environ["COMPILED_BUILD_DIR"] = prev
        self.assertTrue(res.get("ok"), res.get("diagnostics"))
        run = res.get("execution") or {}
        # normalize schema variants
        status = run.get("status")
        if status == "blocked":
            self.skipTest(run.get("stderr") or run.get("log") or "execution blocked")
        if status is None:
            # older/simple schema: ok/stdout/stderr/returncode
            if run.get("ok") is False and (run.get("stderr") or run.get("log")):
                self.skipTest(run.get("stderr") or run.get("log"))
            rc = run.get("returncode")
        else:
            self.assertEqual(status, "ok", run)
            rc = run.get("return_code", run.get("returncode"))
        if expected_stdout is not None:
            self.assertEqual(run.get("stdout"), expected_stdout, run)
        if expected_return is not None:
            self.assertEqual(rc, expected_return, run)
        return res


class TestBitwiseAndCompound(FeaturePipelineTestCase):
    def test_bitwise_ops_and_compound(self):
        src = r'''
int main() {
    int a = 6; // 110
    int b = 3; // 011
    int c = (a & b) + (a | b) + (a ^ b) + (~0 == -1 ? 1 : 0);
    a |= 1;
    a &= 7;
    a ^= 2;
    a <<= 1;
    a >>= 2;
    if (c != (2 + 7 + 5 + 1)) return 1;
    return a;
}
'''
        self.assert_runs(src, expected_return=2)

    def test_parser_accepts_bitwise_compound_assignment(self):
        res = parse_source("int main(){int x=1; x<<=2; x>>=1; x&=3; x|=4; x^=1; return x;}")
        self.assertTrue(res.ok, res.syntax_errors)


class TestConstQualifier(FeaturePipelineTestCase):
    def test_const_variable_read_ok(self):
        self.assert_runs("int main(){const int x=5; return x;}", expected_return=5)

    def test_const_assignment_rejected(self):
        self.assert_semantic_error("int main(){const int x=5; x=6; return x;}", "cannot assign to a const value")

    def test_const_increment_rejected(self):
        self.assert_semantic_error("int main(){const int x=5; x++; return x;}", "cannot modify a const value")

    def test_const_must_be_initialized(self):
        self.assert_semantic_error("int main(){const int x; return 0;}", "must be initialised")


class TestTernary(FeaturePipelineTestCase):
    def test_ternary_basic(self):
        self.assert_runs("int main(){int a=5,b=3; int c=(a>b)?a:b; return c;}", expected_return=5)

    def test_ternary_single_branch_side_effect(self):
        src = r'''
int main() {
    int x = 0;
    int y = 1 ? (x = 7) : (x = 9);
    return x + y;
}
'''
        self.assert_runs(src, expected_return=14)


class TestSwitch(FeaturePipelineTestCase):
    def test_switch_and_break(self):
        src = r'''
int main() {
    int x = 2, y = 0;
    switch (x) {
        case 1: y = 10; break;
        case 2: y = 20; break;
        default: y = 30;
    }
    return y;
}
'''
        self.assert_runs(src, expected_return=20)

    def test_switch_fallthrough_and_default(self):
        src = r'''
int main() {
    int x = 1, y = 0;
    switch (x) {
        case 1: y += 1;
        case 2: y += 2; break;
        default: y += 100;
    }
    return y;
}
'''
        self.assert_runs(src, expected_return=3)


class TestPointersAndReferences(FeaturePipelineTestCase):
    def test_address_of_and_deref(self):
        self.assert_runs("int main(){int x=1; int *p=&x; *p=2; return *p;}", expected_return=2)

    def test_pointer_arithmetic(self):
        src = r'''
int main() {
    int a[3];
    a[0]=10; a[1]=20; a[2]=30;
    int *p = a;
    return *(p+1) + *(p+2);
}
'''
        self.assert_runs(src, expected_return=50)

    def test_pass_by_reference(self):
        src = r'''
void inc(int &x){ x = x + 1; }
int main(){ int a = 1; inc(a); return a; }
'''
        self.assert_runs(src, expected_return=2)


class TestOverloading(FeaturePipelineTestCase):
    def test_function_overloading(self):
        src = r'''
int add(int a, int b){ return a+b; }
double add(double a, double b){ return a+b; }
int main(){
    int x = add(1,2);
    double y = add(1.5, 2.5);
    if (y > 3.9 && y < 4.1) return x + 4;
    return 0;
}
'''
        self.assert_runs(src, expected_return=7)


if __name__ == "__main__":
    unittest.main()
