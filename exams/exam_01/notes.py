"""Per-question additions to the transcribe and parse prompts. No answer keys here."""

NUMBERS = """\
Each answer is a single number or arithmetic expression. Keep fractions and \
unsimplified arithmetic exactly as written."""

SUBSCRIPTS = """\
Answers may use subscripted quantities. Write a subscript in square brackets \
directly after its letter, keeping the subscript exactly as written: x with \
subscript k is x[k], and x with subscript k+1 is x[k+1]. Write a fraction as \
(numerator) / (denominator). The last answer is the name of a distribution: keep \
its name and any parameters as written, in the form Name(a, b)."""

POINTS = """\
Points are lists of coordinates: write [x, y] or [x, y, z]. In this course's \
pseudocode, arithmetic on points is elementwise (`p + q`, `p * q`, `c * p`, `p / c`) \
and arrays are indexed from 1. Keep such arithmetic and every index exactly as \
written: never expand it into coordinates, shift an index, or turn `+` into a \
different operation. `rotate(p, angle, center)`, `weighted_die(w)` and `die(n)` are \
available and are kept as calls. Write `x^y` as `x**y`."""

SCALAR = "Write `x^y` as `x**y`."

GIVEN_F = """\
The printed definition of `f(x)` is provided by the checker: do not include it \
unless the student changed it. `[X, Y] = g()` assigns two results."""
