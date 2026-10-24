"""
C++ tokenizer (lexical analyzer)

Usage:
    python cpp_tokenizer.py              -> runs built-in tests, then interactive mode
    python cpp_tokenizer.py file.cpp     -> tokenizes a C++ source file
    python cpp_tokenizer.py file.cpp --comments   (also keep comments as tokens)
"""
import re
import sys
from collections import Counter
from dataclasses import dataclass

# ---------------------------------------------------------------
# 1. TOKEN DEFINITIONS
# ---------------------------------------------------------------
KEYWORDS = {
    "alignas", "alignof", "and", "and_eq", "asm", "auto", "bitand", "bitor",
    "bool", "break", "case", "catch", "char", "char8_t", "char16_t", "char32_t",
    "class", "compl", "concept", "const", "consteval", "constexpr", "constinit",
    "const_cast", "continue", "co_await", "co_return", "co_yield", "decltype",
    "default", "delete", "do", "double", "dynamic_cast", "else", "enum",
    "explicit", "export", "extern", "false", "float", "for", "friend", "goto",
    "if", "inline", "int", "long", "mutable", "namespace", "new", "noexcept",
    "not", "not_eq", "nullptr", "operator", "or", "or_eq", "private",
    "protected", "public", "register", "reinterpret_cast", "requires", "return",
    "short", "signed", "sizeof", "static", "static_assert", "static_cast",
    "struct", "switch", "template", "this", "thread_local", "throw", "true",
    "try", "typedef", "typeid", "typename", "union", "unsigned", "using",
    "virtual", "void", "volatile", "wchar_t", "while", "xor", "xor_eq",
}

OPERATORS_3 = {"<<=", ">>=", "<=>", "->*", "..."}
OPERATORS_2 = {"::", "->", ".*", "++", "--", "<<", ">>", "<=", ">=", "==", "!=",
               "&&", "||", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "##"}
OPERATORS_1 = set("+-*/%^&|~!=<>?:.#")
PUNCTUATORS = set("(){}[];,")

STRING_PREFIXES = {"L", "u", "U", "u8"}
RAW_PREFIXES = {"R", "LR", "uR", "UR", "u8R"}

IDENT_RE = re.compile(r"[A-Za-z_]\w*")
# "preprocessing number": grabs the whole numeric blob, validated afterwards
PPNUM_RE = re.compile(r"\.?\d(?:[eEpP][+-]|'\w|[\w.])*")

D = r"\d(?:'?\d)*"
H = r"[0-9a-fA-F](?:'?[0-9a-fA-F])*"
INT_SUFFIX = r"(?:[uU](?:ll|LL|[lL]|[zZ])?|(?:ll|LL|[lL]|[zZ])[uU]?)?"
INT_RE = re.compile(
    rf"(?:0[xX]{H}|0[bB][01](?:'?[01])*|{D}){INT_SUFFIX}\Z")
FLOAT_RE = re.compile(
    rf"(?:(?:{D}\.(?:{D})?|\.{D})(?:[eE][+-]?\d+)?|{D}[eE][+-]?\d+)[fFlL]?\Z")
HEXFLOAT_RE = re.compile(
    rf"0[xX](?:{H}(?:\.(?:{H})?)?|\.{H})[pP][+-]?\d+[fFlL]?\Z")


@dataclass
class Token:
    kind: str
    text: str
    line: int
    col: int


# ---------------------------------------------------------------
# 2. SCANNER
# ---------------------------------------------------------------
def read_quoted(src, j, quote):
    """Return index just past the closing quote, or -1 if unterminated."""
    k, n = j + 1, len(src)
    while k < n:
        ch = src[k]
        if ch == "\\":
            k += 2
            continue
        if ch == quote:
            return k + 1
        if ch == "\n":
            return -1
        k += 1
    return -1


def strip_line_comment(text):
    """Cut a trailing // comment off a preprocessor line (ignoring quotes)."""
    quote = None
    for k, ch in enumerate(text):
        if quote:
            if ch == "\\":
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif text.startswith("//", k):
            return text[:k].rstrip()
    return text


