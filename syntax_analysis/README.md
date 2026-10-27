# Syntax Analysis (Parser) for the C++ Subset Compiler

This is the syntax analysis phase of our group compiler project. It takes the tokens produced by the lexical analysis group (`cpp_tokenizer.py`) and builds an **Abstract Syntax Tree (AST)** for the semantic analysis group. It also reports syntax errors with line and column numbers.

- **Language:** Python 3.7+ (matches the lexer). No `match` statements and no `X | Y` type hints.
- **Dependencies:** none. Standard library only.
- **Technique:** hand-written recursive descent (predictive, LL(1) with small fixed lookahead in two places).
- **Entry point:** `python -m syntax_analysis <source_file>`

---

## 1. Overview

```
 source.cpp
     │
     ▼
 ┌────────────────────┐   tokens + lexical errors
 │ Lexical analysis   │ ─────────────────────────┐
 │ cpp_tokenizer.py   │                          │
 └────────────────────┘                          ▼
                                   ┌──────────────────────────┐
                                   │ SYNTAX ANALYSIS (this)   │
                                   │ tokens.py  (adapter)     │
                                   │ parser.py  (rec. descent)│
                                   └──────────────────────────┘
                                                 │  AST (ast_nodes.py)
                                                 ▼          + syntax errors
                                   ┌──────────────────────────┐
                                   │ Semantic analysis        │
                                   └──────────────────────────┘
                                                 ▼
                                      code generation, ...
```

The parser checks that the token sequence follows the grammar in section 2. It does **not** check meaning, such as undeclared variables, type mismatches, or `break` outside a loop. That is the semantic analysis group's job.

Files:

| File | Purpose |
|---|---|
| `tokens.py` | **Adapter to the lexer.** The only file that imports `cpp_tokenizer`. Converts tokens, drops comments, appends `EOF`. |
| `ast_nodes.py` | AST node dataclasses (the contract with semantic analysis). |
| `parser.py` | The recursive descent parser and `parse_source()`. |
| `errors.py` | `ParseError` (message + line + col). |
| `ast_printer.py` | Tree pretty-printer for demos and debugging. |
| `__main__.py` | Command-line entry point. |
| `../tests/test_parser.py` | 60 `unittest` tests. |
| `../examples/` | Sample valid and invalid programs. |

---

## 2. Grammar (EBNF)

`{ x }` means zero or more, `[ x ]` means optional, `|` means choice. Terminals in quotes match the token `text`; upper-case names are token kinds from the lexer.

```ebnf
program        = { preprocessor | using_dir | external_decl } EOF ;
preprocessor   = PREPROCESSOR ;                    (* recorded, not parsed further *)
using_dir      = "using" "namespace" "std" ";" ;
external_decl  = type IDENT ( func_rest | var_rest ) ;
func_rest      = "(" [ params ] ")" ( block | ";" ) ;
params         = "void" | param { "," param } ;
param          = type [ IDENT ] [ "[" [ INTEGER_LITERAL ] "]" ] ;
var_rest       = decl_tail { "," declarator } ";" ;

var_decl       = type declarator { "," declarator } ";" ;
declarator     = IDENT decl_tail ;
decl_tail      = [ "[" [ expr ] "]" ] [ "=" initializer ] ;
initializer    = assignment | "{" [ assignment { "," assignment } ] "}" ;
type           = "int" | "float" | "double" | "char" | "bool" | "void"
               | "string" | "std" "::" "string" ;

block          = "{" { statement } "}" ;
statement      = block
               | var_decl
               | if_stmt | while_stmt | do_stmt | for_stmt
               | "return" [ expr ] ";"
               | "break" ";" | "continue" ";"
               | preprocessor | using_dir
               | [ expr ] ";" ;
if_stmt        = "if" "(" expr ")" statement [ "else" statement ] ;
while_stmt     = "while" "(" expr ")" statement ;
do_stmt        = "do" statement "while" "(" expr ")" ";" ;
for_stmt       = "for" "(" ( var_decl | [ expr ] ";" ) [ expr ] ";" [ expr ] ")" statement ;

expr           = assignment ;
assignment     = logical_or [ assign_op assignment ] ;          (* right-associative *)
assign_op      = "=" | "+=" | "-=" | "*=" | "/=" | "%=" ;
logical_or     = logical_and    { "||" logical_and } ;
logical_and    = equality       { "&&" equality } ;
equality       = relational     { ( "==" | "!=" ) relational } ;
relational     = shift          { ( "<" | ">" | "<=" | ">=" ) shift } ;
shift          = additive       { ( "<<" | ">>" ) additive } ;   (* cout << / cin >> *)
additive       = multiplicative { ( "+" | "-" ) multiplicative } ;
multiplicative = unary          { ( "*" | "/" | "%" ) unary } ;
unary          = ( "-" | "+" | "!" | "++" | "--" ) unary | postfix ;
postfix        = primary { "(" [ args ] ")" | "[" expr "]" | "++" | "--" } ;
args           = assignment { "," assignment } ;
primary        = INTEGER_LITERAL | FLOAT_LITERAL | STRING_LITERAL | CHAR_LITERAL
               | "true" | "false"
               | IDENT | "std" "::" IDENT
               | "(" expr ")" ;
```

