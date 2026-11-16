# Feature Implementation Plan: C++ Subset Extensions

This document captures the gap analysis and exact-change implementation plan for adding the following features to the COMPILED compiler pipeline without breaking existing support for recursion, arrays, scoping, and `for/if-else` control flow:

1. `const` type qualifiers
2. Pointers and address-of operators
3. Pass-by-reference parameters
4. Function overloading (name mangling)
5. Bitwise operators
6. `switch` / `case` control flow
7. Ternary conditional operator (`?:`)

It is intended as a continuation point for implementation work.

---

## Current pipeline snapshot (relevant)

- **Lexer** (`cpp_tokenizer.py`): keywords already include `const`, `switch`, `case`, `default`; operators already tokenize `&`, `*`, `|`, `^`, `~`, `<<`, `>>`, `?`, `:` and compound assignments like `&=`, `|=`, `^=`, `<<=`, `>>=`.
- **Parser adapter** (`syntax_analysis/tokens.py`): passes lexer tokens through and appends `EOF`; `TYPE_KEYWORDS = {int,float,double,char,bool,void}` plus special `string`/`std::string`.
- **AST** (`syntax_analysis/ast_nodes.py`): types are plain strings; declarations/params carry `type_name`, array flags/size; no qualifiers/pointers/references; no `Ternary`, `Switch/Case/Default`, or explicit address-of/deref nodes.
- **Parser** (`syntax_analysis/parser.py`): rejects `const/switch/case/default` via `UNSUPPORTED_KEYWORDS`; `parse_type()` only accepts scalar/base type names; params/declarators support only optional `[]`; expressions have `<<`/`>>` at shift level, no bitwise `&|^`, no unary `~`, no ternary, assignment targets only `Identifier`/`Index`.
- **Semantic** (`compiler/semantic.py`): symbols/functions keyed by name (`self.functions: Dict[str, FunctionInfo]`), call resolution by name, no overloads/mangling; lvalues limited to variables/array elements; stream ops special-cased; `const`/refs/pointers/switch/ternary not modeled.
- **IR** (`compiler/ir.py`): TAC with ops like `bin/un/load/store/call/goto/label/if/ifnot/param/ret/print/read`; calls carry callee name string; lvalues are var or array index; no general address/deref or switch construct.
- **Codegen** (`compiler/codegen.py`): emits `userfn_<name>` labels, private stack-based user-function calling convention, array/string helpers, libc I/O/copy; no pointer/reference indirection for params beyond existing array handling, no bitwise `&|^~` emission.
- **Must preserve:** recursion, arrays (decl/init/index/params/decay behavior currently supported), lexical scoping/blocks/loops, `if/else`, `for/while/do-while`, `break/continue`, stream I/O, existing numeric/string/bool/char behavior.

---

## 1) `const` type qualifiers

### Current rejection/missing
- `cpp_tokenizer.py`: `const` already tokenized (OK).
- `syntax_analysis/parser.py`: `UNSUPPORTED_KEYWORDS` includes `"const"`; `parse_type()` has no qualifier parsing; `Param`/`VarDecl`/declarators have no const metadata.
- `syntax_analysis/ast_nodes.py`: `Param`, `Declarator`, `VarDecl`, `FunctionDecl` lack const flags.
- `compiler/semantic.py`: no immutability tracking; `_declare` stores `Symbol` without const; `_analyze_assign`, unary/postfix inc-dec, and stream input do not reject writes/reads-into const lvalues.
- `compiler/ir.py` / `compiler/codegen.py`: mostly unaffected for storage/layout if enforced semantically.

### Exact changes
- **`syntax_analysis/ast_nodes.py`**
  - Add `is_const: bool = False` to `Param`, `Declarator` (if qualifier per declarator needed), and `VarDecl` (defaults at end to reduce breakage).
  - Optionally add `return_is_const: bool = False` to `FunctionDecl` only if in-scope.
  - Consider future structured type helper, but keep `type_name` string initially.
- **`syntax_analysis/parser.py`**
  - Remove `"const"` from `UNSUPPORTED_KEYWORDS`.
  - Parse optional `const` in type positions (`const int` at minimum; optionally `int const`), returning qualifier info alongside base type.
  - Propagate const through `parse_external`, `parse_function_rest`, `parse_params`/`parse_param`, `parse_var_decl`, `parse_declarator_tail`/`parse_more_declarators` into AST nodes.
  - Update grammar/docs in `syntax_analysis/README.md`.
