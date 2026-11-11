"""Per-question additions to the transcribe and parse prompts. No answer keys here."""

RECTANGLE = """\
Answer 1.1 is a set written as a product of two intervals. Write it as \
[lower, upper] x [lower, upper], keeping the order of the two intervals and every \
bound exactly as written."""

KEEP_CONSTANTS = """\
Preserve every numeric constant exactly as written, including enormous powers and \
tiny reciprocals. Never evaluate, clip or replace them, and do not substitute \
`huge_constant` yourself."""

POINT_PROCESS = """\
Preserve collections and multiplicities. `poisson_dist(m)` is a permitted primitive \
inside `poisson_process()`, separate from the student's own `poisson_dist`, which \
must be kept as written for the second part."""
