import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cpp_tokenizer import tokenize


# FIRST FUNCTION
def validate_input(source_code):

    errors = []

    if not isinstance(source_code, str):
        errors.append("Input must be a string.")
        return False, errors

    if not source_code.strip():
        errors.append("Source code cannot be empty.")
        return False, errors

    if len(source_code) > 1_000_000:
        errors.append("Source code exceeds the maximum size.")
        return False, errors

    return True, []


# SECOND FUNCTION - ADD THIS BELOW THE FIRST
def validate_cpp_source(source_code):

    valid, errors = validate_input(source_code)

    if not valid:
        return False, errors

    tokens, lexical_errors = tokenize(source_code)

    if lexical_errors:
        return False, lexical_errors

    return True, []


# TESTING SECTION
if __name__ == "__main__":


    test_cases = {

        "Valid C++ code": """
        int main() {
            int x = 10;
            return 0;
        }
        """,

        "Empty input": "",

        "Invalid character": """
        int main() {
            int x = 10 @ 5;
            return 0;
        }
        """,

        "Malformed number": """
        int main() {
            int x = 3.4.5;
            return 0;
        }
        """,

        "Whitespace input": "     "
    }

    for name, code in test_cases.items():

        print("\nTesting:", name)

        valid, errors = validate_cpp_source(code)

        if valid:
            print("PASSED: Input accepted.")

        else:
            print("REJECTED:")

            for error in errors:
                print(error)