Extra syntactic rule: the left side of an assignment operator must be an identifier or an array element (`x = 1`, `a[i] += 2`). `5 = x` is reported as a syntax error.

---

## 3. Design decisions

### Why recursive descent?

- **It maps one-to-one onto the grammar.** Every rule above is one `parse_<rule>` method in `parser.py`, so a reader can check the code against the EBNF directly.
- **No tools or libraries.** Generators like ANTLR, PLY or Lark would need installing and add a build step. Our rule is standard library only.
- **Good error messages.** At every point the parser knows exactly what it expected, so it can say `expected ')' but found '{'`.
- **The subset is (almost) LL(1).** See the feasibility section for the two places that need more lookahead.

### Precedence and associativity

Each precedence level is its own method, called from the level below it (lowest precedence first):

| Level (low → high) | Operators | Associativity |
|---|---|---|
| assignment | `= += -= *= /= %=` | right |
| logical or | `\|\|` | left |
| logical and | `&&` | left |
| equality | `== !=` | left |
| relational | `< > <= >=` | left |
| shift | `<< >>` | left |
| additive | `+ -` | left |
| multiplicative | `* / %` | left |
| unary (prefix) | `- + ! ++ --` | right |
| postfix | `() [] ++ --` | left |

This matches the real C++ precedence table for these operators.

- **Left associativity** comes from a loop (`parse_left_assoc`): `a - b - c` becomes `(a - b) - c`.
- **Right associativity** comes from recursion: `a = b = c` becomes `a = (b = c)`.
- **`cout << a + b << endl`** parses naturally as `((cout << (a + b)) << endl)`, because `+` binds tighter than `<<`.
- **Dangling `else`** binds to the nearest `if`, as in C++.

### Error handling and recovery

- **Format:** errors print as `Syntax error: <message> at line L, column C`, the same style as the lexer's `Error: <message> at line L, column C`.
- **Missing symbols:** when a missing symbol such as `;` or `)` is noticed only on the next line, the error points just *after the previous token*, where the symbol was forgotten (like gcc/clang).
- **Lexical errors first:** if the lexer reports errors, we print them and **stop before parsing**. The lexer drops bad tokens, so parsing what's left would only produce confusing follow-on errors.
- **Panic-mode recovery:** after a syntax error the parser skips to a safe point and continues, so one run reports several errors.
  - *Inside a block:* it skips to just after the next `;`, or past a whole `{ ... }` block (e.g. the body of a broken `if (x > y {`), or stops in front of `}`, a statement keyword (`if`, `while`, `for`, `do`, `return`, `break`, `continue`) or a type name.
  - *At the top level:* it skips to just after the next `;`, past a whole `{ ... }` function body, or stops in front of a preprocessor line, `using`, or a type name on a later line.
