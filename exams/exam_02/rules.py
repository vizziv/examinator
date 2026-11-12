"""Grade rules: from field outcomes to this exam's marks."""
from examinator import Grade
from examinator.std import math

WORK = "judge the work"


def with_work(outcomes):
    """The credit for an answer that also needs work on the page: a right answer
    leaves it for a person; a wrong, blank or undecided one is `math.all_correct`'s."""
    answer = math.all_correct(outcomes)
    return Grade(None, reason=WORK) if answer == 'correct' else answer
