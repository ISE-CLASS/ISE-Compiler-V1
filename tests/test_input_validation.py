import unittest

from input_validation.validator import validate_input, validate_cpp_source


class TestInputValidation(unittest.TestCase):

    def test_valid_input(self):
        code = "int main() { return 0; }"
        valid, errors = validate_input(code)

        self.assertTrue(valid)
        self.assertEqual(errors, [])

    def test_empty_input(self):
        valid, errors = validate_input("")

        self.assertFalse(valid)
        self.assertIn("Source code cannot be empty.", errors)

    def test_whitespace_input(self):
        valid, errors = validate_input("     ")

        self.assertFalse(valid)
        self.assertIn("Source code cannot be empty.", errors)

    def test_non_string_input(self):
        valid, errors = validate_input(123)

        self.assertFalse(valid)
        self.assertIn("Input must be a string.", errors)

    def test_input_exceeds_maximum_size(self):
        code = "a" * 1_000_001
        valid, errors = validate_input(code)

        self.assertFalse(valid)
        self.assertIn("Source code exceeds the maximum size.", errors)

    def test_valid_cpp_source(self):
        code = "int main() { return 0; }"
        valid, errors = validate_cpp_source(code)

        self.assertTrue(valid)
        self.assertEqual(errors, [])

    def test_invalid_character(self):
        code = "int main() { int x = 10 @ 5; return 0; }"
        valid, errors = validate_cpp_source(code)

        self.assertFalse(valid)
        self.assertTrue(errors)


if __name__ == "__main__":
    unittest.main()
