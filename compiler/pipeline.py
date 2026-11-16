"""Unified compiler pipeline.

Public entry point used by the HTTP service, the command line and the tests::

    from compiler import compile_source
    result = compile_source(source, filename="main.cpp", stage="compile")

Stages (in order):
    validation -> tokenize -> parse -> optimize -> semantic -> IR -> assembly
              -> native assemble/link -> run

``stage`` selects how far to go.  ``compile`` and ``run`` execute every stage
and return every artifact, which is what the UI's main Run button uses.
Individual stage selections (tokenize, parse, semantic, optimize, ir,
assembly) stop after that stage, so a failure is reported against the stage
that produced it.
"""
import os
import re
import tempfile
import time
from pathlib import Path

from optimization import optimize as run_optimizer
from optimization.unparse import unparse
from syntax_analysis import format_ast, parse_source

from . import constexpr
from .codegen import X86Codegen
from .diagnostics import Diagnostic, as_dicts, as_text, error, info, warning
from .ir import lower as lower_ir
from .semantic import analyze as analyze_semantics
from .toolchain import (build_assembly, detect_toolchain, run_executable)

STAGES = ("validation", "tokenize", "parse", "optimize", "semantic", "ir",
          "assembly", "build", "run")
ALL_STAGES = {"compile", "run"}
_STAGE_ALIASES = {
    "all": "compile",
    "full": "compile",
    "tokens": "tokenize",
    "lex": "tokenize",
    "ast": "parse",
    "symbols": "semantic",
    "symbol-table": "semantic",
    "optimization": "optimize",
    "intermediate": "ir",
    "asm": "assembly",
    "execute": "run",
}

_ERROR_POSITION = re.compile(
    r"at line (?P<line>\d+), column (?P<col>\d+)", re.IGNORECASE)


def default_build_root():
    """Directory for generated assembly/executables.

    Prefer a project-local directory: on Windows, Application Control
    policies often block executing freshly built binaries from the system temp
    directory.  ``COMPILED_BUILD_DIR`` overrides the location.
    """
    configured = os.environ.get("COMPILED_BUILD_DIR")
    if configured:
        root = Path(configured).expanduser()
    else:
        root = Path(__file__).resolve().parent.parent / ".compiled-build"
    try:
        root.mkdir(parents=True, exist_ok=True)
        return root
    except OSError:
        return Path(tempfile.gettempdir())


def new_work_dir():
    """Return the directory used for generated build artifacts.

    Artifacts are written directly in this directory (unique file names are
    chosen by :func:`compiler.toolchain.build_assembly`) because some Windows
    Application Control policies block executing binaries created in nested
    or temporary directories.
    """
    return default_build_root()


def _normalise_stage(stage):
    value = (stage or "compile").strip().lower()
    value = _STAGE_ALIASES.get(value, value)
    if value not in set(STAGES) | ALL_STAGES:
        raise ValueError(
            "unknown stage '{}'; expected one of: {}".format(
                stage, ", ".join(sorted(set(STAGES) | ALL_STAGES))))
    return value


def _position_from_text(text):
    match = _ERROR_POSITION.search(text or "")
    if not match:
        return 0, 0
    return int(match.group("line")), int(match.group("col"))


def _token_dict(token):
    return {
        "kind": token.kind,
        "text": token.text,
        "line": token.line,
        "col": token.col,
    }


def _stats_dict(stats):
    if stats is None:
        return {}
    return {
        "total": stats.total,
        "rounds": getattr(stats, "rounds", 0),
        "counts": dict(stats.counts),
        "summary": stats.summary_lines(),
        "events": [
            {"kind": kind, "line": line, "text": text}
            for kind, line, text in stats.events
        ],
    }


