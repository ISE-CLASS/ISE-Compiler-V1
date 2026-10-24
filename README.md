# C++ Tokenizer (`cpp_tokenizer.py`)

The lexical analyzer (scanner) for our C++ compiler project. It reads C++ source text and turns it into a list of **tokens**, each with its type, text, and position. It also reports lexical errors without stopping, so one run finds every problem.

- **Language:** Python 3.7+ (uses `dataclasses` and f-strings)
- **Dependencies:** none (standard library only). `graphviz` is optional, for the automaton diagram.
- **Input:** C++ source text (as a string or a file)
- **Output:** `(tokens, errors)`

---

## 1. Quick start

```bash
# Run built-in tests, then interactive mode
python cpp_tokenizer.py

# Tokenize a C++ file
python cpp_tokenizer.py main.cpp

# Tokenize a file and keep comments as tokens
python cpp_tokenizer.py main.cpp --comments
```

In interactive mode, type one line of C++ and press Enter. Type `quit` to exit.

Example:

```
>>> int x = 0x1F + 3.14;
LINE:COL  TYPE              LEXEME
--------------------------------------------------
1:1       KEYWORD           int
1:5       IDENTIFIER        x
1:7       OPERATOR          =
1:9       INTEGER_LITERAL   0x1F
1:14      OPERATOR          +
1:16      FLOAT_LITERAL     3.14
1:20      PUNCTUATOR        ;
```

---

## 2. Using it from another module (e.g. the parser)

```python
from cpp_tokenizer import tokenize

source = open("main.cpp", encoding="utf-8").read()
tokens, errors = tokenize(source)

if errors:
    for e in errors:
        print(e)          # lexical errors; decide whether to stop here
else:
    for tok in tokens:
        print(tok.kind, tok.text, tok.line, tok.col)
```

### `tokenize(src, keep_comments=False)`

| Parameter | Type | Description |
|---|---|---|
| `src` | `str` | Full C++ source text |
| `keep_comments` | `bool` | If `True`, comments are returned as `COMMENT` tokens. Default `False` (comments are skipped). |

**Returns** a tuple `(tokens, errors)`:

- `tokens`: `list[Token]`, in source order
- `errors`: `list[str]`, one message per lexical error (empty list if none)

### `Token`

A dataclass with four fields:

| Field | Type | Meaning |
|---|---|---|
| `kind` | `str` | Token type (see section 3) |
| `text` | `str` | Exact text from the source |
| `line` | `int` | Line number, starting at 1 |
| `col` | `int` | Column number, starting at 1 |

---

## 3. Token types

| `kind` | What it covers | Examples |
|---|---|---|
| `KEYWORD` | Any C++20 keyword, including `true`, `false`, `nullptr`, and alternative operators like `and`, `or`, `not` | `int`, `class`, `return`, `constexpr` |
| `IDENTIFIER` | Names that aren't keywords | `main`, `std`, `_count1` |
| `INTEGER_LITERAL` | Decimal, hex (`0x`), binary (`0b`), digit separators, suffixes `u l ll z` | `42`, `0x1F`, `0b101`, `1'000`, `10UL` |
| `FLOAT_LITERAL` | Decimal and hex floats, exponents, suffixes `f l` | `3.14`, `.5`, `1e9`, `3.14e-2f`, `0x1.8p3` |
| `STRING_LITERAL` | Normal, prefixed (`L`, `u`, `U`, `u8`) and raw strings | `"hi\n"`, `u8"x"`, `R"(raw "text")"` |
| `CHAR_LITERAL` | Character literals, with optional prefix | `'a'`, `'\n'`, `L'x'` |
| `OPERATOR` | Operators, matched longest first | `+`, `<<=`, `->`, `::`, `<=>`, `->*`, `...` |
| `PUNCTUATOR` | Brackets and separators | `( ) { } [ ] ; ,` |
| `PREPROCESSOR` | A whole `#` directive line, including `\` line continuations. A trailing `//` comment is removed. | `#include <iostream>`, `#define MAX 100` |
| `COMMENT` | Only produced when `keep_comments=True` | `// note`, `/* note */` |

