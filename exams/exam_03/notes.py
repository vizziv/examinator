"""Per-question additions to the transcribe and parse prompts. No answer keys here."""

RECTANGLE = """\
Answer 1.1 is a set written as a product of two intervals. Write it as \
[lower, upper] x [lower, upper], keeping the order of the two intervals and every \
bound exactly as written."""

# For samplers: the library's parse prompt leaves this to the exam.
FORMULAS = "Do not repair missing probability formulas."

KEEP_CONSTANTS = """\
Preserve every numeric constant exactly as written, including enormous powers and \
tiny reciprocals. Never evaluate, clip or replace them, and do not substitute \
`huge_constant` yourself. `huge_constant` is a name the exam allows: where the \
student wrote it, keep it, with no review marker for it. """ + FORMULAS

# Question 5's one box has no printed label, and its printed options are numbers
# (one of them equal to the label).
ONE_BOX = """\
The page has one answer box, labelled 5 here: the printed numbers in it are its \
options, not answer labels. The answer is the option the student marked or wrote."""

POINT_PROCESS = """\
Preserve collections and multiplicities. `poisson_dist(m)` is a permitted primitive \
inside `poisson_process()`, separate from the student's own `poisson_dist`, which \
must be kept as written for the second part. """ + FORMULAS