- **`compiler/semantic.py`**
  - Add `is_const` to `Symbol`, `ParamInfo` (and include in signatures later for overloads if needed).
  - Copy const flags during global/local/param declaration collection.
  - Enforce init rules as chosen (recommended: require initializer for const locals/globals in supported forms).
  - Reject writes to const: `_analyze_assign` (`=` and compound assignments), prefix/postfix `++/--`, and `cin >>` target validation. Array element mutation through const base must be rejected per model.
  - Allow reads of const values where `T` is expected by value; add explicit const-write checks separate from compatibility.
- **`compiler/ir.py` / `compiler/codegen.py`**
  - No new opcodes/storage for basic const correctness; ensure no assumptions that all lvalues are mutable outside semantic checks.

### Compatibility notes
- Only accept `const` in valid type/declarator contexts.
- No calling convention/layout/symbol identity changes.

---

## 2) Pointers & address-of operators

### Current rejection/missing
- Lexer tokenizes `*`, `&` (OK).
- Parser: prefix `*`/`&` not handled in unary parsing; `parse_type`/params/declarators cannot represent pointers; assignment lvalues exclude deref; no pointer type forms.
- AST: no pointer metadata and no explicit address-of/deref nodes (or unary handling conventions).
- Semantic: `base_type/is_array_type/make_array_type/compatible` understand only scalars/`T[]`; no `T*`, no address-of/deref typing, no pointer arithmetic/indexing-on-pointer rules, limited lvalues.
- IR: array `load/store(base,index)` exists but no general address computation/deref or pointer arithmetic semantics.
- Codegen: can compute addresses for arrays/globals/locals in specific cases but not general address-of/deref/pointer arithmetic/load-store through computed pointers.

### Exact changes
- **`syntax_analysis/ast_nodes.py`**
  - Add pointer/reference/type-shape fields: e.g. `Param.pointer_depth`, `Declarator.pointer_depth`, `Param.is_reference` (feature 3), `is_const`.
  - Add `AddressOf(operand)` and `Deref(operand)` expr nodes (preferred for clarity) or formalize unary `&/*` handling.
  - Extend assignable targets to include deref lvalues.
- **`syntax_analysis/parser.py`**
  - Extend type/declarator/param parsing for `int*`, `int *p`, multi-level pointers as subset allows.
  - Handle prefix unary `&` and `*` in `parse_unary` (prefix context) while preserving infix `*` multiply in multiplicative parsing.
  - Allow deref lvalues in `parse_assignment` (e.g. `*p = ...`, and possibly `(*p)[i]` depending on AST).
  - Keep `&` disambiguation by context: declarator/type parsing vs expression parsing.
  - Document supported subset/grammar.
- **`compiler/semantic.py`**
  - Introduce type helpers/model for base/const/pointer depth/ref/array.
  - Address-of: operand must be addressable lvalue; result pointer to operand type.
  - Deref: operand must be pointer-like; result lvalue of pointee type when valid.
  - Extend `_analyze_index`, binary `+/-`, and comparisons for pointer cases as supported (`p+i`, `i+p`, `p-p`, pointer indexing).
  - Extend `_lvalue_type`, assignment compatibility, param/arg checks, and const-correctness for pointees.
  - Preserve array semantics and decay behavior.
- **`compiler/ir.py`**
  - Add/address conventions for address-of and deref load/store (`addr`/pointer load/store or generalized load/store/address forms).
  - Lower deref lvalues and address-of of supported lvalues.
  - Represent pointer arithmetic with type-aware scaling info.
  - Keep existing array paths working.
- **`compiler/codegen.py`**
  - Generalize address computation and load/store for pointer values and deref targets.
  - Implement pointer arithmetic scaling in `_binary` by pointee element/storage size.
  - Ensure pointer slots/params are 8-byte address values and copied correctly.
  - Preserve private user-function calling convention.

### Compatibility notes
- Do not break `a[i]` for declared arrays.
- Preserve `*` multiply vs prefix deref and prepare `&` address-of vs infix bitwise AND disambiguation.

---

## 3) Pass-by-reference parameters

