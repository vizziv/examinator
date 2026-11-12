"""A kit for grading samplers written in pseudocode. Optional: not part of the core.

- `primitives`: random builtins to compose into a `pseudocode.Language`
  (`unif`, `coin(p)`, `die(n)` from a chosen start).
- `fit`: distribution-closeness tests (support, KS, chi-square, projected KS),
  each returning a `Fit` with a plot for graders.
- `checks`: drawing samples from a parsed program, deciding which run-time
  failures are undecided, collecting per-part verdicts and turning them into
  a `Result` (plots first, a compact verdict table, details collapsed).

It depends on the core only for types and mechanisms: `pseudocode` (Primitive,
Program, the exceptions), `std.blocks` and `Result`. Nothing here knows about
a particular exam; policy (seeds, sample sizes, targets, what counts as
accept-reject) belongs to the exam package.
"""