def tokenize(src, keep_comments=False):
    tokens, errors = [], []
    n = len(src)
    i = 0
    line, line_start = 1, 0
    at_line_start = True  # only whitespace/comments seen so far on this line

    def advance_lines(text, start):
        nonlocal line, line_start
        k = text.count("\n")
        if k:
            line += k
            line_start = start + text.rfind("\n") + 1

    while i < n:
        c = src[i]

        # ---- whitespace ----
        if c == "\n":
            line += 1
            i += 1
            line_start = i
            at_line_start = True
            continue
        if c in " \t\r\f\v":
            i += 1
            continue

        sline, scol = line, i - line_start + 1

        def add(kind, text):
            tokens.append(Token(kind, text, sline, scol))

        def err(msg):
            errors.append(f"Error: {msg} at line {sline}, column {scol}")

        # ---- comments ----
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j == -1 else j
            if keep_comments:
                add("COMMENT", src[i:j])
            i = j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            if j == -1:
                err("unterminated block comment")
                advance_lines(src[i:], i)
                i = n
                continue
            text = src[i:j + 2]
            if keep_comments:
                add("COMMENT", text)
            advance_lines(text, i)
            i = j + 2
            continue

        # ---- preprocessor directive (# at start of line) ----
        if c == "#" and at_line_start:
            j = i
            while True:
                j = src.find("\n", j)
                if j == -1:
                    j = n
                    break
                if src[j - 1] == "\\":      # line continuation
                    j += 1
                    continue
                break
            raw = src[i:j]
            add("PREPROCESSOR", strip_line_comment(raw))
            advance_lines(raw, i)
            i = j
            at_line_start = False
            continue

        at_line_start = False

        # ---- identifiers, keywords, prefixed string/char literals ----
        m = IDENT_RE.match(src, i)
        if m:
            ident = m.group()
            j = m.end()
            nxt = src[j] if j < n else ""

            # raw string literal: R"delim( ... )delim"
            if ident in RAW_PREFIXES and nxt == '"':
                p = src.find("(", j + 1)
                delim = src[j + 1:p] if p != -1 else None
                if delim is None or len(delim) > 16 or re.search(r'[\s()\\]', delim):
                    err("invalid raw string literal")
                    i = j + 1
                    continue
                closing = ")" + delim + '"'
                e = src.find(closing, p + 1)
                if e == -1:
                    err("unterminated raw string literal")
                    advance_lines(src[i:], i)
                    i = n
                    continue
                text = src[i:e + len(closing)]
                add("STRING_LITERAL", text)
                advance_lines(text, i)
                i = e + len(closing)
                continue

            # prefixed string / char literal: L"..", u8"..", U'..'
            if ident in STRING_PREFIXES and nxt in ('"', "'"):
                end = read_quoted(src, j, nxt)
                if end == -1:
                    err("unterminated string literal" if nxt == '"'
                        else "unterminated character literal")
                    e = src.find("\n", j)
                    i = n if e == -1 else e
                    continue
                add("STRING_LITERAL" if nxt == '"' else "CHAR_LITERAL", src[i:end])
                i = end
                continue

            add("KEYWORD" if ident in KEYWORDS else "IDENTIFIER", ident)
            i = j
            continue

        # ---- numbers ----
        if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            m = PPNUM_RE.match(src, i)
            text = m.group()
            if INT_RE.match(text):
                add("INTEGER_LITERAL", text)
            elif FLOAT_RE.match(text) or HEXFLOAT_RE.match(text):
                add("FLOAT_LITERAL", text)
            else:
                err(f"malformed number '{text}'")
            i = m.end()
            continue

        # ---- string / char literals ----
        if c in ('"', "'"):
            end = read_quoted(src, i, c)
            if end == -1:
                err("unterminated string literal" if c == '"'
                    else "unterminated character literal")
                e = src.find("\n", i)
                i = n if e == -1 else e
                continue
            text = src[i:end]
            if c == "'" and text == "''":
                err("empty character literal")
            else:
                add("STRING_LITERAL" if c == '"' else "CHAR_LITERAL", text)
            i = end
            continue

        # ---- operators & punctuators (longest match first) ----
        if src[i:i + 3] in OPERATORS_3:
            add("OPERATOR", src[i:i + 3])
            i += 3
            continue
        if src[i:i + 2] in OPERATORS_2:
            add("OPERATOR", src[i:i + 2])
            i += 2
            continue
        if c in PUNCTUATORS:
            add("PUNCTUATOR", c)
            i += 1
            continue
        if c in OPERATORS_1:
            add("OPERATOR", c)
            i += 1
            continue

        # ---- anything else ----
        err(f"invalid character {c!r}")
        i += 1

    return tokens, errors


# ---------------------------------------------------------------
# 3. OUTPUT
# ---------------------------------------------------------------
def print_tokens(tokens):
    print(f"{'LINE:COL':<10}{'TYPE':<18}LEXEME")
    print("-" * 50)
    for t in tokens:
        text = t.text if len(t.text) <= 40 else t.text[:37] + "..."
        print(f"{t.line}:{t.col:<8}{t.kind:<18}{text}")


def print_summary(tokens):
    counts = Counter(t.kind for t in tokens)
    print("\nSummary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())),
          f"| total={len(tokens)}")


def process(src, label="INPUT", keep_comments=False):
    print("=" * 50)
    print(label)
    print("=" * 50)
    tokens, errors = tokenize(src, keep_comments)
    print_tokens(tokens)
    print_summary(tokens)
    if errors:
        print("\nLexical errors:")
        for e in errors:
            print("  " + e)
    print()