### Current rejection/missing
- No `&` reference marker in param/type parsing or AST.
- Semantic does not require lvalue arguments for references or model aliasing/const-correct binding.
- IR/codegen copy argument values into callee param slots; no indirection to caller storage.

### Exact changes
- **`syntax_analysis/ast_nodes.py`**
  - Add `is_reference: bool = False` to `Param` (combine with const/pointer depth).
- **`syntax_analysis/parser.py`**
  - Recognize `&` in parameter declarator/type positions (`int& x`, `const int &x`).
  - Keep `&` unambiguous (reference in params, address-of in expressions).
  - Define subset scope (parameters first).
- **`compiler/semantic.py`**
  - Add `is_reference` to `ParamInfo`/`Symbol`.
  - For by-ref params, enforce argument lvalue/compatibility/const-correctness at call sites.
  - Reads/writes/inc-dec/stream input on ref params operate on referenced object type.
- **`compiler/ir.py`**
  - For ref params, pass address of caller lvalue argument and mark/load/store through indirection in callee.
  - Preserve by-value params and existing array/string behavior.
- **`compiler/codegen.py`**
  - Caller passes address for ref args; callee stores pointer and dereferences on access.
  - Taking address of a ref param yields aliased object address.
  - Maintain recursion/stack correctness.

### Compatibility notes
- Behavior changes only for explicitly `&`-marked parameters.

---

## 4) Function overloading (name mangling)

### Current rejection/missing
- Semantic stores functions by name only and resolves calls by name.
- IR emits/carries callee source name; codegen emits `userfn_<name>` labels.
- No overload resolution or mangled symbols.

### Exact changes
- **`syntax_analysis/ast_nodes.py`**
  - Add `mangled_name` / resolved overload fields on `FunctionDecl`/`Call` as needed.
- **`compiler/semantic.py`**
  - Replace name-only map with overload-aware structures (e.g. name -> list and/or mangled -> info).
  - Compute stable parameter signatures including const/ref/pointer (and array decay rules chosen) for collection and conflict/redefinition checks.
  - Generate deterministic, label-safe mangled names; store on function/call nodes.
  - Implement overload resolution in `_analyze_call` (arity/type compatibility, ambiguity diagnostics).
  - Keep `main` special/unique.
- **`compiler/ir.py`**
  - Emit/use resolved mangled callee name in calls; carry mangled identity in `IRFunction`.
- **`compiler/codegen.py`**
  - Use mangled names in labels and call emission while preserving `main` entry behavior.

### Compatibility notes
- Internal labels may change even without overloads; runtime behavior must remain identical.
- Define and apply consistent signature encoding as new type features land.

---

## 5) Bitwise operators

### Current rejection/missing
- Lexer tokenizes `| & ^ ~ << >>` and compound assignments (OK).
- Parser: no binary bitwise `&|^`, no unary `~`, `ASSIGN_OPS` missing `<<= >>= &= |= ^=`.
- Semantic: shifts partly handled as non-stream binary ops; no `&|^`/`~`.
- Codegen: integer binary path lacks `&|^` and unary lacks `~`.
- IR compound assignment lowering uses `node.op[0]` (incorrect for multi-char ops like `<<=`/`>>=`).

### Exact changes
- **`syntax_analysis/parser.py`**
  - Add precedence levels: bitwise AND/XOR/OR between equality and logical AND (standard subset ordering).
  - Add unary `~` in `parse_unary`.
  - Extend `ASSIGN_OPS` with `<<=`, `>>=`, `&=`, `|=`, `^=`.
  - Preserve `cout<<`/`cin>>` parsing/behavior and `&`/`*` context disambiguation.
- **`compiler/semantic.py`**
  - Handle binary `&|^` and unary `~` for integral/bool-compatible operands per subset.
  - Keep/validate non-stream `<< >>` as integral shifts; preserve stream special-case.
  - Allow bitwise compound assignments for valid lvalues/types.
- **`compiler/ir.py`**
  - Fix compound assignment lowering to use `op = node.op[:-1]` instead of `node.op[0]`.
  - Emit/pass through new unary/binary ops.
- **`compiler/codegen.py`**
  - `_binary`: add `and/or/xor` for `&|^`; keep existing `shl/sar` for shifts.
  - `_unary`: add `not` for `~`.

