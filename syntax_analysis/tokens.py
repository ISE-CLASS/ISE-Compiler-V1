"""
Adapter between the lexical analysis phase (cpp_tokenizer.py) and the parser.

This is the ONLY file that knows about the lexer. If the lexer's interface
changes (function name, Token fields, kind names), update this file and the
parser keeps working.

What the lexer gives us (see the root README.md):
    tokenize(src, keep_comments=False) -> (tokens, errors)
    Token(kind: str, text: str, line: int, col: int)
    errors: list of str, "Error: <message> at line <L>, column <C>"

What this adapter adds:
    - converts lexer tokens into our own Token class
    - drops COMMENT tokens (in case comments were ever kept)
    - appends an EOF token, which the lexer does not produce
"""
import os
import sys
from dataclasses import dataclass

try:
    from cpp_tokenizer import tokenize as _lexer_tokenize
except ImportError:
    # cpp_tokenizer.py lives in the repository root, one level above this package.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from cpp_tokenizer import tokenize as _lexer_tokenize


# Token kinds produced by the lexer, plus EOF which we add ourselves.
KEYWORD = "KEYWORD"
IDENTIFIER = "IDENTIFIER"
INTEGER_LITERAL = "INTEGER_LITERAL"
FLOAT_LITERAL = "FLOAT_LITERAL"
STRING_LITERAL = "STRING_LITERAL"
CHAR_LITERAL = "CHAR_LITERAL"
OPERATOR = "OPERATOR"
PUNCTUATOR = "PUNCTUATOR"
PREPROCESSOR = "PREPROCESSOR"
COMMENT = "COMMENT"
EOF = "EOF"

# Built-in type names of the subset. 'string' is an IDENTIFIER in the lexer,
# not a keyword, so it is listed separately.
TYPE_KEYWORDS = {"int", "float", "double", "char", "bool", "void"}
STRING_TYPE = "string"


@dataclass
class Token:
    kind: str
    text: str
    line: int
    col: int


def lex(source):
    """Tokenize source text.

    Returns (tokens, lexical_errors). tokens always ends with an EOF token.
    lexical_errors is the lexer's list of error strings, unchanged.
    """
    raw_tokens, errors = _lexer_tokenize(source, keep_comments=False)
    tokens = [Token(t.kind, t.text, t.line, t.col)
              for t in raw_tokens if t.kind != COMMENT]
    tokens.append(_eof_token(source))
    return tokens, list(errors)


def _eof_token(source):
    """EOF is placed just after the last character of the source."""
    line = source.count("\n") + 1
    col = len(source) - (source.rfind("\n") + 1) + 1
    return Token(EOF, "", line, col)


# ---------------------------------------------------------------
# Token tests used by the parser
# ---------------------------------------------------------------
def is_symbol(tok, *texts):
    """Operator or punctuator with one of the given texts, e.g. is_symbol(t, ';')."""
    return tok.kind in (OPERATOR, PUNCTUATOR) and tok.text in texts


def is_keyword(tok, *texts):
    return tok.kind == KEYWORD and tok.text in texts


def is_identifier(tok, text=None):
    return tok.kind == IDENTIFIER and (text is None or tok.text == text)


def describe(tok):
    """Human-readable description of a token for error messages."""
    if tok.kind == EOF:
        return "end of file"
    text = tok.text if len(tok.text) <= 30 else tok.text[:27] + "..."
    if tok.kind == IDENTIFIER:
        return "identifier '{}'".format(text)
    if tok.kind == INTEGER_LITERAL:
        return "integer literal {}".format(text)
    if tok.kind == FLOAT_LITERAL:
        return "float literal {}".format(text)
    if tok.kind == STRING_LITERAL:
        return "string literal {}".format(text)
    if tok.kind == CHAR_LITERAL:
        return "character literal {}".format(text)
    if tok.kind == PREPROCESSOR:
        return "preprocessor directive '{}'".format(text)
    return "'{}'".format(text)