class _Run:
    def __init__(self, stage, filename):
        self.stage = stage
        self.filename = filename
        self.started = time.perf_counter()
        self.diagnostics = []
        self.stages = []
        self.result = {
            "ok": True,
            "stage": stage,
            "filename": filename,
            "output": "",
            "diagnostics": [],
            "tokens": [],
            "ast": "",
            "semantic": {},
            "symbols": {},
            "ir": "",
            "optimized_source": "",
            "assembly": "",
            "toolchain": {},
            "execution": {},
        }
        self.failed_stage = None

    def mark(self, name, ok, detail="", started=None):
        duration = 0
        if started is not None:
            duration = int((time.perf_counter() - started) * 1000)
        self.stages.append({
            "name": name,
            "ok": bool(ok),
            "detail": detail,
            "duration_ms": duration,
        })

    def add(self, diagnostic):
        self.diagnostics.append(diagnostic)
        return diagnostic

    def fail(self, stage, message, line=0, col=0):
        self.add(error(stage, message, line, col))
        self.failed_stage = self.failed_stage or stage
        self.result["ok"] = False

    def partial_stage(self, stage):
        if self.stage in ALL_STAGES:
            return True
        return self.stage == stage or (
            self.stage == "semantic" and stage in ("validation", "tokenize", "parse", "optimize")
        ) or (
            self.stage == "ir" and stage in ("validation", "tokenize", "parse", "optimize", "semantic")
        ) or (
            self.stage == "assembly" and stage in ("validation", "tokenize", "parse", "optimize", "semantic", "ir")
        )

    def finish(self, output):
        self.result["diagnostics"] = as_text(self.diagnostics)
        self.result["diagnostic_details"] = as_dicts(self.diagnostics)
        self.result["stages"] = self.stages
        self.result["failed_stage"] = self.failed_stage
        self.result["ok"] = bool(self.result.get("ok")) and not self.failed_stage
        self.result["output"] = output
        self.result["duration_ms"] = int((time.perf_counter() - self.started) * 1000)
        return self.result


def _semantic_payload(semantic_result):
    return {
        "ok": semantic_result.ok,
        "symbols": semantic_result.symbol_table,
        "functions": [f.to_dict() for f in semantic_result.functions.values()],
        "globals": [s.to_dict() for s in semantic_result.globals.values()],
        "errors": [d.to_dict() for d in semantic_result.errors],
        "warnings": [d.to_dict() for d in semantic_result.warnings],
    }