### Compatibility notes
- `<<`/`>>` must continue to work for streams and integer shifts as currently supported.
- Prefix `&`/`*` vs infix operators must remain correct.

---

## 6) `switch` / `case` control flow

### Current rejection/missing
- Lexer tokenizes `switch/case/default/break` (OK).
- Parser rejects `switch/case/default`; no statement/label parsing.
- AST has no switch/case/default nodes.
- Semantic allows `break` only in loops (`loop_depth`), no switch context, no constant-integer/duplicate/default validation.
- IR/codegen have labels/jumps but no switch lowering.

### Exact changes
- **`syntax_analysis/ast_nodes.py`**
  - Add `Switch`, `Case`, `Default` (or equivalent section representation) nodes with source locations and bodies.
- **`syntax_analysis/parser.py`**
  - Remove `switch`, `case`, `default` from `UNSUPPORTED_KEYWORDS`.
  - Add `parse_switch()` and handle `case/default` labels/sections inside switch bodies; require `:` after labels.
  - Integrate with statement parsing/recovery.
- **`compiler/semantic.py`**
  - Track `switch_depth` (or break-target stack) so `break` is valid in loops or switches; `continue` remains loop-only.
  - Validate switch condition integral type, case labels constant integers, no duplicate cases, at most one default.
  - Analyze section bodies with correct scoping and existing statement rules; allow fallthrough.
- **`compiler/ir.py`**
  - Lower switch via evaluate-once + comparisons/branches/labels and switch-end break target.
  - Support nested switch/loop break/continue binding correctly.
- **`compiler/codegen.py`**
  - No new primitives expected if lowered to existing label/goto/conditional ops.

### Compatibility notes
- Expand `break` validity only to include switch contexts; preserve loop behavior and nesting semantics.

---

## 7) Ternary conditional operator (`?:`)

### Current rejection/missing
- Lexer tokenizes `?` and `:` (OK).
- Parser has no ternary level; `:` not valid in expression contexts.
- AST/semantic/IR/codegen lack ternary node, typing, and conditional lowering.

### Exact changes
- **`syntax_analysis/ast_nodes.py`**
  - Add `Ternary(condition, when_true, when_false, line, col)`.
- **`syntax_analysis/parser.py`**
  - Add ternary parsing between assignment/value and logical-or (right-associative): `cond ? expr : ternary/assignment-level`.
  - Ensure `:` is consumed only in ternary contexts (and switch labels in statement contexts).
- **`compiler/semantic.py`**
  - Condition must be scalar; analyze both branches; enforce type compatibility/common result type rules per subset; annotate result type.
  - Treat as non-lvalue in initial subset.
- **`compiler/ir.py`**
  - Lower with control flow and temp so only one branch executes (preserve side effects): condition -> branch -> eval/store -> join.
- **`compiler/codegen.py`**
  - No new primitives if lowered to existing conditional/copy ops; ensure typed temp handling.

### Compatibility notes
- Must not break existing expressions or future/added `:` usage in switch labels.

---

## Cross-cutting integration details

- **Type representation:** centralize helpers for base type, const, pointer depth, reference, array element/size/decay, pointee size, integral/numeric checks, and lvalue/addressability rules. Avoid ad-hoc string checks as features combine.
- **Lvalues:** unify support for `Identifier`, `Index` (array/pointer base as supported), and `Deref` across parser, semantic (`_lvalue_type`), IR (`_lvalue/_load/_store`), and codegen.
- **Operator precedence (target):** postfix/call/index -> unary (`+ - ! ~ ++ -- * &`) -> multiplicative -> additive -> shift `<< >>` -> relational -> equality -> bitwise `&` -> bitwise `^` -> bitwise `|` -> logical `&&` -> logical `||` -> ternary `?:` -> assignment `= += -= *= /= %= <<= >>= &= ^= |=`.
- **Streams:** preserve `cin >>`/`cout <<` stream semantics when left operand is `istream/ostream`.
- **Mangled signatures:** include parameter types after chosen decay rules and type qualifiers/pointer/ref forms; define consistent treatment of top-level const on by-value params.
- **`main`:** keep special/unique entrypoint behavior.

---

## Recommended implementation order