- **No duplicates or hangs:** a second error at exactly the same position is suppressed, and the parser always consumes at least one token per error, so it can never loop forever.
- **Unsupported C++ keywords** (`class`, `struct`, `template`, `switch`, `const`, `auto`, `long`, `nullptr`, ...) get a clear message: `'class' is not supported in this C++ subset`.

Example (`examples/invalid_syntax.cpp`):

```
Syntax error: expected ';' but found 'int' at line 5, column 14
Syntax error: expected ')' but found '{' at line 7, column 15
Syntax error: left side of '=' must be a variable or array element at line 10, column 7
Syntax error: expected an expression but found ';' at line 12, column 16

4 syntax error(s) found.
```

---

## 4. Interface contracts

### 4.1 What we expect FROM the lexer

We use exactly the interface documented in the root `README.md`:

```python
from cpp_tokenizer import tokenize
tokens, errors = tokenize(src, keep_comments=False)
# tokens: list of Token(kind: str, text: str, line: int, col: int)
# errors: list of str, "Error: <message> at line <L>, column <C>"
```

How the parser relies on it (confirmed against the real `cpp_tokenizer.py`):

| Token | Lexer output | What the parser does |
|---|---|---|
| `int float double char bool void` | `KEYWORD` | type names |
| `string`, `std`, `cout`, `cin`, `endl` | `IDENTIFIER` | `string` and `std::string` are recognised as types **by text** |
| `::` | `OPERATOR` | only `std::name` is accepted |
| `true`, `false` | `KEYWORD` | `Literal(kind="bool")` |
| `>>`, `<<` | one `OPERATOR` each | shift / stream operators |
| `( ) { } [ ] ; ,` | `PUNCTUATOR` | matched by text together with operators |
| `#include ...` | one `PREPROCESSOR` token per line | `Preprocessor(text)` node |
| comments | not produced (default) | dropped anyway if present |
| end of input | **not produced** | adapter appends `Token("EOF", "", line, col)` |

All of this is isolated in `tokens.py`. If the lexer changes, only that file needs updating; see section 7.

### 4.2 What we give TO semantic analysis

```python
from syntax_analysis import parse_source

result = parse_source(source_text)
result.ok              # True when there are no lexical and no syntax errors
result.ast             # ast_nodes.Program (None if there were lexical errors)
result.lexical_errors  # list[str], copied from the lexer
result.syntax_errors   # list[ParseError]; str(e) gives the formatted message,
                       # e.line / e.col / e.message give the parts
```

**Only run semantic analysis when `result.ok` is True.** With syntax errors the AST is partial: broken statements are missing from it.

**Node classes** (all dataclasses in `ast_nodes.py`; every node also has `line` and `col`):

| Node | Fields |
|---|---|
| `Program` | `items: list` of `Preprocessor`, `UsingDirective`, `FunctionDecl`, `VarDecl` |
| `Preprocessor` | `text` |
| `UsingDirective` | `namespace` (always `"std"`) |
| `FunctionDecl` | `return_type`, `name`, `params: list[Param]`, `body: Block` or `None` for a prototype |
| `Param` | `type_name`, `name` (`None` if unnamed), `is_array` |
| `VarDecl` | `type_name`, `declarators: list[Declarator]` |
| `Declarator` | `name`, `is_array`, `array_size: Expr or None`, `init: Expr, InitList or None` |
| `InitList` | `elements: list[Expr]` |
| `Block` | `statements: list` |
| `ExprStmt` | `expr` |
| `EmptyStmt` | (none), a lone `;` |
| `If` | `condition`, `then_branch`, `else_branch` or `None` |
| `While` | `condition`, `body` |
| `DoWhile` | `body`, `condition` |
| `For` | `init` (`VarDecl`, `Expr` or `None`), `condition`, `update` (each `Expr` or `None`), `body` |
| `Return` | `value: Expr or None` |
| `Break`, `Continue` | (none) |
| `Assign` | `op` (`= += -= *= /= %=`), `target` (`Identifier` or `Index`), `value` |
| `Binary` | `op`, `left`, `right` |
| `Unary` | `op` (prefix `- + ! ++ --`), `operand` |
| `Postfix` | `op` (`++ --`), `operand` |
| `Call` | `callee`, `args: list[Expr]` |
| `Index` | `array`, `index` |
| `Identifier` | `name`, `namespace` (`"std"` for `std::cout`, else `None`) |
| `Literal` | `kind` (`int float string char bool`), `value` = **exact source text** |

