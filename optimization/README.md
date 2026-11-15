# Group 3 - Abstract Syntax Tree tools and Code Optimization

Pipeline position:

```
source -> lexer (cpp_tokenizer.py) -> parser (syntax_analysis/, builds the AST)
       -> OPTIMIZER (this package) -> code generation (Group 4)
```

The AST node classes (`syntax_analysis/ast_nodes.py`) are produced by the parser.
This package works **on that AST**: it can export it, measure it, optimize it, and
print it back as C++. The input tree is never modified; `optimize()` returns a new one.

- **Language:** Python 3.7+, standard library only
- **Input:** a `Program` AST (or C++ source text)
- **Output:** an optimized `Program` AST, a change log, and C++ source text

---

## 1. Quick start

Run from the repository root:

```bash
python -m optimization examples/optimize_demo.cpp              # report + optimized code
python -m optimization examples/optimize_demo.cpp --verbose    # list every change
python -m optimization examples/optimize_demo.cpp --ast        # AST before and after
python -m optimization examples/optimize_demo.cpp -o out.cpp   # save optimized code
python -m optimization examples/optimize_demo.cpp --dot tree   # tree_before.dot, tree_after.dot
python -m optimization examples/optimize_demo.cpp --json tree  # tree_before.json, tree_after.json
```

Render a Graphviz file (needs Graphviz installed): `dot -Tpng tree_after.dot -o tree_after.png`

Example (`examples/optimize_demo.cpp`): 110 AST nodes -> 51 nodes.

```cpp
int width = 4, height = 5;
int area = width * height;
int unused = area + 100;
bool debug = false;
if (debug) { cout << "debug mode" << endl; } else { cout << "area = " << area << endl; }
while (false) { cout << "never" << endl; }
```

becomes

```cpp
cout << "area = " << 20 << endl;
```

---

## 2. Using it from another module (e.g. code generation)

```python
from optimization import optimize_source

out = optimize_source(open("main.cpp").read())
if out.ok:
    program = out.optimized      # syntax_analysis.ast_nodes.Program (optimized)
    print(out.code)              # optimized C++ source text
    print(out.stats.counts)      # {"constant_folding": 4, ...}
else:
    print(out.lexical_errors, out.syntax_errors)
```

Or, when you already have the AST from the parser:

```python
from syntax_analysis import parse_source
from optimization import optimize

result = optimize(parse_source(text).ast)
result.ast        # optimized tree      result.rounds  # passes until stable
result.stats      # what was changed    result.nodes_before / nodes_after
```

AST helpers: `to_dict(node)`, `to_json(node)`, `to_dot(node, title)`, `ast_stats(node)`
(node count, depth, count per node type), `unparse(node)` (AST -> C++ text).

---

## 3. Optimizations

The passes run in a loop until the tree stops changing (one pass often enables the next).

| Pass | File | What it does | Example |
|---|---|---|---|
| Constant propagation | `simplify.py` | Replaces a variable by its known constant value | `int a = 2; b = a + 1;` -> `b = 2 + 1;` |
| Constant folding | `simplify.py` | Evaluates constant expressions at compile time | `2 * 3 + 1` -> `7`, `5 > 3` -> `true` |
| Algebraic simplification | `simplify.py` | Removes identity operations | `x + 0`, `x * 1`, `x / 1` -> `x`; `x * 0`, `x - x` -> `0`; `-(-x)` -> `x`; `false && f()` -> `false` |
| Strength reduction | `simplify.py` | Replaces costly operations by cheaper ones | `x * 8` -> `x << 3`; `x * 2` -> `x + x` |
| Constant branches | `dead_code.py` | Keeps only the branch that can run | `if (false) A else B` -> `B` |
| Dead loops | `dead_code.py` | Removes loops that never run | `while (false) {...}`, `for (...; false; ...)`, `do {} while (false)` |
| Unreachable code | `dead_code.py` | Removes code after `return` / `break` / `continue` | |
| Useless statements | `dead_code.py` | Removes statements with no effect | `5;`  `x = x;`  `x += 0;` |
| Unused variables | `unused.py` | Removes local variables that are never read | `int unused = a + 1;` |

### Safety rules (the optimized program must print exactly the same output)

- Only **local** `int`, `bool` and `double` variables are propagated. Globals, arrays,
  `char`, `string` and `float` are not.
- A variable's known value is forgotten when a loop or `if` may change it, when `++`/`--`
  changes it, and when `cin >> x` reads into it.
- Integer folding follows C++: `/` truncates toward zero; results must fit in 32 bits
  (otherwise the expression is left alone); division by zero is never folded.
- `x + 0 -> x` style identities only apply when the type of `x` is known to be
  `int`/`double` (for `char` or `bool` the type would change, e.g. `c + 0` prints a number).
- `false && f()` and `true || f()` fold because C++ never calls `f()` there. `x && false`
  only folds when `x` has no side effects.
- `x * 8 -> x << 3` is only done for variables known to be `int`.

---

## 4. Files

```
optimization/
  __init__.py     optimize_source(), re-exports
  __main__.py     command line (python -m optimization)
  optimizer.py    optimize(): runs the passes until nothing changes
  simplify.py     pass 1: propagation, folding, algebra, strength reduction
  dead_code.py    pass 2: dead branches, dead loops, unreachable code
  unused.py       pass 3: unused local variables
  ast_export.py   to_dict / to_json / to_dot / ast_stats
  unparse.py      AST -> C++ source text
  stats.py        change counters and log
  utils.py        tree walking, literal parsing, purity, "which variables change"
tests/test_optimizer.py
examples/optimize_demo.cpp
```

---

## 5. Testing

```bash
python -m unittest discover -s tests -v
```

`tests/test_optimizer.py` has unit tests per optimization, tests for the AST export, and
(if `g++` is installed) a check that compiles the original and the optimized program and
compares their output.

---

## 6. Known limitations

- Comments are not part of the AST, so the printed optimized code has none.
- The source's own parentheses are not kept; they are re-created from operator precedence.
- Not done: common subexpression elimination, loop unrolling, inlining, dead-store
  elimination (a value overwritten before being read), reassociation (`(x + 1) + 2`).
- Only the language subset supported by `syntax_analysis` is handled.