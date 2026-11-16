"""COMPILED compiler core.

Pipeline:
    source -> validation -> lexer -> parser/AST -> semantic analysis
           -> optimizer -> IR -> x86-64 assembly -> optional toolchain check

The public entry point is :func:`compiler.pipeline.compile_source`.
"""


def compile_source(*args, **kwargs):
    from .pipeline import compile_source as _compile_source
    return _compile_source(*args, **kwargs)


def compile_stage(*args, **kwargs):
    from .pipeline import compile_stage as _compile_stage
    return _compile_stage(*args, **kwargs)


__all__ = ["compile_source", "compile_stage"]