Conventions:

- **Type names** are plain strings. `std::string` is normalized to `"string"`.
- **Literal values** are not converted. `0x1F`, `1'000`, `'\n'` and `"hi"` (with quotes) are kept exactly as written, so semantic analysis or code generation decides how to interpret them.
- **Positions:** `line`/`col` point at the operator for `Binary`/`Assign`, at `(`/`[` for `Call`/`Index`, and at the first token for everything else.

Example. This source:

```cpp
int main() { int x = 2 + 3 * 4; return x; }
```

produces this AST (`python -m syntax_analysis file.cpp`):

```
Program
└── FunctionDecl return_type='int' name='main'
    └── body: Block
        ├── VarDecl type_name='int'
        │   └── declarators
        │       └── Declarator name='x'
        │           └── init: Binary op='+'
        │               ├── left: Literal kind='int' value='2'
        │               └── right: Binary op='*'
        │                   ├── left: Literal kind='int' value='3'
        │                   └── right: Literal kind='int' value='4'
        └── Return
            └── value: Identifier name='x'
```

In Python:

```python
Program(items=[FunctionDecl(return_type='int', name='main', params=[],
    body=Block(statements=[
        VarDecl(type_name='int', declarators=[Declarator(name='x', init=
            Binary(op='+', left=Literal('int', '2'),
                   right=Binary(op='*', left=Literal('int', '3'), right=Literal('int', '4'))))]),
        Return(value=Identifier(name='x'))]))])
```

A visitor for semantic analysis can dispatch on the class name:

```python
def visit(node):
    return getattr(self, "visit_" + type(node).__name__)(node)
```

---

## 5. How to run

All commands are run from the **repository root** (the folder that contains `cpp_tokenizer.py`).

```bash
# Print the AST of a program
python -m syntax_analysis examples/valid_program.cpp

# Also show line:col of every node
python -m syntax_analysis examples/valid_program.cpp --positions

# See syntax error reporting and recovery
python -m syntax_analysis examples/invalid_syntax.cpp

# See lexical errors stopping the parse
python -m syntax_analysis examples/invalid_lexical.cpp
```

Exit codes: `0` parsed OK, `1` lexical or syntax errors, `2` file not readable.

On consoles that can't show Unicode (e.g. the default Windows console), the tree is drawn with ASCII characters (`|--`, `` `-- ``) automatically.

### Running the tests

```bash
python -m unittest discover -s tests -v
```

There are 60 tests covering:

- the lexer adapter (EOF token, comments)
- declarations: multiple declarators, arrays, init lists, all types
- functions, prototypes, unnamed and `void` parameters
- assignments and compound assignments
- precedence and associativity at every level
- `if`/`else` (including dangling else), `while`, `do`-`while`, `for`, `break`, `continue`, `return`
- nested blocks, `cout`/`cin`, `std::` names
- **12 invalid programs with exact error messages**
- multi-error recovery
- lexical errors stopping the parse
- the printer and the command line

---

## 6. Feasibility analysis

### 6.1 Why a subset of C++?

Full C++ is one of the hardest languages to parse, and a full parser is not realistic for a student project:

