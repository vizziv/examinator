"""Per-question additions to the transcribe and parse prompts. No answer keys here."""

PLACEHOLDERS = """\
Each answer fills a placeholder in printed code. An answer may be a number, a list \
of numbers, or the word None. Write a list as [x, y], keeping the order and every \
number exactly as written, and keep None as the text None (it is an answer, not a \
blank)."""

EXPRESSIONS = """\
Each answer fills a placeholder in printed code with an expression in u, v or x. \
Keep every expression exactly as written."""

TRUE_FALSE = "Each answer is one filled bubble, True or False. Write that word."

POWERS = "Each answer is one filled bubble beside a power of ten. Write it as 10^k, with the printed exponent."

OPTIONS = """\
Each answer is one filled bubble beside a printed option. Write the selected \
option's printed text in plain ASCII, using <=, >= and the word delta."""

BOXED = "Only the boxed final answers are answers. Work elsewhere on the page is not transcribed."

SNOW_CONE = """\
Points are lists [x, y]. Keep arithmetic on points exactly as written, such as \
adding two lists or scaling one. `rotate(point, angle, center)` and `die(n)` \
(a whole number from 0 to n - 1) are provided helpers: keep calls to them."""

NORMAL = """\
The printed helper `g(t)` may be called. Keep the student's constants exactly as \
written. `die(n)` is a whole number from 0 to n - 1."""