1. **Type-system groundwork/helpers** (shared type metadata/utilities, plumbing) while keeping tests green.
2. **Bitwise operators + compound-assignment lowering fix** (`node.op[:-1]`) in parser/semantic/IR/codegen.
3. **`const` qualifiers** (parser/AST/semantic enforcement).
4. **Ternary `?:`** (parser/AST/semantic/IR lowering).
5. **`switch/case/default`** (parser/AST/semantic context + IR lowering).
6. **Pointers & address-of/deref/pointer arithmetic** (largest cross-cutting change).
7. **Pass-by-reference parameters** (builds on addressing/lvalue machinery).
8. **Function overloading + name mangling + signature-aware resolution** (after type representation stabilizes).

---

## Validation checklist (repeat per step)

- **No regressions:** recursion, 1D arrays (decl/init/index/params), block scoping/shadowing, `if/else`, `for/while/do-while`, `break/continue`, arithmetic/logical ops, `cin/cout`, existing compile/run behavior.
- **Diagnostics:** clear source locations/stages for rejected const writes, bad deref/address-of targets, invalid ref bindings, ambiguous/missing overloads, non-constant/duplicate switch cases, invalid bitwise/ternary types.
- **Runtime/codegen:** correct aliasing for references, correct pointer load/store/arithmetic scaling, single-branch ternary evaluation, switch fallthrough/break behavior, unique mangled labels for overloads and correct recursion.
- **Environment note:** when executing compiled binaries in this environment, start backend with `COMPILED_BUILD_DIR` set to an allowed path (e.g. `$env:COMPILED_BUILD_DIR="C:\Users\jeffe\AppData\Local\Temp\opencode\compiled-build"`).

## Implementation progress (this session)

### In progress: Step 2 — Bitwise operators + compound-assignment lowering fix (pipeline unblocked; more validation/tests pending)

**Changed files**
- `syntax_analysis/parser.py`
  - `ASSIGN_OPS` extended with `<<=`, `>>=`, `&=`, `|=`, `^=`.
  - `PREFIX_OPS` extended with `~`.
  - Added precedence parsing levels: `logical_and -> bitwise_or -> bitwise_xor -> bitwise_and -> equality` (so infix `| ^ &` parsed with standard relative ordering below logical ops).
  - Unary parser doc/comment updated to include `~`.
- `compiler/semantic.py`
  - Unary `~` analyzed as integer-bitwise op (requires `int/char/bool`, result `int`).
  - Binary `& | ^` analyzed as integer-bitwise ops (integral operands, result `int`); `<< >>` remain integral (stream special-case preserved).
  - Compound assignments `<<= >>= &= |= ^=` validated as integer ops; other compound assignments unchanged.
- `compiler/ir.py`
  - Fixed compound assignment lowering to use `node.op[:-1]` (supports multi-char ops like `<<=`/`>>=` and bitwise compound assignments).
- `compiler/codegen.py`
  - Integer `_binary`: added `&` (`and`), `|` (`or`), `^` (`xor`).
  - Integer `_unary`: added `~` (`not`).
- `optimization/simplify.py`
  - Added constant-folding support for unary `~` and binary `& | ^` (and preserved shift folding rules).
- `optimization/unparse.py`
  - Added precedence entries for `| ^ &` and adjusted precedence constants so binary operators remain below unary/postfix/primary (avoids incorrect parenthesization/regressions).

**Verification done**
- `python -m unittest tests.test_parser` → 60 tests OK.
- `python -m unittest tests.test_parser tests.test_optimizer` → 102 tests OK (1 skipped: existing WinError 4551 execution-block case).
- Smoke `compiler.pipeline.compile_source` with bitwise/compound ops now completes compile/build stages (`ok=True`, `failed_stage=None`) in this environment; execution may still be blocked by Windows Application Control when run from disallowed paths (set `COMPILED_BUILD_DIR` to an allowed path for execution attempts).

**Remaining for Step 2**
- Add/expand targeted parser/semantic/codegen/runtime tests for bitwise ops, unary `~`, shifts in non-stream contexts, and bitwise compound assignments (including precedence/disambiguation with `<<`/`>>` streams and future `&/*` pointer syntax).
- Consider updating `compiler/constexpr.py` and any other constant-eval paths to understand `&|^~` for consistency (array sizes/global const-eval cases), if needed by supported subset behavior.