- **The grammar is ambiguous.** `T * x;` is a pointer declaration if `T` is a type and a multiplication if it is a variable. `T(x);` can be a declaration or a function-style cast. C++ resolves these with the rule "if it can be a declaration, it is one", which needs to know what every name means.
- **Parsing is context-sensitive.** Whether a name is a type depends on earlier declarations (`typedef`, `class`, `using`, templates), so the parser would have to work together with a symbol table. That mixes syntax and semantic analysis, which breaks our group split.
- **Templates make `<` and `>` ambiguous.** `a < b > c` can be two comparisons or a template use. `vector<vector<int>>` needs the `>>` token split in two (the lexer README already warns about this).
- **The "most vexing parse":** `int x(int());` declares a function, not a variable.
- **Size:** the full standard grammar has hundreds of productions. Real compilers (GCC, Clang) use tens of thousands of lines of hand-written parser code.

Our subset removes the root cause: **it has no user-defined types**. The only type names are fixed keywords plus `string`/`std::string`. So the parser can always tell a declaration from an expression by looking at the first token or three, with no symbol table, and the grammar becomes deterministic.

### 6.2 Technical feasibility

- **The approach is sound for this grammar.** The grammar has no left recursion (expression levels use loops instead) and every choice can be made with a fixed number of lookahead tokens:
  - Statements: one token decides (a keyword, `{`, `;`, a type name, or else an expression).
  - Top level: after `type IDENT`, a `(` means function, anything else means variable. This is left-factored, so it is effectively LL(1).
  - `std::string` (a type) versus `std::cout` (an expression) needs 3 tokens of lookahead. That is a small, fixed LL(3) peek in one spot (`Parser.at_type`), and it is harmless.
- **Complexity:** parsing is O(n) in the number of tokens, and each token is looked at a constant number of times. Recovery only moves forward.
- **Limits:** recursion depth grows with nesting depth. Python's default recursion limit (1000) allows roughly 50–100 levels of nested parentheses or blocks, which is far beyond normal programs.
- **Verified:** 60 automated tests pass. All modules also parse with the Python 3.7 grammar (`ast.parse(..., feature_version=(3, 7))`), though they were only *run* on Python 3.14.

### 6.3 Integration feasibility (merging with other groups)

| Risk | Impact | Mitigation |
|---|---|---|
| The lexer changes `tokenize()` or `Token` | parser breaks | Only `tokens.py` touches the lexer. Update `lex()` there; nothing else changes. Their README promises the interface won't change without notice. |
| The lexer renames token kinds (e.g. `IDENTIFIER` → `IDENT`) | tokens not recognised | Kind names are constants at the top of `tokens.py`. Change them in one place. |
| The lexer starts producing an EOF token | double EOF | `lex()` would need to skip appending its own (one line). |
| The lexer makes `string` a keyword | `string` not recognised as a type | `at_type()` checks the identifier by text. Add `"string"` to `TYPE_KEYWORDS` instead. |
| Semantic analysis needs different AST fields | rework | The node table in section 4.2 is the contract. Agree on it **before** they write their visitor. Adding fields with defaults is backward compatible. |
| Semantic group expects converted literal values (`int` 31 instead of `"0x1F"`) | wrong values | We deliberately keep source text. Confirm who converts. |
| Package name clash | import errors | We used `syntax_analysis`, not `parser`, because Python 3.7–3.9 has a built-in module called `parser`. |
| Folder layout changes at merge | `import cpp_tokenizer` fails | `tokens.py` falls back to looking one folder up. If the lexer moves into a package, change the import in `tokens.py`. |
| Different Python versions | syntax errors | Code avoids every feature newer than 3.7. |

### 6.4 Time and skill feasibility for a student group

- **Size:** about 1,150 lines of Python including docstrings and comments (610 of them in `parser.py`), plus about 430 lines of tests. That is a realistic size for a group in a few weeks.
- **Skills needed:** recursive functions, dataclasses, and the grammar/precedence material already taught in a compiler course. No parsing theory beyond LL(1) and no external tools.
- **Workload:** each grammar rule is a short, independent method, so the work is easy to split between members and easy to explain in the presentation.
- **Extending:** adding a new statement means adding one EBNF rule, one `parse_` method, one AST node and a test. A new binary operator level is one 3-line method.