Notes:

- `>>` is always one `OPERATOR` token. The parser must handle nested templates like `vector<vector<int>>` itself.
- `#` counts as `PREPROCESSOR` only at the start of a line. Elsewhere it is an `OPERATOR`.

---

## 4. Lexical errors

Errors are returned as strings in this format:

```
Error: <message> at line <L>, column <C>
```

| Message | Cause | Example |
|---|---|---|
| `malformed number '...'` | A number that isn't valid C++ | `3.4.5`, `12abc`, `1f` |
| `unterminated string literal` | Missing closing `"` before end of line | `"hello` |
| `unterminated character literal` | Missing closing `'` | `'a` |
| `empty character literal` | `''` | `char c = '';` |
| `unterminated block comment` | `/*` with no `*/` | `/* never closed` |
| `unterminated raw string literal` | `R"(` with no matching `)"` | |
| `invalid raw string literal` | Bad raw string delimiter | |
| `invalid character '...'` | A character C++ doesn't use | `@`, `` ` ``, `\` outside a literal, `$` |

**Error recovery (the scanner never stops early):**

- A malformed number is reported and **no token** is produced for it.
- An unterminated string or character literal is reported, and scanning resumes at the next line.
- An unterminated block comment or raw string consumes the rest of the file.
- An invalid character is reported and skipped.

---

## 5. Helper functions

| Function | Purpose |
|---|---|
| `print_tokens(tokens)` | Prints the token table |
| `print_summary(tokens)` | Prints counts per token type |
| `process(src, label, keep_comments)` | Tokenizes and prints tokens, summary and errors |
| `export_automaton_graphviz(filename)` | Writes `<filename>.dot` (and a `.png` if Graphviz is installed) showing the scanner's state machine |

---

## 6. Known limitations

Contributors should be aware of these before relying on edge cases:

- **Preprocessor lines are one token.** `#include <iostream>` is not split into `#`, `include`, `<`, `iostream`, `>`. Macros are not expanded.
- **User-defined literal suffixes** (e.g. `10_km`, `"text"_s`) are not supported. `10_km` reports a malformed number.
- **Digraphs and trigraphs** (`<:`, `%:`, `??=`) are not supported.
- **Unicode identifiers** (non-ASCII letters) are not supported; they produce `invalid character`.
- **Line continuation (`\` at end of line)** is handled only inside preprocessor directives.
- **Multi-character literals** like `'ab'` are accepted as a single `CHAR_LITERAL`, since C++ allows them.
- The scanner does not check meaning (undeclared names, types, and so on). That belongs to later compiler stages.

---

## 7. Testing

The built-in tests run with `python cpp_tokenizer.py`:

- **TEST 1** is a valid program covering every token type. It should produce **0 errors**.
- **TEST 2** has six deliberate errors. It should report exactly six messages (malformed numbers `3.4.5` and `12abc`, an unterminated string, `@`, an empty character literal, an unclosed block comment).

When you change the scanner, re-run both and confirm the counts. Please add a new line to the test samples (`SAMPLE` / `BAD_SAMPLE` near the bottom of the file) for any new case you handle.

---

## 8. Contributing

1. Create a branch (`git checkout -b your-name-feature`).
2. Keep the `tokenize()` signature and the `Token` fields unchanged, so the parser doesn't break. If you must change them, tell the team first.
3. Add new keywords/operators to the sets at the top of the file (`KEYWORDS`, `OPERATORS_1/2/3`).
4. Run both built-in tests before committing.
5. Open a pull request describing what changed.

---

## 9. File layout

```
cpp_tokenizer.py
├── 1. Token definitions   KEYWORDS, OPERATORS_*, PUNCTUATORS, regexes, Token
├── 2. Scanner             read_quoted(), strip_line_comment(), tokenize()
├── 3. Output              print_tokens(), print_summary(), process()
├── 4. Graphviz export     export_automaton_graphviz()
└── 5. Main                sample inputs, file mode, interactive mode
```
