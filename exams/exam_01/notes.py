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
Points are lists of coordinates: write [x, y] or [x, y, z]. Keep arithmetic on \
points exactly as written: never expand it into coordinates or turn `+` into a \
different operation. Keep calls to `rotate`, `weighted_die` and `die`. In this \
course arrays are indexed from 1, but here indexing is from 0: write every index \
one less than the student did (x[1] becomes x[0], x[k] becomes x[k - 1]). Write \
`x^y` as `x**y`."""

SCALAR = "Write `x^y` as `x**y`."

# For every sampler's parse: the library's parse prompt leaves this to the exam.
FORMULAS = "Do not repair missing probability formulas."

GIVEN_F = """\
The printed definition of `f(x)` is provided by the checker: do not include it \
unless the student changed it. `[X, Y] = g()` assigns two results."""