# ---------------------------------------------------------------
# 4. GRAPHVIZ EXPORT OF THE SCANNER AUTOMATON (simplified overview)
# ---------------------------------------------------------------
def export_automaton_graphviz(filename="cpp_scanner_dfa"):
    dot_source = r"""digraph CppScanner {
  rankdir=LR;
  node [shape=circle];
  START [shape=circle, style=bold];
  IDENT [shape=doublecircle];
  INT [shape=doublecircle];
  FLOAT [shape=doublecircle];
  NUMBLOB [label="NUMBER\n(pp-number)"];
  STR [label="STRING\nbody"];
  STRLIT [shape=doublecircle, label="STRING\nLITERAL"];
  CHR [label="CHAR\nbody"];
  CHRLIT [shape=doublecircle, label="CHAR\nLITERAL"];
  SLASH [label="/"];
  LCOM [label="line\ncomment"];
  BCOM [label="block\ncomment"];
  COMEND [shape=doublecircle, label="COMMENT"];
  OP [shape=doublecircle, label="OPERATOR /\nPUNCTUATOR"];
  PP [shape=doublecircle, label="PREPROCESSOR"];
  ERROR [shape=doublecircle, color=red, fontcolor=red];

  START -> START [label="whitespace"];
  START -> IDENT [label="letter or _"];
  IDENT -> IDENT [label="letter, digit, _"];
  IDENT -> STR [label="prefix + \" (L, u8, R)"];
  START -> NUMBLOB [label="digit, or . digit"];
  NUMBLOB -> NUMBLOB [label="digit, letter, ', ., e+, p-"];
  NUMBLOB -> INT [label="valid integer\n(dec/hex/bin + suffix)"];
  NUMBLOB -> FLOAT [label="valid float\n(. or exponent)"];
  NUMBLOB -> ERROR [label="malformed\n(e.g. 3.4.5, 12abc)", color=red, fontcolor=red];
  START -> STR [label="\""];
  STR -> STR [label="any char / \\escape"];
  STR -> STRLIT [label="\""];
  STR -> ERROR [label="newline / EOF", color=red, fontcolor=red];
  START -> CHR [label="'"];
  CHR -> CHR [label="any char / \\escape"];
  CHR -> CHRLIT [label="'"];
  CHR -> ERROR [label="newline / EOF / empty", color=red, fontcolor=red];
  START -> SLASH [label="/"];
  SLASH -> LCOM [label="/"];
  LCOM -> LCOM [label="not newline"];
  LCOM -> COMEND [label="newline"];
  SLASH -> BCOM [label="*"];
  BCOM -> BCOM [label="anything"];
  BCOM -> COMEND [label="*/"];
  BCOM -> ERROR [label="EOF", color=red, fontcolor=red];
  SLASH -> OP [label="other (/ or /=)"];
  START -> PP [label="# at line start"];
  START -> OP [label="+ - * % = < > ! & | ^ ~ ? : . , ; ( ) [ ] { }\n(longest match)"];
  START -> ERROR [label="any other char\n(@ $ ` \\)", color=red, fontcolor=red];
}
"""
    with open(f"{filename}.dot", "w") as f:
        f.write(dot_source)
    print(f"Saved {filename}.dot")
    try:
        import graphviz
        graphviz.Source(dot_source).render(filename, format="png", cleanup=True)
        print(f"Saved {filename}.png")
    except Exception:
        print("(Install Graphviz + 'pip install graphviz' to also get a PNG image.)")


# ---------------------------------------------------------------
# 5. MAIN
# ---------------------------------------------------------------
SAMPLE = r"""#include <iostream>
#define MAX 100 // limit

/* Demo program */
int main() {
    int x = 0x1F + 0b101 + 1'000;
    double pi = 3.14e-2f;
    std::string s = "Hello, \"World\"\n";
    char c = '\n';
    auto raw = R"(raw "text" here)";
    for (int i = 0; i <= MAX; ++i) { x += i << 2; }
    if (x != 0 && pi >= 1.0) return x->y;
    return 0;
}
"""

BAD_SAMPLE = r"""int a = 3.4.5;
int b = 12abc;
char *s = "unterminated;
int c = 5 @ 3;
char d = '';
/* never closed
"""

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    keep = "--comments" in sys.argv

    if args:
        with open(args[0], encoding="utf-8", errors="replace") as f:
            process(f.read(), f"FILE: {args[0]}", keep)
        sys.exit(0)

    export_automaton_graphviz("cpp_scanner_dfa")
    print()
    process(SAMPLE, "TEST 1: valid C++", keep)
    process(BAD_SAMPLE, "TEST 2: lexical errors", keep)

    print("Interactive mode: type one line of C++ (or 'quit' to exit)")
    while True:
        try:
            line = input(">>> ")
        except EOFError:
            break
        if line.strip().lower() in ("quit", "exit"):
            break
        if line.strip():
            process(line, f"INPUT: {line}", keep)