### Started: Step 3 — `const` qualifiers (partial, basic enforcement)

**Changed files**
- `syntax_analysis/ast_nodes.py`
  - Added `is_const: bool = False` to `Param`, `Declarator`, and `VarDecl`.
- `syntax_analysis/parser.py`
  - Removed `const` from `UNSUPPORTED_KEYWORDS`.
  - Added type-spec parsing support for optional leading `const` (`at_type`/`parse_type` now handle `const` prefix and propagate flag).
  - Propagated `is_const` through global/local variable declarations and function parameters.
- `compiler/semantic.py`
  - Added `is_const` tracking to `Symbol` and `ParamInfo`; propagated through globals, locals, and params.
  - Enforced initialization requirement for `const` variables (globals/locals in analyzed paths).
  - Added immutability checks: reject assignment/compound assignment, prefix/postfix `++/--`, and `cin >>` into const lvalues (including indexed elements of const arrays, via base-lvalue const detection).
- `compiler/pipeline.py`
  - Moved semantic analysis to run **before** optimization for source-level diagnostics (so optimizer transforms cannot hide semantic errors such as const violations), then re-runs semantic analysis on the optimized AST (if optimization runs) to attach annotations for IR/codegen.

**Verification done**
- `python -m unittest tests.test_parser` → 60 tests OK.
- `python -m unittest tests.test_parser tests.test_optimizer` → 102 tests OK (1 skipped).
- Manual pipeline checks:
  - `const int x=5; return x;` compiles successfully through pipeline stages.
  - `const int x=5; x=6;` now fails at semantic stage with `cannot assign to a const value`.

**Remaining for Step 3**
- Add targeted tests for const globals/locals/params, array const cases, and all rejected-mutation forms (assign, compound assign, `++/--`, `cin >>`).
- Decide/document exact const initialization rules for supported subset (e.g. array forms) and improve diagnostics/messages if needed.
- Consider optimizer awareness of const (safe propagation/folding opportunities and avoiding unnecessary transformations), though semantic correctness is now preserved by pipeline ordering.
- Integrate const into future type-model work (pointers/references/overloads/signatures).

### Started: Step 4 — Ternary conditional operator `?:` (basic end-to-end)

**Changed files**
- `syntax_analysis/ast_nodes.py`: added `Ternary(condition, when_true, when_false, ...)` expression node.
- `syntax_analysis/parser.py`: added `parse_ternary()` and integrated into `parse_assignment()` (right-associative ternary parsing).
- `compiler/semantic.py`: added ternary analysis (scalar condition, branch type compatibility/common numeric result typing).
- `compiler/ir.py`: added ternary lowering with control flow + temp (single-branch evaluation).
- `optimization/unparse.py`: added ternary printing and precedence handling; adjusted precedence constants for correct parenthesization.
- `optimization/simplify.py`: added ternary traversal/simplification/type inference support (including constant-condition branch folding).
- `optimization/utils.py`: `is_pure()` updated to treat ternary as pure when all subexpressions are pure.

**Verification done**
- `python -m unittest tests.test_parser tests.test_optimizer` → 102 tests OK (1 skipped) before latest switch/unparse follow-up edits (re-verification pending).
- Manual pipeline smoke test: `int main(){int a=5,b=3; int c=(a>b)?a:b; return c;}` compiles through pipeline (execution return code observed as 5 in this environment capture; build/compile stages ok).

**Remaining for Step 4**
- Add targeted parser/semantic/runtime tests (including side-effect branch evaluation, nested/right-associative ternary cases, and type-compatibility diagnostics).
- Extend `compiler/constexpr.py` if constant ternary evaluation is needed for constant-expression contexts in supported subset.

### In progress: Step 5 — `switch/case/default` (basic end-to-end in place)