### 6.5 Known limitations

Out of scope by agreement (an error or `'X' is not supported in this C++ subset` is reported):

- classes, structs, unions, enums, templates
- pointers and references (`*`, `&` in declarations; `->`, `.`)
- namespaces other than `using namespace std;`; qualified names other than `std::`
- lambdas, operator overloading, `switch`/`case`, `goto`, exceptions
- macros and preprocessor expansion (each directive is recorded as text only)

Also not supported, though they would be easy to add:

- ternary `?:`, comma operator, bitwise operators (`& | ^ ~`), `<<= >>= &= |= ^=`
- `const`, `static`, `long`, `short`, `unsigned`, `auto`, and casts
- alternative tokens `and`, `or`, `not` (the lexer gives them as keywords)
- `int x(5);` and `int x{5};` initialization; multi-dimensional arrays
- member calls like `cin.get()` or `s.length()` (no `.` operator)
- `string` cannot be used as a variable name, because it is treated as a type
- `++x = 5` is rejected, although C++ allows it, because assignment targets are limited to identifiers and array elements

Error recovery limitations:

- Panic mode can skip valid code after an error. For example, after a missing `;` the next statement can be skipped: one that starts with a type name or a statement keyword (`int y = 10;`, `return x;`) is still parsed, but one that starts with an expression (`y = 10;`) is skipped up to its `;`. Likewise, a broken `if (...)` header skips its whole block, so errors inside that block are only found in the next run.
- Errors inside a `for (...)` header may produce one extra follow-on error.

### 6.6 Future improvements

- Add the "easy" operators above (ternary, bitwise, comma), each as one more precedence level.
- Support `const`, `long`, `unsigned` as type qualifiers in `parse_type`.
- Support `.` member access as one more postfix case, for `s.length()` and `cin.get()`.
- Smarter recovery: insert a missing `;` instead of skipping (phrase-level recovery), and track parentheses inside `for` headers.
- Export the AST to JSON for tools or for groups that want to inspect it outside Python.
- To grow towards real C++ (user-defined types, classes), the parser needs a symbol table of type names that semantic analysis also updates. That is the step where C++ parsing becomes context-sensitive.

---

## 7. Assumptions

1. The source language is the C++ subset in section 2 (agreed with the team).
2. `cpp_tokenizer.py` sits in the repository root and keeps its documented interface. We do not modify it.
3. `string`, `std`, `cout`, `cin` and `endl` are identifiers (checked against the lexer), and `string` / `std::string` is the only non-keyword type.
4. `true`/`false` are keywords (checked against the lexer).
5. Lexical errors stop compilation before parsing.
6. The parser adds its own EOF token, placed just after the last character of the source.
7. Literal values stay as raw source text. Conversion happens in a later phase.
8. Semantic checks (types, scopes, `break` outside a loop, `void` variables, array sizes being constant) are not done here.
9. Commands are run from the repository root.

## 8. Integration checklist (for the merge)

- [ ] `cpp_tokenizer.py` and the root `README.md` are unchanged by our branch.
- [ ] `python -m unittest discover -s tests -v` passes on the merged branch.
- [ ] The lexer group confirms the `tokenize()` signature, `Token` fields and kind names in section 4.1 are final.
- [ ] The semantic analysis group has reviewed the AST node table (section 4.2) and agrees on:
  - [ ] literal values as raw text,
  - [ ] `std::string` normalized to `"string"`,
  - [ ] only running when `result.ok` is True.
- [ ] Everyone uses the same entry point (`from syntax_analysis import parse_source`) and the same Python version (3.7+).
- [ ] If the lexer moves into a package, update the import in `syntax_analysis/tokens.py` only.
- [ ] If other groups add test folders, check that `tests/` names don't clash (for example, rename ours to `test_syntax_*.py` if needed).
- [ ] Optionally, add a one-line pointer to this README in the root README (coordinate with the lexer group, who own it).