def compile_source(source, filename="main.cpp", stage="compile", optimize=True,
                   stdin="", run=None, timeout=10, toolchain=None,
                   work_dir=None):
    """Run the compiler pipeline and return a JSON-friendly result dictionary.

    Parameters
    ----------
    source:     C++ source text.
    filename:   Display name used in results.
    stage:      tokenize | parse | optimize | semantic | ir | assembly |
                compile | run
    optimize:   run the Group 3 optimizer before semantic analysis (default True)
    stdin:      text fed to the compiled program for compile/run stages.
    run:        force/disable executing a built program.  Defaults to True for
                ``compile`` and ``run``.
    timeout:    native build and program timeout in seconds.
    toolchain:  optional :class:`compiler.toolchain.Toolchain` override.
    work_dir:   optional directory for generated assembly/executable.
    """
    stage = _normalise_stage(stage)
    if not isinstance(source, str):
        source = "" if source is None else str(source)
    runner = _Run(stage, filename)
    should_run = (stage in ALL_STAGES) if run is None else bool(run)

    # ---------------------------------------------------------------
    # Validation
    # ---------------------------------------------------------------
    started = time.perf_counter()
    from input_validation.validator import validate_input
    valid, validation_errors = validate_input(source)
    runner.mark("validation", valid, "; ".join(validation_errors), started)
    if not valid:
        for message in validation_errors:
            runner.add(error("validation", message, 1, 1))
        return runner.finish("Input validation failed.")

    # ---------------------------------------------------------------
    # Tokenize
    # ---------------------------------------------------------------
    started = time.perf_counter()
    try:
        from cpp_tokenizer import tokenize as lex_source
        tokens, lexical_errors = lex_source(source, keep_comments=False)
    except Exception as exc:  # defensive: never let a phase crash the service
        runner.mark("tokenize", False, str(exc), started)
        runner.fail("tokenize", "tokenizer failed: {}".format(exc), 1, 1)
        return runner.finish("Tokenizer failed.")
    runner.result["tokens"] = [_token_dict(token) for token in tokens]
    runner.mark("tokenize", not lexical_errors,
                "{} tokens".format(len(tokens)), started)
    for message in lexical_errors:
        line, col = _position_from_text(message)
        runner.add(error("tokenize", message, line, col))
    if lexical_errors:
        runner.failed_stage = "tokenize"
        return runner.finish("Tokenization failed with {} error(s).".format(len(lexical_errors)))
    if stage == "tokenize":
        return runner.finish("Tokenized {} tokens.".format(len(tokens)))

    # ---------------------------------------------------------------
    # Parse
    # ---------------------------------------------------------------
    started = time.perf_counter()
    try:
        parsed = parse_source(source)
    except Exception as exc:
        runner.mark("parse", False, str(exc), started)
        runner.fail("parse", "parser failed: {}".format(exc), 1, 1)
        return runner.finish("Parser failed.")
    if parsed.ast is not None:
        runner.result["ast"] = format_ast(parsed.ast, ascii_only=True)
    runner.mark("parse", parsed.ok,
                "{} top-level item(s)".format(
                    len(parsed.ast.items) if parsed.ast is not None else 0), started)
    for message in parsed.lexical_errors:
        line, col = _position_from_text(message)
        runner.add(error("parse", message, line, col))
    for syntax_error in parsed.syntax_errors:
        runner.add(error("parse", syntax_error.message,
                         getattr(syntax_error, "line", 0),
                         getattr(syntax_error, "col", 0)))
    if not parsed.ok:
        runner.failed_stage = "parse"
        count = len(parsed.lexical_errors) + len(parsed.syntax_errors)
        return runner.finish("Parsing failed with {} error(s).".format(count))
    if stage == "parse":
        return runner.finish("Parsed {} top-level item(s).".format(len(parsed.ast.items)))

    program = parsed.ast
    optimization_stats = None

    # ---------------------------------------------------------------
    # Semantic analysis (source-level diagnostics must reflect user intent)
    # ---------------------------------------------------------------
    # Analyze before optimization so const/semantic violations are not hidden by
    # transformations. Optimization clones the AST; if continuing past semantic
    # we re-run semantic analysis on the optimized tree to attach annotations
    # needed by IR/codegen.
    started = time.perf_counter()
    try:
        semantic_result = analyze_semantics(program)
    except Exception as exc:
        runner.mark("semantic", False, str(exc), started)
        runner.fail("semantic", "semantic analysis failed: {}".format(exc), 1, 1)
        return runner.finish("Semantic analysis failed.")
    runner.result["semantic"] = _semantic_payload(semantic_result)
    runner.result["symbols"] = semantic_result.symbol_table
    runner.mark("semantic", semantic_result.ok,
                "{} function(s), {} global(s), {} scope(s)".format(
                    len(semantic_result.functions), len(semantic_result.globals),
                    len(semantic_result.symbol_table.get("scopes", []))), started)
    for diagnostic in semantic_result.errors:
        runner.add(diagnostic)
    for diagnostic in semantic_result.warnings:
        runner.add(diagnostic)
    if not semantic_result.ok:
        runner.failed_stage = "semantic"
        return runner.finish("Semantic analysis failed with {} error(s).".format(
            len(semantic_result.errors)))
    if stage == "semantic":
        return runner.finish(
            "Semantic analysis passed: {} function(s), {} global variable(s).".format(
                len(semantic_result.functions), len(semantic_result.globals)))

    # ---------------------------------------------------------------
    # Optimize (syntactic; runs after semantic validation)
    # ---------------------------------------------------------------
    if optimize:
        started = time.perf_counter()
        try:
            optimized = run_optimizer(program)
            program = optimized.ast
            optimization_stats = optimized.stats
            optimization_stats.rounds = optimized.rounds
            runner.result["optimized_source"] = unparse(program)
            runner.result["optimized_ast"] = format_ast(program, ascii_only=True)
            runner.result["optimization"] = _stats_dict(optimization_stats)
            runner.result["optimization"]["nodes_before"] = optimized.nodes_before
            runner.result["optimization"]["nodes_after"] = optimized.nodes_after
            runner.mark("optimize", True,
                        "{} change(s), {} round(s)".format(
                            optimization_stats.total, optimized.rounds), started)
            # Re-annotate optimized AST for IR/codegen.
            semantic_result = analyze_semantics(program)
        except Exception as exc:
            runner.mark("optimize", False, str(exc), started)
            runner.fail("optimize", "optimizer failed: {}".format(exc), 1, 1)
            return runner.finish("Optimization failed.")
        if stage == "optimize":
            detail = "\n".join(optimization_stats.summary_lines())
            return runner.finish(
                "Optimized program ({} change(s), {} round(s)).\n{}".format(
                    optimization_stats.total, optimized.rounds, detail))
    else:
        runner.mark("optimize", True, "disabled", started)
        runner.result["optimization"] = {"total": 0, "disabled": True}
        if stage == "optimize":
            return runner.finish("Optimization is disabled.")

    # ---------------------------------------------------------------
    # Intermediate representation
    # ---------------------------------------------------------------
    started = time.perf_counter()
    try:
        ir_program = lower_ir(program)
        runner.result["ir"] = ir_program.text()
        runner.result["ir_data"] = ir_program.to_dict()
        runner.mark("ir", True, "{} function(s)".format(
            len(ir_program.functions)), started)
    except Exception as exc:
        runner.mark("ir", False, str(exc), started)
        runner.fail("ir", "IR generation failed: {}".format(exc), 1, 1)
        return runner.finish("IR generation failed.")
    if stage == "ir":
        return runner.finish("Generated IR for {} function(s).".format(
            len(ir_program.functions)))

    # ---------------------------------------------------------------
    # x86-64 assembly
    # ---------------------------------------------------------------
    started = time.perf_counter()
    try:
        assembly = X86Codegen(program, ir_program, semantic_result).generate()
        runner.result["assembly"] = assembly
        runner.mark("assembly", True, "{} line(s)".format(
            assembly.count("\n")), started)
    except Exception as exc:
        runner.mark("assembly", False, str(exc), started)
        runner.fail("assembly", "code generation failed: {}".format(exc), 1, 1)
        return runner.finish("Code generation failed.")
    if stage == "assembly":
        return runner.finish("Generated x86-64 assembly ({} lines).".format(
            assembly.count("\n")))

    # ---------------------------------------------------------------
    # Native build (optional) and execution
    # ---------------------------------------------------------------
    toolchain = toolchain or detect_toolchain()
    runner.result["toolchain"] = toolchain.to_dict() if toolchain else {
        "name": None, "supports_assembly": False,
    }
    started = time.perf_counter()
    build = build_assembly(assembly, toolchain, out_dir=work_dir or new_work_dir(),
                           timeout=timeout)
    runner.result["build"] = {k: v for k, v in build.items() if k != "assembly_path"}
    if build["ok"]:
        runner.mark("build", True, "linked with {}".format(toolchain.name), started)
    else:
        runner.mark("build", False, build["log"], started)
        runner.add(warning("build", build["log"] or "native build unavailable"))
        # Assembly was generated correctly; a missing host toolchain is not a
        # compiler failure, so keep ok=True and surface it as a warning.
        if not build["log"]:
            runner.fail("build", "native build failed", 1, 1)

    execution = {}
    if build["ok"] and should_run:
        started = time.perf_counter()
        execution = run_executable(build["executable"], stdin, timeout=timeout)
        runner.result["execution"] = execution
        runner.mark("run", execution["ok"], execution["log"], started)
        if execution["log"]:
            runner.add(warning("run", execution["log"]))

    stdout = execution.get("stdout", "") if execution else ""
    stderr = execution.get("stderr", "") if execution else ""
    if build["ok"] and should_run:
        if stdout or stderr:
            output = stdout
            if stderr:
                output = (output + "\n" + stderr).strip()
        else:
            output = "(program produced no output; exit code {})".format(
                execution.get("returncode"))
    elif build["ok"]:
        output = "Compiled successfully to native code with {}.".format(toolchain.name)
    else:
        output = ("Generated x86-64 assembly, but the native build step is "
                  "unavailable:\n{}".format(build["log"]))
    return runner.finish(output)


def compile_stage(source, filename="main.cpp", stage="tokenize", **kwargs):
    """Convenience wrapper used by the HTTP service and tests."""
    return compile_source(source, filename=filename, stage=stage, **kwargs)