**Changed files**
- `syntax_analysis/ast_nodes.py`: added `Switch`, `Case`, `Default` nodes.
- `syntax_analysis/parser.py`: removed `switch/case/default` from unsupported keywords; parse `switch` with `case/default` sections and statement bodies.
- `compiler/semantic.py`: validates integral switch condition, constant integer case labels, duplicate/default checks, and `break` in switch/loop contexts; analyzes case/default bodies.
- `compiler/ir.py`: lowers switch to compare/jump dispatch with fallthrough support and break-target handling separate from loop continue targets.
- `optimization/unparse.py`: added switch/case/default printing.
- `optimization/simplify.py`: traverses/simplifies switch expression, case values, and section bodies with conservative env invalidation.
- `optimization/dead_code.py`: recurses into switch case/default bodies for statement-level cleanup.
- `optimization/unused.py`: rewrites inside switch case/default bodies.
- `syntax_analysis/ast_printer.py`: flattens `cases`/`body` lists for readable AST output (with guard to avoid breaking non-list `body` nodes).

**Verification done**
- `python -m unittest tests.test_parser tests.test_optimizer` → 102 tests OK (1 skipped).
- Manual smoke compiles: ternary, switch, switch fallthrough, bitwise/compound ops, const ok case all reach compile/build stages; const mutation rejected at semantic.

**Remaining for Step 5**
- Add targeted tests for valid/invalid switch forms and runtime semantics (fallthrough, nested switch/loops, `break`/`continue` interactions).
- Extend switch/case constant-expression handling further if needed for broader subset behavior.

### Started: Type-system groundwork + pointers/references/overloading prerequisites (very early)

**Changed files**
- `syntax_analysis/ast_nodes.py`: added `pointer_depth`/`is_reference` fields to `Param` and `pointer_depth` to `Declarator`.
- `syntax_analysis/parser.py`: added declarator/parameter parsing for pointer `*` prefixes and parameter `&` references; added unary `&`/`*` operators to prefix expression parsing. Adjusted `tests/test_parser.py::TestSyntaxErrors.test_missing_operand` case to remain a true "missing operand" error with new unary `*` support.
- `compiler/semantic.py`: propagated `pointer_depth`/`is_reference` into `ParamInfo`/`Symbol`, updated type formatting (`full_type`) to include pointer/reference suffixes for groundwork (arrays/pointers/refs not fully semantically implemented yet).
- `compiler/constexpr.py`: extended constant evaluation to support unary `!`/`~`, bitwise binary ops, ternary `?:`, and safer shift handling for integer constants.

**Verification done**
- `python -m unittest tests.test_parser tests.test_optimizer` → 102 tests OK (1 skipped) after groundwork changes.

**Remaining (major)**
- [ ] Type-system groundwork/helpers (shared const/pointer/ref/array/type utilities and canonical type formatting/parsing)
- [ ] Pointers & address-of/deref/pointer arithmetic end-to-end (AST semantics, lvalue/address handling, IR load/store/address ops, codegen, optimizer/unparser)
- [ ] Pass-by-reference parameters end-to-end (semantic binding/call arg requirements, IR/codegen by-ref passing, mutation behavior)
- [ ] Function overloading/name mangling/signature-aware resolution (collect/resolve overloads, mangle labels consistently in IR/codegen/calls, preserve `main`)
- [ ] Docs/grammar updates (`syntax_analysis/README.md`, etc.) for implemented features
- [ ] Full regression + new-feature tests and UI/backend reverification

## Status

- [x] Analysis and implementation plan captured in this file
- [~] Implementation in progress (Steps 2-5 basic support present; groundwork started for pointers/references/overloading; constexpr improved)
- [ ] Tests added/updated and passing (existing suites passing; new feature coverage still needed)
- [ ] UI/backend reverified as needed

## Status

- [x] Analysis and implementation plan captured in this file
- [~] Implementation started (Step 2 bitwise mostly unblocked; Step 3 const basic enforcement in place; pipeline semantic-order adjusted)
- [ ] Tests added/updated and passing
- [ ] UI/backend reverified as needed

## Continuation notes

- When running compiled executables in this environment, set `COMPILED_BUILD_DIR` to an allowed path before starting backend/running execution paths, e.g.:
  - PowerShell: `$env:COMPILED_BUILD_DIR="C:\Users\jeffe\AppData\Local\Temp\opencode\compiled-build"`
- Backend for UI: `cd ISE-Compiler-V1 && python -m compiler.server --host 127.0.0.1 --port 8000`
- UI dev/build: `cd ISE-Compiler-V1/ui && npm run dev` / `npm run build`
- Preserve recursion, arrays, scoping, and `for/if-else` behavior while completing remaining features. Use the per-feature sections above for exact change targets.
