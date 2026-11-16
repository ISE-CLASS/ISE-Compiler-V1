"""Stage 4: input validation.

The validator rejects empty or oversized input before the lexer runs.
"""
from .validator import validate_cpp_source, validate_input

__all__ = ["validate_input", "validate_cpp_source"]
