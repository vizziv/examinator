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

# For samplers: the library's parse prompt leaves this to the exam.
FORMULAS = "Do not repair missing probability formulas."

SNOW_CONE = """\
Points are lists [x, y]. Keep arithmetic on points exactly as written, such as \
adding two lists or scaling one, and keep calls to `rotate` and `die`. """ + FORMULAS

NORMAL = """\
The printed helper `g(t)` may be called. Keep the student's constants exactly as \
written. """ + FORMULAS
