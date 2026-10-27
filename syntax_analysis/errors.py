"""
Syntax error type used by the parser.

The class is called ParseError so it does not shadow Python's built-in
SyntaxError. Its message follows the lexer's error style
("Error: <message> at line <L>, column <C>"):

    Syntax error: expected ';' but found identifier 'y' at line 4, column 12
"""


class ParseError(Exception):
    def __init__(self, message, line, col):
        super().__init__(message)
        self.message = message
        self.line = line
        self.col = col

    def __str__(self):
        return "Syntax error: {} at line {}, column {}".format(
            self.message, self.line, self.col)
