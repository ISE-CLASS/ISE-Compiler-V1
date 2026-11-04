"""Bookkeeping for the optimizer: what was changed, and how often."""
from collections import OrderedDict

# kind -> human readable name (also the order used in reports)
KINDS = OrderedDict([
    ("constant_propagation", "Constant propagation"),
    ("constant_folding", "Constant folding"),
    ("algebraic", "Algebraic simplification"),
    ("strength_reduction", "Strength reduction"),
    ("dead_branch", "Constant branch elimination"),
    ("dead_loop", "Dead loop elimination"),
    ("unreachable", "Unreachable code removal"),
    ("dead_expression", "Useless statement removal"),
    ("unused_variable", "Unused variable removal"),
])


class Stats:
    def __init__(self):
        self.counts = OrderedDict((k, 0) for k in KINDS)
        self.events = []          # (kind, line, text)

    def record(self, kind, line, text):
        self.counts[kind] += 1
        self.events.append((kind, line, text))

    @property
    def total(self):
        return sum(self.counts.values())

    def summary_lines(self):
        lines = []
        for kind, label in KINDS.items():
            n = self.counts[kind]
            if n:
                lines.append("  {:<32} {}".format(label, n))
        return lines or ["  (nothing to optimize)"]

    def event_lines(self):
        lines = []
        for kind, line, text in self.events:
            where = "line {}".format(line) if line else "      "
            lines.append("  [{:<20}] {:>7}: {}".format(kind, where, text))
        return lines