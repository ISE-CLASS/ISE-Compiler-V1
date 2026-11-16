"""Shared diagnostic type used by every compiler stage.

A diagnostic is deliberately small and JSON friendly so the UI, the HTTP
service and the command line can all render the same information.
"""
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Diagnostic:
    stage: str
    severity: str          # "error", "warning" or "info"
    message: str
    line: int = 0
    col: int = 0

    def to_dict(self):
        return asdict(self)

    def format(self):
        where = ""
        if self.line:
            where = " at line {}".format(self.line)
            if self.col:
                where += ", column {}".format(self.col)
        return "{} {}: {}{}".format(
            self.stage.capitalize(),
            self.severity,
            self.message,
            where,
        )


def error(stage, message, line=0, col=0):
    return Diagnostic(stage, "error", message, line, col)


def warning(stage, message, line=0, col=0):
    return Diagnostic(stage, "warning", message, line, col)


def info(stage, message, line=0, col=0):
    return Diagnostic(stage, "info", message, line, col)


def as_dicts(diagnostics):
    return [d.to_dict() if isinstance(d, Diagnostic) else d for d in diagnostics]


def as_text(diagnostics):
    result = []
    for d in diagnostics:
        if isinstance(d, Diagnostic):
            result.append(d.format())
        else:
            result.append(str(d))
    return result


def first_error(diagnostics) -> Optional[Diagnostic]:
    for d in diagnostics:
        if isinstance(d, Diagnostic) and d.severity == "error":
            return d
    return None
